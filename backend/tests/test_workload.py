import math
import subprocess
import sys
from pathlib import Path

import numpy as np

from simuloom.domain.workload.generator import generate_workload
from tests.helpers import base_config

ROOT = Path(__file__).resolve().parent.parent


def test_deterministic_and_distinct_by_seed_and_replication():
    c = base_config()
    a, b = generate_workload(c, 42, 0), generate_workload(c, 42, 0)
    assert a == b and a.fingerprint == b.fingerprint
    assert generate_workload(c, 42, 1).fingerprint != a.fingerprint
    assert generate_workload(c, 43, 0).fingerprint != a.fingerprint


def test_structure_and_bounds():
    c = base_config()
    w = generate_workload(c, 7, 3)
    arr = [v.arrival_time for v in w.visitors]
    assert arr == sorted(arr) and all(0 <= t < c.horizon_minutes for t in arr)
    assert [v.visitor_id for v in w.visitors] == list(range(len(arr)))
    assert all(v.service_minutes > 0 and v.patience_minutes > 0 for v in w.visitors)
    assert {v.group_key for v in w.visitors} <= {"undergrad", "postgrad", "international"}
    assert not any(v.no_show for v in w.visitors)  # disabled


def test_does_not_use_global_rng():
    c = base_config()
    np.random.seed(1)
    a = generate_workload(c, 5, 0).fingerprint
    np.random.seed(999)
    import random

    random.seed(3)
    assert generate_workload(c, 5, 0).fingerprint == a


def test_streams_are_independent_of_optional_features():
    on = base_config()
    off = base_config(abandonment={"enabled": False})
    ns = base_config(no_show={"enabled": True, "probability": 0.2})
    a, b, d = (generate_workload(x, 11, 2) for x in (on, off, ns))
    key = lambda w: [(v.arrival_time, v.group_key, v.service_minutes) for v in w.visitors]  # noqa: E731
    assert key(a) == key(b) == key(d)  # arrivals, groups and service unaffected
    assert all(math.isinf(v.patience_minutes) for v in b.visitors)
    assert 0.1 < sum(v.no_show for v in d.visitors) / len(d.visitors) < 0.3


def test_sample_statistics_are_plausible():
    c = base_config(horizon_minutes=1440, warmup_minutes=0, counters=50)  # ~1296 visitors
    w = generate_workload(c, 2026, 0)
    n = len(w.visitors)
    assert abs(n - 0.9 * 1440) < 5 * math.sqrt(0.9 * 1440)  # Poisson count within 5 sd
    share = sum(v.group_key == "undergrad" for v in w.visitors) / n
    assert abs(share - 0.55) < 0.06
    mean_int = np.mean([v.service_minutes for v in w.visitors if v.group_key == "international"])
    assert 7.0 < mean_int < 11.5  # nominal 9.0


def test_renewal_and_deterministic_arrivals():
    c = base_config(arrival={"process": "renewal", "interarrival": {"kind": "deterministic", "value_minutes": 10}})
    w = generate_workload(c, 1, 0)
    assert [v.arrival_time for v in w.visitors][:3] == [10.0, 20.0, 30.0] and len(w.visitors) == 47


def test_cross_process_reproducibility():
    code = (
        "from tests.helpers import base_config;"
        "from simuloom.domain.workload.generator import generate_workload;"
        "print(generate_workload(base_config(),42,0).fingerprint)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
    ).stdout.strip()
    assert out == generate_workload(base_config(), 42, 0).fingerprint
