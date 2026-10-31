"""Metrics engine tests: hand-calculated fixtures (spec 7.7), censoring, warm-up, properties."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from simuloom.domain.metrics.compute import compute_run_metrics, metric_rows
from simuloom.domain.metrics.stats import percentile, t_interval
from simuloom.domain.metrics.types import ALL_GROUPS, MetricValue
from simuloom.domain.policies.fcfs import FcfsPolicy
from simuloom.domain.simulation.engine import EngineParams, simulate
from tests.helpers import scripted_g, two_group_config

INF = math.inf
SPEC_ROWS = [(0, 4, "x"), (1, 3, "y"), (2, 2, "x"), (3, 5, "y"), (10, 1, "x")]  # spec 7.7


def run_metrics(rows, cfg=None, noshow=None, c=1, cap=None):
    cfg = cfg or two_group_config(counters=c, queue_capacity=cap)
    w = scripted_g(rows, noshow)
    rec = simulate(w, EngineParams(cfg.counters, cfg.horizon_minutes, cfg.queue_capacity), FcfsPolicy())
    return compute_run_metrics(rec, cfg), rec


def val(m: MetricValue) -> float:
    assert m.evaluable and m.value is not None
    return m.value


def test_spec_7_7_worked_example_overall():
    rm, _ = run_metrics(SPEC_ROWS)
    o = rm.overall
    assert val(o["wait_mean"]) == pytest.approx(3.6)
    assert val(o["wait_median"]) == pytest.approx(4.0)
    assert val(o["wait_p95"]) == pytest.approx(5.8)  # type-7 percentile of [0,3,4,5,6]
    assert val(o["throughput_per_hour"]) == pytest.approx(15.0)
    assert val(o["utilization"]) == pytest.approx(0.75)
    assert val(o["queue_length_mean"]) == pytest.approx(0.9)
    assert val(o["queue_length_peak"]) == 3
    assert val(o["completed_count"]) == 5 and val(o["eligible_count"]) == 5
    assert val(o["max_individual_wait"]) == pytest.approx(6.0)
    assert o["wait_mean"].n == 5 and o["wait_p95"].n == 5
    assert rm.warnings == ()


def test_spec_7_7_worked_example_groups():
    rm, _ = run_metrics(SPEC_ROWS)
    x, y = rm.groups["x"], rm.groups["y"]
    assert val(x["wait_mean"]) == pytest.approx(3.0) and val(y["wait_mean"]) == pytest.approx(4.5)
    assert val(x["wait_median"]) == pytest.approx(4.0) and val(y["wait_median"]) == pytest.approx(4.5)
    assert val(x["sla_attainment"]) == pytest.approx(1 / 3) and val(y["sla_attainment"]) == pytest.approx(0.5)
    assert val(x["long_wait_rate"]) == pytest.approx(2 / 3) and val(y["long_wait_rate"]) == pytest.approx(0.5)
    assert x["wait_mean"].n == 3 and y["wait_mean"].n == 2


def test_default_thresholds_make_small_groups_not_evaluable():
    cfg = two_group_config(min_sample={"group_n": 30, "p95_n": 100})
    rm, _ = run_metrics(SPEC_ROWS, cfg)
    assert val(rm.overall["wait_mean"]) == pytest.approx(3.6)  # overall needs n >= 1 only
    assert rm.overall["wait_p95"].status.value == "NOT_EVALUABLE"
    assert rm.overall["wait_p95"].reason == "INSUFFICIENT_SAMPLE" and rm.overall["wait_p95"].value is None
    for g in ("x", "y"):
        for k in ("wait_mean", "wait_median", "sla_attainment", "long_wait_rate"):
            assert rm.groups[g][k].reason == "INSUFFICIENT_SAMPLE"


def test_empty_group_is_not_evaluable_never_zero():
    rm, _ = run_metrics([(0, 1, "x"), (1, 1, "x")])
    assert rm.groups["y"]["wait_mean"].reason == "NO_OBSERVATIONS"
    assert rm.groups["y"]["wait_mean"].value is None
    assert rm.groups["y"]["sla_attainment"].value is None


def test_empty_run():
    rm, _ = run_metrics([])
    assert rm.overall["wait_mean"].reason == "NO_OBSERVATIONS"
    assert val(rm.overall["throughput_per_hour"]) == 0 and val(rm.overall["utilization"]) == 0
    assert rm.individual_max_wait.reason == "NO_OBSERVATIONS"


def test_censoring_abandonment_blocking_and_noshow_hand_computed():
    """c=1, H=20, theta=3, capacity 1, abandonment enabled. Visitors (A,S,group,patience):
    0:(0,10,x) served 0-10 W=0. 1:(1,1,x,patience 2) abandons at 3 (elapsed 2).
    2:(2,1,y) joins queue (queue empty after v1 left? v1 still waiting until 3 -> pool has v1 so v2 blocked at t=2)
    """
    cfg = two_group_config(
        queue_capacity=1,
        abandonment={"enabled": True, "patience": {"kind": "deterministic", "value_minutes": 5}},
    )
    rows = [
        (0, 10, "x"),  # 0: served immediately, completes at 10
        (1, 1, "x", 2),  # 1: joins queue, abandons at 3 (elapsed 2)
        (2, 1, "y"),  # 2: queue holds v1 (cap 1) -> BLOCKED
        (3, 1, "y"),  # 3: v1 left at 3 -> joins queue, served at 10 (W=7 > theta)
        (4, 1, "x"),  # 4: queue holds v3 -> BLOCKED
        (15, 1, "y"),  # 5: served at 15 (W=0)
        (19, 5, "x"),  # 6: served at 19, in service at horizon
    ]
    rm, rec = run_metrics(rows, cfg, noshow={4})
    st = {o.visitor_id: o.status.value for o in rec.outcomes}
    assert st == {0: "COMPLETED", 1: "ABANDONED", 2: "BLOCKED", 3: "COMPLETED", 4: "NO_SHOW",
                  5: "COMPLETED", 6: "IN_SERVICE_AT_HORIZON"}  # fmt: skip
    c = rm.counts[ALL_GROUPS]
    assert c["eligible"] == 6 and c["abandoned"] == 1 and c["blocked"] == 1 and c["started"] == 4
    assert c["in_service_at_horizon"] == 1 and c["waiting_at_horizon"] == 0
    o = rm.overall
    assert val(o["abandonment_rate"]) == pytest.approx(1 / 5)  # denominator: eligible minus blocked
    assert o["abandonment_rate"].n == 5
    assert val(o["blocking_rate"]) == pytest.approx(1 / 6)
    # SLA: successes v0,v5,v6 (W=0); failures: v1 abandoned, v2 blocked, v3 (W=7) -> 3/6
    assert val(o["sla_attainment"]) == pytest.approx(0.5) and o["sla_attainment"].n == 6
    # long-wait denominator excludes blocked (E_det=5): waits 0,2(abandoned),7,0,0 -> >3: only v3
    assert val(o["long_wait_rate"]) == pytest.approx(1 / 5) and o["long_wait_rate"].n == 5
    assert val(o["wait_mean"]) == pytest.approx((0 + 7 + 0 + 0) / 4)
    assert val(o["wait_mean_incl_censored"]) == pytest.approx((0 + 2 + 7 + 0 + 0) / 5)
    assert val(o["max_individual_wait"]) == pytest.approx(7.0)
    assert val(o["completed_count"]) == 3  # v0,v3,v5 complete within H; v6 completes at 24 > 20


def test_waiting_at_horizon_is_censored_and_undetermined_handling():
    # c=1: v0 occupies 0..30. v1 arrives 5 (elapsed 15 at H=20 > theta -> failure), v2 arrives 18 (elapsed 2 <= 3 -> U)
    cfg = two_group_config(min_sample={"group_n": 2, "p95_n": 2}, statistics={"max_censored_fraction": 1.0})
    rm, rec = run_metrics([(0, 30, "x"), (5, 1, "y"), (18, 1, "y")], cfg)
    c = rm.counts[ALL_GROUPS]
    assert c["waiting_at_horizon"] == 2 and c["undetermined"] == 1 and c["in_service_at_horizon"] == 1
    o = rm.overall
    assert val(o["sla_attainment"]) == pytest.approx(1 / 2) and o["sla_attainment"].n == 2  # v0 ok, v1 failed
    assert val(o["long_wait_rate"]) == pytest.approx(1 / 2) and o["long_wait_rate"].n == 2
    assert val(o["wait_mean"]) == pytest.approx(0.0) and o["wait_mean"].n == 1
    assert val(o["wait_mean_incl_censored"]) == pytest.approx((0 + 15 + 2) / 3)
    assert val(o["max_individual_wait"]) == pytest.approx(15.0)
    assert len(rm.warnings) == 2 and "censored" in rm.warnings[0] and "undetermined" in rm.warnings[1]


def test_excessive_censoring_rule_blocks_group_metrics_only():
    cfg = two_group_config(min_sample={"group_n": 2, "p95_n": 2}, statistics={"max_censored_fraction": 0.10})
    rows = [(0, 30, "x"), (1, 1, "y"), (2, 1, "y"), (3, 1, "y")]  # all of y waits at horizon
    rm, _ = run_metrics(rows, cfg)
    y = rm.groups["y"]
    assert y["sla_attainment"].reason == "EXCESSIVE_CENSORING" and y["long_wait_rate"].reason == "EXCESSIVE_CENSORING"
    assert y["wait_mean"].reason == "NO_OBSERVATIONS"  # nobody in y ever started
    assert val(y["wait_mean_incl_censored"]) == pytest.approx((19 + 18 + 17) / 3)  # no CEN rule for this variant
    assert val(rm.overall["sla_attainment"]) is not None  # overall is not subject to the group CEN rule


def test_warmup_boundary():
    """Visitors arriving before t_w are simulated but excluded from visitor-level metrics; counts and
    time-weighted metrics are measured over [t_w, H]."""
    cfg = two_group_config(warmup_minutes=5)
    rows = [(0, 8, "x"), (4, 1, "x"), (5, 1, "y"), (6, 1, "y")]  # v0 serves 0-8; v1 8-9; v2 9-10; v3 10-11
    rm, rec = run_metrics(rows, cfg)
    assert rm.counts[ALL_GROUPS]["eligible"] == 2  # v2 (A=5, exactly t_w) and v3 only
    assert val(rm.overall["wait_mean"]) == pytest.approx((4 + 4) / 2)  # v2: 9-5, v3: 10-6
    L = 15.0
    assert val(rm.overall["throughput_per_hour"]) == pytest.approx(4 / (L / 60))  # v0 (C=8) counts: in [5,20]
    assert val(rm.overall["utilization"]) == pytest.approx((11 - 5) / L)  # busy continuously 5..11
    # queue: t5: v1,v2 waiting (2) ... v0 done at 8 -> v1 starts: q=2 (v2,v3 after t6) compute independently
    assert val(rm.overall["queue_length_peak"]) == 3


def test_window_integrals_match_bruteforce():
    rm, rec = run_metrics(SPEC_ROWS, two_group_config(warmup_minutes=2))
    L = 18.0
    qs = [(p.time, p.queue_length, p.busy_counters) for p in rec.steps]
    q = b = 0.0
    for t0 in [2 + i * 0.001 for i in range(int(L * 1000))]:  # fine midpoint sampling
        cur = [x for x in qs if x[0] <= t0][-1]
        q += cur[1] * 0.001
        b += cur[2] * 0.001
    assert val(rm.overall["queue_length_mean"]) == pytest.approx(q / L, abs=0.01)
    assert val(rm.overall["utilization"]) == pytest.approx(b / L, abs=0.01)


def test_disabled_features_are_not_evaluable_not_zero():
    rm, _ = run_metrics(SPEC_ROWS)
    assert rm.overall["abandonment_rate"].reason == "FEATURE_DISABLED"
    assert rm.overall["blocking_rate"].reason == "FEATURE_DISABLED"


def test_metric_rows_flattening():
    rm, _ = run_metrics(SPEC_ROWS)
    rows = metric_rows(rm)
    assert ("wait_mean", ALL_GROUPS) in {(k, g) for k, g, _ in rows} and ("wait_mean", "x") in {(k, g) for k, g, _ in rows}
    assert len(rows) == len(rm.overall) + sum(len(v) for v in rm.groups.values())


def test_percentile_convention_type7():
    assert percentile([0, 3, 4, 5, 6], 95) == pytest.approx(5.8)
    assert percentile([1, 2], 50) == 1.5 and percentile([7], 95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_t_interval_matches_scipy_oracle_and_min_n():
    from scipy import stats

    xs = [2.1, 3.4, 2.8, 3.9, 2.5, 3.1, 2.7, 3.6, 2.9, 3.3]
    iv = t_interval(xs, 0.95, 5)
    lo, hi = stats.t.interval(0.95, len(xs) - 1, loc=sum(xs) / 10, scale=stats.sem(xs))
    assert iv.mean == pytest.approx(3.03) and iv.ci_low == pytest.approx(lo) and iv.ci_high == pytest.approx(hi)
    assert (round(iv.ci_low, 3), round(iv.ci_high, 3)) == (2.644, 3.416) and round(iv.std, 4) == 0.5397
    short = t_interval(xs[:4], 0.95, 5)
    assert short.ci_low is None and short.std is not None
    assert t_interval([1.0], 0.95, 5).std is None
    z = t_interval([2.0] * 6, 0.95, 5)
    assert z.zero_variance and z.ci_low == z.ci_high == 2.0
    with pytest.raises(ValueError):
        t_interval([], 0.95, 5)


# ---------------- property tests: metrics agree with an independent recomputation --------------
halves = st.integers(0, 39).map(lambda i: i / 2.0)
rows_st = st.lists(
    st.tuples(halves, st.integers(1, 10).map(lambda i: i / 2.0), st.sampled_from(["x", "y"]),
              st.one_of(st.just(None), st.integers(1, 12).map(lambda i: i / 2.0))),
    min_size=0, max_size=30,
).map(lambda rows: sorted(rows, key=lambda r: r[0]))  # fmt: skip


@settings(max_examples=200, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3), cap=st.one_of(st.none(), st.integers(0, 3)),
       tw=st.sampled_from([0, 2, 5]))  # fmt: skip
def test_property_metrics_consistent_with_outcomes(rows, c, cap, tw):
    cfg = two_group_config(counters=c, queue_capacity=cap, warmup_minutes=tw, horizon_minutes=25,
                           abandonment={"enabled": True, "patience": {"kind": "deterministic", "value_minutes": 3}})  # fmt: skip
    w = scripted_g(rows)
    rec = simulate(w, EngineParams(c, 25.0, cap), FcfsPolicy())
    rm = compute_run_metrics(rec, cfg)
    elig = [o for o in rec.outcomes if o.arrival_time >= tw]
    assert rm.counts[ALL_GROUPS]["eligible"] == len(elig)
    started = [o for o in elig if o.service_start is not None]
    assert rm.counts[ALL_GROUPS]["started"] == len(started)
    if started:
        waits = [o.service_start - o.arrival_time for o in started]
        assert min(waits) >= 0
        assert val(rm.overall["wait_mean"]) == pytest.approx(sum(waits) / len(waits))
        assert val(rm.overall["wait_median"]) == pytest.approx(sorted(waits)[len(waits) // 2] if len(waits) % 2 else
                                                                (sorted(waits)[len(waits) // 2 - 1] + sorted(waits)[len(waits) // 2]) / 2)  # fmt: skip
    u = val(rm.overall["utilization"])
    assert -1e-12 <= u <= 1 + 1e-12
    busy = sum(min(o.completion if o.completion is not None else 25.0, 25.0) - max(o.service_start, tw)
               for o in rec.outcomes if o.service_start is not None and min(o.completion if o.completion is not None else 25.0, 25.0) > max(o.service_start, tw))  # fmt: skip
    assert u == pytest.approx(busy / (c * (25.0 - tw)), abs=1e-9)
    s = rm.overall["sla_attainment"]
    if s.evaluable:
        assert 0 <= val(s) <= 1
    for k, _g in (("completed_count", "completed"),):
        assert val(rm.overall[k]) == sum(1 for o in rec.outcomes if o.status.value == "COMPLETED" and tw <= o.completion <= 25.0)  # fmt: skip
    counts = rm.counts[ALL_GROUPS]
    assert counts["eligible"] == (counts["started"] + counts["abandoned"] + counts["blocked"] + counts["waiting_at_horizon"])
