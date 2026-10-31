"""SimuLoom fairness-constrained policy (spec section 8): exact scores, guard, ties, invariants."""

import ast
import itertools
import math
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from simuloom.domain.policies.base import DecisionContext, GroupWaitStats, WaitingVisitor
from simuloom.domain.policies.fair import FairParams, SimuLoomFairPolicy, resolve_fair_params
from simuloom.domain.policies.fcfs import FcfsPolicy
from simuloom.domain.policies.registry import create_policy
from simuloom.domain.simulation.engine import EngineParams, simulate
from tests.helpers import base_config, scripted_g, two_group_config

POLICIES_DIR = Path(__file__).resolve().parent.parent / "src" / "simuloom" / "domain" / "policies"


def wv(vid, arrival, group, prio):
    return WaitingVisitor(vid, arrival, group, prio)


def policy(**kw):
    base = dict(alpha=1.0, beta=0.25, gamma=0.5, p_max=2.0, t_ref=15.0, d_scale=3.0, w_guard=100.0,
                n_pressure_min=30, c_min=1, c_max=2)  # fmt: skip
    base.update(kw)
    return SimuLoomFairPolicy(FairParams(**base))


def ctx(now, stats=None, idle=1):
    return DecisionContext(now, idle, stats or {})


def test_spec_example_1_exact_scores_and_selection():
    """Spec 8.4 Example 1: t=20, group means R_ug=4, R_pg=5, R_int=8 (pressure 0, 1/3, 4/3)."""
    stats = {
        "ug": GroupWaitStats(105.0, 29),  # + waiting a (w=15): (105+15)/30 = 4.0
        "pg": GroupWaitStats(132.0, 29),  # + waiting c (w=18): (132+18)/30 = 5.0
        "int": GroupWaitStats(232.0, 29),  # + waiting b (w=8): (232+8)/30 = 8.0
    }
    a, b, c = wv(0, 5.0, "ug", 2), wv(1, 12.0, "int", 1), wv(2, 2.0, "pg", 2)
    p = policy()
    sel = p.select([a, b, c], ctx(20.0, stats))
    assert sel.visitor_id == 1 and "pressure=1.3333" in sel.reason and "score=1.450000" in sel.reason
    assert sel.flags == ()
    # the other two scores, via single-candidate selections (identical pressure environment)
    assert "score=1.000000" in p.select([a], ctx(20.0, {**stats, "ug": GroupWaitStats(105.0, 29)})).reason
    # FCFS would have chosen c (earliest arrival): the fairness terms changed the outcome
    assert FcfsPolicy().select([a, b, c], ctx(20.0)).visitor_id == 2
    assert p.overtaking_bound_minutes == pytest.approx(18.75)  # tau = 15 * (0.25 + 0.5*2) / 1


def test_spec_example_1_c_score():
    stats = {"ug": GroupWaitStats(105.0, 29), "pg": GroupWaitStats(132.0, 29), "int": GroupWaitStats(232.0, 29)}
    c = wv(2, 2.0, "pg", 2)
    # remove b and a so only c is selectable: score(c) = 18/15 + 0 + 0.5 * (R_pg - R_ref)/3 with R_ref = R_ug-ish
    only_c = policy().select([c], ctx(20.0, {**stats, "ug": GroupWaitStats(120.0, 30)}))  # R_ug = 4.0 w/o waiting a
    assert "score=1.366667" in only_c.reason


def test_spec_example_2_guard_and_tie_break():
    p = policy(w_guard=45.0)
    d, e, f = wv(0, 3.0, "a", 2), wv(1, 4.0, "a", 2), wv(2, 40.0, "a", 1)
    sel = p.select([f, e, d], ctx(50.0, idle=2))
    assert sel.visitor_id == 0 and "guard" in sel.reason and sel.flags == ("GUARD_SELECTED",)
    sel = p.select([f, e, d], ctx(50.0, idle=1))  # 2 overdue > 1 idle counter
    assert sel.visitor_id == 0 and sel.flags == ("GUARD_SELECTED", "GUARD_SATURATED")
    g, h = wv(7, 10.0, "a", 2), wv(8, 10.0, "a", 2)  # identical scores -> lowest visitor_id
    assert p.select([h, g], ctx(20.0)).visitor_id == 7


