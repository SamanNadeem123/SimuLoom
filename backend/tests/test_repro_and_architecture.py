import ast
import hashlib
import subprocess
import sys
from pathlib import Path

from simuloom.domain.policies.registry import available_policies, create_policy
from simuloom.domain.simulation.engine import EngineParams, simulate
from simuloom.domain.workload.generator import generate_workload
from tests.helpers import base_config

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "simuloom"


def digest(seed: int = 42, rep: int = 0) -> str:
    cfg = base_config(counters=4)  # abandonment on, 3 groups
    w = generate_workload(cfg, seed, rep)
    r = simulate(w, EngineParams(cfg.counters, cfg.horizon_minutes, cfg.queue_capacity),
                 create_policy("fcfs"), record_trace=True)  # fmt: skip
    return hashlib.sha256(repr((r.outcomes, r.steps, r.trace, r.workload_fingerprint)).encode()).hexdigest()


def test_end_to_end_repeat_and_fresh_process():
    assert digest() == digest()
    assert digest() != digest(rep=1)
    code = "from tests.test_repro_and_architecture import digest; print(digest())"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
        env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
    ).stdout.strip()  # fmt: skip
    assert out == digest()


def test_end_to_end_run_is_meaningful():
    cfg = base_config(counters=4)
    w = generate_workload(cfg, 42, 0)
    r = simulate(w, EngineParams(4, cfg.horizon_minutes), create_policy("fcfs"))
    statuses = {o.status.value for o in r.outcomes}
    assert len(r.outcomes) == len(w.visitors) > 300
    assert "COMPLETED" in statuses and max(s.busy_counters for s in r.steps) == 4
    assert r.workload_fingerprint == w.fingerprint and r.policy_key == "fcfs"


def test_registry():
    assert available_policies() == ["aging_priority", "fcfs", "simuloom_fair", "static_priority"]
    import pytest

    with pytest.raises(KeyError):
        create_policy("nope")
    with pytest.raises(ValueError):
        create_policy("fcfs", {"x": 1})


def test_domain_is_framework_free():
    banned = {"fastapi", "starlette", "sqlalchemy", "alembic", "uvicorn", "psycopg"}
    for f in (SRC / "domain").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            assert not {m.split(".")[0] for m in mods} & banned, f


def test_policies_and_config_do_not_import_simpy_or_numpy_rng():
    for f in (SRC / "domain" / "policies").rglob("*.py"):
        text = f.read_text()
        assert "simpy" not in text and "numpy" not in text and "random" not in text, f
