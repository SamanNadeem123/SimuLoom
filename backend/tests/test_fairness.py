"""Constraint evaluation (per run) and replication aggregation: hand-calculated cases (spec 7.4-7.6)."""

import statistics

import pytest
from scipy import stats as sps

from simuloom.domain.config.models import ConstraintType, StatisticsConfig
from simuloom.domain.fairness.aggregate import (
    Certainty,
    aggregate_constraint,
    aggregate_constraints,
    aggregate_metric,
    experiment_label,
    paired_difference,
    policy_label,
)
from simuloom.domain.fairness.evaluator import (
    ConstraintResult,
    ConstraintStatus,
    evaluate_run_constraints,
    violation_magnitude,
)
from simuloom.domain.metrics.compute import compute_run_metrics
from simuloom.domain.metrics.types import ALL_GROUPS, MetricValue
from simuloom.domain.policies.fcfs import FcfsPolicy
from simuloom.domain.simulation.engine import EngineParams, simulate
from tests.helpers import scripted_g, two_group_config

SPEC_ROWS = [(0, 4, "x"), (1, 3, "y"), (2, 2, "x"), (3, 5, "y"), (10, 1, "x")]  # group means x=3.0, y=4.5
S = ConstraintStatus


def evaluate(constraints, rows=SPEC_ROWS, policy="fcfs", **over):
    cfg = two_group_config(constraints=constraints, **over)
    rec = simulate(scripted_g(rows), EngineParams(1, cfg.horizon_minutes), FcfsPolicy())
    return {(r.constraint_id, r.group_key): r for r in evaluate_run_constraints(compute_run_metrics(rec, cfg), cfg, policy)}


def c(cid, ctype, limit, unit, **kw):
    return {"id": cid, "type": ctype, "limit": limit, "unit": unit, **kw}


# ------------------------------------------------------------------ per-run, hand calculated
def test_mean_wait_disparity_violated_with_magnitude():
    r = evaluate([c("d", "MAX_MEAN_WAIT_DISPARITY", 1.0, "minute")])[("d", ALL_GROUPS)]
    assert r.status is S.VIOLATED and r.observed == pytest.approx(1.5) and r.violation_magnitude == pytest.approx(0.5)
    assert r.details["pair"] == ["x", "y"] and "no guarantee" in r.explanation


def test_equality_satisfies_and_loose_limit_satisfies():
    assert evaluate([c("d", "MAX_MEAN_WAIT_DISPARITY", 1.5, "minute")])[("d", ALL_GROUPS)].status is S.SATISFIED
    r = evaluate([c("d", "MAX_MEAN_WAIT_DISPARITY", 5.0, "minute")])[("d", ALL_GROUPS)]
    assert r.status is S.SATISFIED and r.violation_magnitude == 0.0


def test_relative_disparity_and_sla_gap_values():
    res = evaluate([c("r", "MAX_RELATIVE_WAIT_DISPARITY", 0.2, "proportion"), c("s", "MAX_SLA_GAP", 0.2, "proportion")])
    rel, gap = res[("r", ALL_GROUPS)], res[("s", ALL_GROUPS)]
    assert rel.observed == pytest.approx(1 / 3) and rel.status is S.VIOLATED  # |3-4.5|/4.5
    assert gap.observed == pytest.approx(1 / 6) and gap.status is S.SATISFIED  # |1/3 - 1/2|


def test_per_group_long_wait_and_sla_rows_and_combination():
    res = evaluate([c("l", "MAX_LONG_WAIT_RATE", 0.6, "proportion"), c("m", "MIN_SLA_ATTAINMENT", 0.4, "proportion")])
    assert res[("l", "x")].status is S.VIOLATED and res[("l", "x")].violation_magnitude == pytest.approx(2 / 3 - 0.6)
    assert res[("l", "y")].status is S.SATISFIED
    assert res[("l", ALL_GROUPS)].status is S.VIOLATED and res[("l", ALL_GROUPS)].details["group_status"] == {
        "x": "VIOLATED", "y": "SATISFIED"}  # fmt: skip
    assert res[("m", "x")].status is S.VIOLATED and res[("m", "x")].violation_magnitude == pytest.approx(0.4 - 1 / 3)
    assert res[("m", "y")].status is S.SATISFIED and res[("m", ALL_GROUPS)].status is S.VIOLATED


