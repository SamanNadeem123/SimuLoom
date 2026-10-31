"""Experiment runner: equivalent workloads across policies, aggregation, determinism, validation."""

import pytest

from simuloom.services.runner import PolicySpec, aggregate_experiment, run_replication, validate_specs
from tests.helpers import base_config

SPECS = [
    PolicySpec("fcfs", "fcfs"),
    PolicySpec("fair", "simuloom_fair"),
    PolicySpec("aging", "aging_priority", {"aging_rate_per_minute": 0.05}),
]


def small_cfg():
    return base_config(horizon_minutes=240, warmup_minutes=30)


def test_policies_share_one_workload_and_detail_is_kept_for_requested_replication():
    cfg = small_cfg()
    r = run_replication(cfg, 11, 0, SPECS, keep_detail=True)
    assert set(r.results) == {"fcfs", "fair", "aging"} and r.visitor_count > 0
    assert all(len(p.outcomes or ()) == r.visitor_count for p in r.results.values())
    assert all(p.series and p.series[0][0] == 0.0 for p in r.results.values())
    assert run_replication(cfg, 11, 1, SPECS).results["fcfs"].outcomes is None
    assert r.results["fair"].resolution_notes  # parameter-resolution notes recorded for the fair policy


def test_deterministic_and_replications_differ():
    cfg = small_cfg()
    a, b = run_replication(cfg, 5, 2, SPECS), run_replication(cfg, 5, 2, SPECS)
    assert a.workload_fingerprint == b.workload_fingerprint
    assert a.results["fair"].metrics.overall == b.results["fair"].metrics.overall
    assert run_replication(cfg, 5, 3, SPECS).workload_fingerprint != a.workload_fingerprint
    assert run_replication(cfg, 6, 2, SPECS).workload_fingerprint != a.workload_fingerprint


def test_aggregate_experiment_end_to_end():
    cfg = small_cfg()
    reps = [run_replication(cfg, 3, i, SPECS) for i in range(6)]
    agg = aggregate_experiment(cfg, SPECS, reps)
    assert agg.baseline_label == "fcfs" and agg.replications == 6 and set(agg.policies) == {"fcfs", "fair", "aging"}
    fcfs = {(m.metric_key, m.group_key): m for m in agg.policies["fcfs"].metrics}
    assert fcfs[("wait_mean", "__all__")].n_valid == 6 and fcfs[("wait_mean", "__all__")].ci_low is not None
    assert ("wait_mean", "international") in fcfs
    assert {c for c, _ in agg.comparisons} == {"fair", "aging"}
    assert agg.policies["fair"].constraint_label in {
        "VIOLATES_AT_LEAST_ONE", "INCOMPLETE", "SATISFIED_INCONCLUSIVE", "SATISFIED_CONFIRMED"}  # fmt: skip
    assert {c.constraint_id for c in agg.policies["fcfs"].constraints} == {"c1", "c2", "c3"}
    assert agg.experiment_label in {"NOT_SATISFIED_BY_ANY_TESTED_POLICY", "AT_LEAST_ONE_POLICY_NOT_REFUTED"}
    assert any("heuristic" in w for w in agg.warnings)


def test_too_few_replications_warns_and_not_evaluable():
    cfg = base_config(horizon_minutes=240, warmup_minutes=30, min_sample={"group_n": 5, "p95_n": 5})
    agg = aggregate_experiment(cfg, SPECS[:1], [run_replication(cfg, 3, i, SPECS[:1]) for i in range(2)])
    assert agg.warnings and agg.policies["fcfs"].constraint_label in {"INCOMPLETE", "VIOLATES_AT_LEAST_ONE"}
    ind = next(c for c in agg.policies["fcfs"].constraints if c.constraint_id == "c1")
    assert ind.reason_code == "TOO_FEW_REPLICATIONS"


def test_validation_errors():
    cfg = small_cfg()
    for bad in (
        [],
        [PolicySpec("a", "fcfs"), PolicySpec("a", "static_priority")],
        [PolicySpec("a", "nope")],
        [PolicySpec("a", "aging_priority")],
        [PolicySpec("a", "fcfs", {"x": 1})],
        [PolicySpec(f"p{i}", "fcfs") for i in range(7)],
        [PolicySpec("", "fcfs")],
    ):
        with pytest.raises(ValueError):
            validate_specs(bad, cfg)
    validate_specs(SPECS, cfg)
    with pytest.raises(ValueError):
        aggregate_experiment(cfg, SPECS, [])
    with pytest.raises(ValueError):
        aggregate_experiment(cfg, SPECS, [run_replication(cfg, 1, 0, SPECS)], "zzz")