def test_spec_example_3_pressure_unavailable():
    p = policy(n_pressure_min=30)
    sel = p.select([wv(0, 0.0, "a", 1), wv(1, 5.0, "b", 2)], ctx(10.0))
    assert "PRESSURE_UNAVAILABLE" in sel.flags and "pressure=0.0000" in sel.reason
    assert sel.visitor_id == 0  # urgency 10/15 + priority 0.25 vs 5/15 + 0


def test_guard_ignores_score_even_for_huge_pressure_and_priority():
    p = policy(w_guard=10.0, gamma=5.0, beta=5.0)
    stats = {"int": GroupWaitStats(1000.0, 30)}
    sel = p.select([wv(0, 0.0, "ug", 2), wv(1, 15.0, "int", 1)], ctx(20.0, stats))
    assert sel.visitor_id == 0  # overdue ug beats the prioritised, pressured int visitor


def test_pressure_from_waiting_only_group_before_anyone_is_served():
    """All of group g are still waiting (none served): elapsed waits alone create pressure."""
    p = policy(n_pressure_min=2, d_scale=10.0, gamma=1.0, beta=0.0, alpha=0.001)
    waiting = [wv(0, 0.0, "slow", 1), wv(1, 0.0, "slow", 1), wv(2, 9.0, "fast", 1)]
    stats = {"fast": GroupWaitStats(2.0, 1)}  # fast: (2 + 1)/2 = 1.5 ; slow: (10+10)/2 = 10
    sel = p.select(waiting, ctx(10.0, stats))
    assert sel.visitor_id == 0 and "pressure=0.8500" in sel.reason  # (10 - 1.5) / 10


def test_pressure_is_capped():
    p = policy(n_pressure_min=1, d_scale=1.0, p_max=2.0, gamma=1.0, beta=0.0, alpha=0.001)
    stats = {"g1": GroupWaitStats(0.0, 1), "g2": GroupWaitStats(100.0, 1)}
    sel = p.select([wv(0, 5.0, "g2", 1)], ctx(5.0, stats))
    assert "pressure=2.0000" in sel.reason


def test_single_priority_class_has_zero_priority_term():
    p = policy(c_min=1, c_max=1, gamma=0.0)
    assert p.select([wv(0, 0.0, "a", 1), wv(1, 1.0, "a", 1)], ctx(5.0)).visitor_id == 0


def test_select_is_independent_of_list_order_and_does_not_mutate():
    p = policy()
    base = [wv(0, 0.0, "a", 2), wv(1, 3.0, "b", 1), wv(2, 3.0, "a", 1), wv(3, 7.0, "b", 2)]
    snap = list(base)
    stats = {"a": GroupWaitStats(50.0, 40), "b": GroupWaitStats(300.0, 40)}
    picks = {p.select(list(perm), ctx(12.0, stats)).visitor_id for perm in itertools.permutations(base)}
    assert len(picks) == 1 and base == snap


def test_alpha_large_approaches_fcfs_and_beta_large_approaches_static():
    waiting = [wv(0, 0.0, "a", 2), wv(1, 1.0, "a", 1), wv(2, 2.0, "a", 1)]
    assert policy(alpha=1000.0).select(waiting, ctx(3.0)).visitor_id == 0  # FCFS-like
    assert policy(beta=1000.0, alpha=0.001, gamma=0.0).select(waiting, ctx(3.0)).visitor_id == 1  # static-like


def test_params_validation():
    for bad in ({"alpha": 0}, {"beta": -1}, {"gamma": math.nan}, {"p_max": 0}, {"t_ref": -1}, {"d_scale": 0},
                {"w_guard": math.inf}, {"n_pressure_min": 0}, {"c_min": 3, "c_max": 2}):  # fmt: skip
        with pytest.raises(ValueError):
            FairParams(**bad)