def test_group_specific_constraint_only_that_group():
    res = evaluate([c("l", "MAX_LONG_WAIT_RATE", 0.6, "proportion", group="y")])
    assert ("l", "x") not in res and res[("l", ALL_GROUPS)].status is S.SATISFIED


def test_individual_wait():
    r = evaluate([c("i", "MAX_INDIVIDUAL_WAIT", 5.0, "minute")])[("i", ALL_GROUPS)]
    assert r.status is S.VIOLATED and r.observed == pytest.approx(6.0) and r.violation_magnitude == pytest.approx(1.0)
    assert evaluate([c("i", "MAX_INDIVIDUAL_WAIT", 6.0, "minute")])[("i", ALL_GROUPS)].status is S.SATISFIED


def test_not_evaluable_with_default_sample_thresholds_and_missing_groups():
    res = evaluate([c("d", "MAX_MEAN_WAIT_DISPARITY", 1.0, "minute"), c("l", "MAX_LONG_WAIT_RATE", 0.5, "proportion")],
                   min_sample={"group_n": 30, "p95_n": 100})  # fmt: skip
    assert res[("d", ALL_GROUPS)].status is S.NOT_EVALUABLE and res[("d", ALL_GROUPS)].reason_code == "INSUFFICIENT_GROUPS"
    assert res[("l", "x")].status is S.NOT_EVALUABLE and res[("l", ALL_GROUPS)].status is S.NOT_EVALUABLE
    only_x = evaluate([c("d", "MAX_MEAN_WAIT_DISPARITY", 1.0, "minute")], rows=[(0, 1, "x"), (3, 1, "x")])
    assert only_x[("d", ALL_GROUPS)].status is S.NOT_EVALUABLE
    empty = evaluate([c("i", "MAX_INDIVIDUAL_WAIT", 1.0, "minute")], rows=[])
    assert empty[("i", ALL_GROUPS)].status is S.NOT_EVALUABLE


def test_relative_disparity_baseline_too_small():
    rows = [(0, 1, "x"), (1, 1, "x"), (5, 1, "y"), (6, 1, "y")]  # zero waits everywhere
    r = evaluate([c("r", "MAX_RELATIVE_WAIT_DISPARITY", 0.5, "proportion")], rows=rows)[("r", ALL_GROUPS)]
    assert r.status is S.NOT_EVALUABLE and r.reason_code == "BASELINE_TOO_SMALL"


def test_disabled_constraints_produce_no_rows_and_online_attempt_flag():
    assert evaluate([c("d", "MAX_MEAN_WAIT_DISPARITY", 1.0, "minute", enabled=False)]) == {}
    cs = [c("d", "MAX_MEAN_WAIT_DISPARITY", 1.0, "minute"), c("s", "MAX_SLA_GAP", 0.2, "proportion")]
    fair = evaluate(cs, policy="simuloom_fair")
    assert fair[("d", ALL_GROUPS)].attempted_online and not fair[("s", ALL_GROUPS)].attempted_online
    assert not evaluate(cs)[("d", ALL_GROUPS)].attempted_online


def test_violation_magnitude_directions():
    assert violation_magnitude(5, 3, "<=") == 2 and violation_magnitude(1, 3, "<=") == 0
    assert violation_magnitude(0.5, 0.8, ">=") == pytest.approx(0.3) and violation_magnitude(0.9, 0.8, ">=") == 0


# ------------------------------------------------------------------ aggregation
STATS = StatisticsConfig()


def cr(obs, limit=3.0, ctype=ConstraintType.MAX_MEAN_WAIT_DISPARITY, group=ALL_GROUPS, comp="<="):
    if obs is None:
        return ConstraintResult("k", ctype, group, S.NOT_EVALUABLE, "X", None, limit, "minute", comp, None, False, "")
    mag = violation_magnitude(obs, limit, comp)
    st = S.VIOLATED if mag > 0 else S.SATISFIED
    return ConstraintResult("k", ctype, group, st, None, obs, limit, "minute", comp, mag, False, "")