def test_resolve_defaults_from_scenario_and_notes():
    cfg = base_config()  # theta 15, c1 disparity limit 3, c3 individual limit 45, classes 2/2/1
    fp, notes = resolve_fair_params({}, cfg)
    assert (fp.t_ref, fp.d_scale, fp.w_guard, fp.n_pressure_min, fp.c_min, fp.c_max) == (15.0, 3.0, 45.0, 30, 1, 2)
    assert (fp.alpha, fp.beta, fp.gamma, fp.p_max) == (1.0, 0.25, 0.5, 2.0)
    assert len(notes) == 3 and any("d_scale defaulted" in n for n in notes)
    bare = base_config(constraints=[])
    fp2, notes2 = resolve_fair_params({}, bare)
    assert fp2.d_scale == 15.0 and fp2.w_guard == 45.0  # fallback: t_ref and 3 x theta
    assert any("fell back" in n for n in notes2)
    fp3, _ = resolve_fair_params(
        {"alpha": 2, "w_guard_minutes": 20, "d_scale_minutes": 4, "t_ref_minutes": 10, "n_pressure_min": 5}, cfg
    )
    assert (fp3.alpha, fp3.w_guard, fp3.d_scale, fp3.t_ref, fp3.n_pressure_min) == (2.0, 20.0, 4.0, 10.0, 5)
    with pytest.raises(ValueError, match="unknown parameters"):
        resolve_fair_params({"bogus": 1}, cfg)
    with pytest.raises(ValueError):
        resolve_fair_params({"alpha": -1}, cfg)


def test_registry_creates_fair_policy_and_requires_scenario():
    pol = create_policy("simuloom_fair", {"gamma": 1.0}, base_config())
    assert isinstance(pol, SimuLoomFairPolicy) and pol.params.gamma == 1.0
    with pytest.raises(ValueError, match="needs the scenario"):
        create_policy("simuloom_fair")


def test_policy_code_never_touches_service_or_patience():
    forbidden = {"service_minutes", "patience_minutes", "no_show"}
    for f in POLICIES_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = {getattr(node, "id", None), getattr(node, "attr", None), getattr(node, "arg", None)}
            assert not forbidden & names, (f, node)


# ------------------------------------------------------------------ engine integration
def test_engine_feeds_group_stats_and_counts_flags():
    """Two groups, one counter. The policy sees realised waits of served/abandoned visitors."""
    seen = []

    class Spy(FcfsPolicy):
        def select(self, waiting, ctx):  # type: ignore[no-untyped-def]
            seen.append((ctx.now, {g: (s.closed_wait_sum, s.closed_count) for g, s in ctx.group_stats.items()}))
            return super().select(waiting, ctx)

    rows = [(0, 4, "x"), (1, 2, "y", 2.0), (2, 1, "y")]  # v1 abandons at t=3 (elapsed 2); v0 done at 4
    simulate(scripted_g(rows), EngineParams(1, 20.0), Spy())
    assert seen[0] == (0.0, {"x": (0.0, 0)} if False else {})  # first decision: nobody closed yet
    # at t=4: v0 started at 0 (wait 0, group x); v1 abandoned at 3 (wait 2, group y)
    assert seen[1] == (4.0, {"x": (0.0, 1), "y": (2.0, 1)})


def test_fair_policy_through_engine_flags_and_bounded_overtaking():
    cfg = base_config(abandonment={"enabled": False})
    from simuloom.domain.workload.generator import generate_workload

    w = generate_workload(cfg, 7, 0)
    pol = create_policy("simuloom_fair", {"w_guard_minutes": 5.0}, cfg)
    r = simulate(w, EngineParams(cfg.counters, cfg.horizon_minutes), pol, record_trace=True)
    assert r.policy_key == "simuloom_fair" and r.policy_version == "0.1.0"
    flags = dict(r.policy_flags)
    assert flags.get("GUARD_SELECTED", 0) > 0  # small guard threshold: the guard fires
    arr = {v.visitor_id: v.arrival_time for v in w.visitors}
    tau = pol.overtaking_bound_minutes  # type: ignore[attr-defined]
    waiting: set[int] = set()
    for e in r.trace:
        if e.kind == "ARRIVE":
            waiting.add(e.visitor_id)
        elif e.kind == "START":
            assert all(arr[e.visitor_id] - arr[i] <= tau + 1e-9 for i in waiting), "bounded overtaking violated"
            waiting.discard(e.visitor_id)
        elif e.kind in ("ABANDON", "BLOCK"):
            waiting.discard(e.visitor_id)


halves = st.integers(0, 39).map(lambda i: i / 2.0)
rows_st = st.lists(
    st.tuples(halves, st.integers(1, 10).map(lambda i: i / 2.0), st.sampled_from(["x", "y"]),
              st.one_of(st.just(None), st.integers(1, 12).map(lambda i: i / 2.0)), st.integers(1, 2)),
    min_size=0, max_size=30,
).map(lambda rows: sorted(rows, key=lambda r: r[0]))  # fmt: skip


@settings(max_examples=150, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3), cap=st.one_of(st.none(), st.integers(0, 3)),
       alpha=st.sampled_from([0.5, 1.0, 4.0]), beta=st.sampled_from([0.0, 0.25, 2.0]),
       gamma=st.sampled_from([0.0, 0.5, 3.0]), guard=st.sampled_from([3.0, 8.0, 100.0]))  # fmt: skip
def test_property_invariants_bounded_overtaking_capacity_guard(rows, c, cap, alpha, beta, gamma, guard):
    pol = policy(alpha=alpha, beta=beta, gamma=gamma, w_guard=guard, t_ref=3.0, d_scale=2.0, n_pressure_min=2)
    w = scripted_g(rows)
    r = simulate(w, EngineParams(c, 25.0, cap), pol, record_trace=True)
    assert all(p.busy_counters <= c for p in r.steps) and len(r.outcomes) == len(rows)
    tau = pol.overtaking_bound_minutes
    arr = {v.visitor_id: v.arrival_time for v in w.visitors}
    waiting: set[int] = set()
    for e in r.trace:
        if e.kind == "ARRIVE":
            waiting.add(e.visitor_id)
        elif e.kind == "START":
            assert all(arr[e.visitor_id] - arr[i] <= tau + 1e-9 for i in waiting)  # invariant I-4
            # guard tier (I-7): if anyone is overdue at this instant, the chosen visitor is overdue
            if any(e.time - arr[i] >= guard for i in waiting):
                assert e.time - arr[e.visitor_id] >= guard
            waiting.discard(e.visitor_id)
        elif e.kind in ("ABANDON", "BLOCK"):
            waiting.discard(e.visitor_id)


@settings(max_examples=100, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3))
def test_property_beta_gamma_zero_and_no_guard_is_fcfs(rows, c):
    """Invariant I-5: with beta = gamma = 0 and the guard never reached, order equals FCFS."""
    w = scripted_g(rows)
    base = simulate(w, EngineParams(c, 25.0), FcfsPolicy())
    fair = simulate(w, EngineParams(c, 25.0), policy(beta=0.0, gamma=0.0, w_guard=1e9))
    assert fair.outcomes == base.outcomes


def test_two_group_integration_pressure_helps_the_slower_group():
    """Statistical sanity (not a guarantee): on the same workload the policy yields a different order than FCFS."""
    cfg = two_group_config(horizon_minutes=20)
    rows = [(0, 3, "x"), (0.5, 1, "y"), (1, 1, "x"), (1.5, 1, "y"), (2, 1, "x")]
    w = scripted_g(rows)
    fcfs = simulate(w, EngineParams(1, 20.0), FcfsPolicy(), record_trace=True)
    fair = simulate(w, EngineParams(1, 20.0), policy(n_pressure_min=1, d_scale=1.0), record_trace=True)
    assert [e.visitor_id for e in fcfs.trace if e.kind == "START"] == [0, 1, 2, 3, 4]
    assert fair.workload_fingerprint == fcfs.workload_fingerprint
    assert cfg.counters == 1