def ci(xs, level=0.95):
    m, s = statistics.mean(xs), statistics.stdev(xs)
    h = sps.t.ppf(0.5 + level / 2, len(xs) - 1) * s / len(xs) ** 0.5
    return m, m - h, m + h


def test_mean_ci_violated_inconclusive():
    xs = [2.0, 3.0, 3.5, 4.0, 4.5]
    a = aggregate_constraint([cr(x) for x in xs], STATS)
    m, lo, hi = ci(xs)
    assert a.status is S.VIOLATED and a.certainty is Certainty.INCONCLUSIVE and a.rule == "MEAN_CI"
    assert a.observed == pytest.approx(m) and a.ci_low == pytest.approx(lo) and a.ci_high == pytest.approx(hi)
    assert a.violation_magnitude == pytest.approx(m - 3.0) and a.replication_pass_rate == pytest.approx(0.4)
    assert a.violation_ci_low == 0.0 and a.violation_ci_high == pytest.approx(hi - 3.0)
    assert a.n_valid == 5 and a.n_total == 5 and "no guarantee" in a.explanation


def test_mean_ci_satisfied_confirmed_and_violated_confirmed():
    low = [1.0, 1.2, 1.4, 1.1, 1.3]
    a = aggregate_constraint([cr(x) for x in low], STATS)
    assert a.status is S.SATISFIED and a.certainty is Certainty.CONFIRMED and a.replication_pass_rate == 1.0
    high = [5.0, 5.2, 5.4, 5.1, 5.3]
    b = aggregate_constraint([cr(x) for x in high], STATS)
    assert b.status is S.VIOLATED and b.certainty is Certainty.CONFIRMED and b.violation_ci_low > 0


def test_lower_bound_direction():
    xs = [0.5, 0.52, 0.48, 0.51, 0.49]
    a = aggregate_constraint([cr(x, 0.8, ConstraintType.MIN_SLA_ATTAINMENT, comp=">=") for x in xs], STATS)
    assert a.status is S.VIOLATED and a.certainty is Certainty.CONFIRMED  # whole CI below 0.8
    assert a.violation_magnitude == pytest.approx(0.8 - 0.5)


def test_too_few_and_excessive_exclusion_and_empty():
    assert aggregate_constraint([cr(1.0)] * 4, STATS).reason_code == "TOO_FEW_REPLICATIONS"
    a = aggregate_constraint([cr(1.0), cr(1.1), cr(1.2), cr(1.3), cr(1.4), cr(None), cr(None)], STATS)
    assert a.status is S.NOT_EVALUABLE and a.reason_code == "EXCESSIVE_EXCLUSION"  # 5/7 < 0.8
    ok = aggregate_constraint([cr(1.0), cr(1.1), cr(1.2), cr(1.3), cr(1.4), cr(1.5), cr(1.6), cr(1.7), cr(1.8), cr(None)], STATS)
    assert ok.status is S.SATISFIED and ok.n_valid == 9 and ok.n_total == 10  # 0.9 >= 0.8
    assert aggregate_constraint([cr(None)] * 5, STATS).reason_code == "X"  # per-run cause is propagated
    with pytest.raises(ValueError):
        aggregate_constraint([], STATS)


def test_zero_variance():
    a = aggregate_constraint([cr(2.0)] * 5, STATS)
    assert a.zero_variance and a.status is S.SATISFIED and a.ci_low == a.ci_high == 2.0


def test_all_replications_rule_for_individual_wait():
    t = ConstraintType.MAX_INDIVIDUAL_WAIT
    rows = [cr(x, 45.0, t) for x in (30, 40, 46, 20, 10)]
    a = aggregate_constraint(rows, STATS)
    assert a.rule == "ALL_REPLICATIONS" and a.status is S.VIOLATED and a.observed == 46
    assert a.details["violating_replications"] == 1 and a.violation_magnitude == pytest.approx(1.0)
    ok = aggregate_constraint([cr(x, 45.0, t) for x in (30, 40, 45)], STATS)  # <5 reps is fine for worst-case rule
    assert ok.status is S.SATISFIED
    ex = aggregate_constraint([cr(1.0, 45.0, t), cr(None, 45.0, t), cr(None, 45.0, t)], STATS)
    assert ex.reason_code == "EXCESSIVE_EXCLUSION"


def test_per_group_aggregation_and_labels():
    t = ConstraintType.MAX_LONG_WAIT_RATE
    reps = []
    for i in range(5):
        x, y = 0.9 + 0.01 * i, 0.1 + 0.01 * i
        rx, ry = cr(x, 0.5, t, "x"), cr(y, 0.5, t, "y")
        reps.append([rx, ry, cr(x, 0.5, t, ALL_GROUPS)])
    aggs = aggregate_constraints(reps, STATS)
    assert [(a.group_key, a.status) for a in aggs] == [("x", S.VIOLATED), ("y", S.SATISFIED), (ALL_GROUPS, S.VIOLATED)]
    assert aggs[2].details["group_status"] == {"x": "VIOLATED", "y": "SATISFIED"}
    assert policy_label(aggs) == "VIOLATES_AT_LEAST_ONE"
    assert policy_label([]) == "NO_CONSTRAINTS"
    sat_conf = aggregate_constraints([[cr(x)] for x in (1.0, 1.2, 1.4, 1.1, 1.3)], STATS)
    sat_inc = aggregate_constraints([[cr(x)] for x in (2.0, 2.5, 3.0, 3.2, 2.8)], STATS)
    ne = aggregate_constraints([[cr(1.0)]] * 4, STATS)
    assert policy_label(sat_conf) == "SATISFIED_CONFIRMED" and policy_label(sat_inc) == "SATISFIED_INCONCLUSIVE"
    assert policy_label(ne) == "INCOMPLETE"
    viol_conf = aggregate_constraints([[cr(x)] for x in (5.0, 5.2, 5.4, 5.1, 5.3)], STATS)
    assert experiment_label({"a": viol_conf, "b": viol_conf}) == "NOT_SATISFIED_BY_ANY_TESTED_POLICY"
    assert experiment_label({"a": viol_conf, "b": sat_conf}) == "AT_LEAST_ONE_POLICY_NOT_REFUTED"
    assert experiment_label({"a": viol_conf, "b": ne}) == "AT_LEAST_ONE_POLICY_NOT_REFUTED"
    assert experiment_label({}) == "NO_POLICIES"


def mv(x):
    return MetricValue.ok(x, "minute", 10) if x is not None else MetricValue.not_evaluable("minute", 0, "NO_OBSERVATIONS")


def test_metric_aggregate_and_paired_difference():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    a = aggregate_metric([mv(x) for x in xs], "wait_mean", ALL_GROUPS, STATS)
    m, lo, hi = ci(xs)
    assert (a.n_valid, a.mean) == (5, 3.0) and a.ci_low == pytest.approx(lo) and a.ci_high == pytest.approx(hi)
    few = aggregate_metric([mv(1.0), mv(2.0), mv(None)], "wait_mean", ALL_GROUPS, STATS)
    assert few.mean == 1.5 and few.ci_low is None and few.n_valid == 2 and few.n_total == 3
    none = aggregate_metric([mv(None)] * 3, "wait_mean", ALL_GROUPS, STATS)
    assert none.mean is None and none.reason == "NO_OBSERVATIONS"
    cand, base = [mv(x) for x in (2.0, 3.0, 4.0, 5.0, 6.0)], [mv(x) for x in (1.0, 2.5, 3.0, 5.5, 4.0)]
    p = paired_difference(cand, base, "wait_mean", ALL_GROUPS, STATS)
    d = [1.0, 0.5, 1.0, -0.5, 2.0]
    assert p.n_pairs == 5 and p.mean_difference == pytest.approx(statistics.mean(d))
    assert p.ci_excludes_zero == bool(ci(d)[1] > 0 or ci(d)[2] < 0)
    skip = paired_difference([mv(1.0), mv(None)], [mv(2.0), mv(3.0)], "k", ALL_GROUPS, STATS)
    assert skip.n_pairs == 1 and skip.ci_excludes_zero is None
    with pytest.raises(ValueError):
        paired_difference(cand, base[:2], "k", ALL_GROUPS, STATS)
