"""Phase 2: static-priority and aging-priority policies (unit, integration, property tests)."""

import hashlib
import itertools
import math
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from simuloom.domain.policies.aging import AgingPriorityPolicy
from simuloom.domain.policies.base import DecisionContext, WaitingVisitor
from simuloom.domain.policies.fcfs import FcfsPolicy
from simuloom.domain.policies.registry import create_policy
from simuloom.domain.policies.static_priority import StaticPriorityPolicy
from simuloom.domain.simulation.engine import EngineParams, simulate
from simuloom.domain.workload.generator import generate_workload
from tests.helpers import base_config, scripted_p

ROOT = Path(__file__).resolve().parent.parent
POLICIES_DIR = ROOT / "src" / "simuloom" / "domain" / "policies"
INF = math.inf


def wv(vid: int, arrival: float, prio: int) -> WaitingVisitor:
    return WaitingVisitor(vid, arrival, "g", prio)


def pick(policy, waiting, now=10.0) -> int:
    return policy.select(waiting, DecisionContext(now, 1)).visitor_id


# ------------------------------------------------------------------ static priority (unit)
def test_static_orders_by_class_then_arrival_then_id():
    p = StaticPriorityPolicy()
    assert pick(p, [wv(0, 0, 3), wv(1, 5, 1), wv(2, 4, 2)]) == 1  # class dominates arrival
    assert pick(p, [wv(0, 5, 2), wv(1, 3, 2), wv(2, 4, 2)]) == 1  # same class: earliest arrival
    assert pick(p, [wv(7, 3, 2), wv(4, 3, 2), wv(9, 3, 2)]) == 4  # same class+arrival: lowest id


def test_static_is_independent_of_input_order_and_does_not_mutate():
    p = StaticPriorityPolicy()
    base = [wv(0, 2, 2), wv(1, 1, 3), wv(2, 2, 1), wv(3, 2, 1)]
    snapshot = list(base)
    for perm in itertools.permutations(base):
        assert pick(p, list(perm)) == 2
    assert base == snapshot


def test_lower_class_number_means_higher_priority():
    assert pick(StaticPriorityPolicy(), [wv(0, 0, 10), wv(1, 99, 1)]) == 1


# ------------------------------------------------------------------ aging (unit)
def test_aging_effective_priority_is_linear_in_waiting_time():
    p = AgingPriorityPolicy(0.5)
    v = wv(0, 2.0, 3)
    assert [p.effective_priority(v, t) for t in (2.0, 4.0, 10.0)] == [3.0, 2.0, -1.0]


def test_aging_rate_zero_equals_static_ordering():
    a, s = AgingPriorityPolicy(0.0), StaticPriorityPolicy()
    base = [wv(0, 0, 3), wv(1, 1, 2), wv(2, 2, 1), wv(3, 2, 1), wv(4, 0, 2)]
    for now in (2.0, 50.0, 1e6):
        for perm in itertools.permutations(base):
            assert pick(a, list(perm), now) == pick(s, list(perm), now)


@pytest.mark.parametrize(
    ("high_arrival", "expected_id"),
    [(3.5, 1), (3.99, 1), (4.0, 0), (4.01, 0), (10.0, 0)],  # r=0.5 -> crossover at a = 2/r = 4
)
def test_aging_crossover_boundary(high_arrival, expected_id):
    """Low priority (class 3, arrived 0) vs high priority (class 1, arrived at high_arrival).

    p_low - p_high = 2 - r*high_arrival; exact tie at 4.0 is broken by earlier arrival (id 0).
    """
    p = AgingPriorityPolicy(0.5)
    waiting = [wv(0, 0.0, 3), wv(1, high_arrival, 1)]
    for now in (high_arrival, high_arrival + 7.0):  # the comparison does not depend on `now`
        assert pick(p, waiting, now) == expected_id


def test_aging_changes_decision_over_time_for_a_fixed_waiting_set():
    """Same two visitors, only the clock differs: the older low-priority one eventually wins."""
    p = AgingPriorityPolicy(1.0)
    low, high = wv(0, 0.0, 3), wv(1, 5.0, 1)
    assert pick(p, [low, high], now=5.0) == 0  # p_low=-2 vs p_high=1: low already ahead (aged 5 min)
    p2 = AgingPriorityPolicy(0.1)
    assert pick(p2, [low, high], now=5.0) == 1  # p_low=2.5 vs p_high=1
    assert pick(p2, [low, wv(1, 25.0, 1)], now=25.0) == 0  # p_low=0.5 vs 1: aged enough


def test_aging_ties_resolved_by_arrival_then_id():
    p = AgingPriorityPolicy(0.5)
    assert pick(p, [wv(5, 2.0, 2), wv(3, 2.0, 2)]) == 3  # identical -> lowest id
    # equal p_eff, different arrival (class 2 @0 vs class 1 @2 at now=6, r=0.5 -> 2-3 = 1-2): earliest wins
    assert pick(p, [wv(1, 2.0, 1), wv(0, 0.0, 2)], now=6.0) == 0


def test_aging_large_rate_approaches_fcfs():
    p = AgingPriorityPolicy(1000.0)
    assert pick(p, [wv(0, 0.0, 10), wv(1, 0.5, 1), wv(2, 1.0, 1)], now=2.0) == 0


@pytest.mark.parametrize("bad", [-0.1, math.nan, INF, -INF, "0.5", None, True])
def test_aging_rejects_invalid_rate(bad):
    with pytest.raises(ValueError):
        AgingPriorityPolicy(bad)


def test_aging_accepts_integer_and_zero_rate():
    assert AgingPriorityPolicy(0).aging_rate_per_minute == 0.0
    assert AgingPriorityPolicy(2).aging_rate_per_minute == 2.0


# ------------------------------------------------------------------ registry
def test_registry_creates_policies_and_validates_params():
    assert isinstance(create_policy("static_priority"), StaticPriorityPolicy)
    a = create_policy("aging_priority", {"aging_rate_per_minute": 0.25})
    assert isinstance(a, AgingPriorityPolicy) and a.aging_rate_per_minute == 0.25
    for key in ("fcfs", "static_priority"):
        with pytest.raises(ValueError, match="no parameters"):
            create_policy(key, {"x": 1})
    with pytest.raises(ValueError, match="requires parameter"):
        create_policy("aging_priority")
    with pytest.raises(ValueError, match="unknown parameters"):
        create_policy("aging_priority", {"aging_rate_per_minute": 1, "extra": 2})
    with pytest.raises(ValueError):
        create_policy("aging_priority", {"aging_rate_per_minute": -1})
    assert [create_policy(k, p).key for k, p in
            [("fcfs", {}), ("static_priority", {}), ("aging_priority", {"aging_rate_per_minute": 1})]] == [
        "fcfs", "static_priority", "aging_priority"]  # fmt: skip


def test_policies_cannot_see_service_or_patience():
    import ast

    forbidden = {"service_minutes", "patience_minutes", "no_show"}
    for f in POLICIES_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):  # code only; docstrings may mention them
            names = {getattr(node, "id", None), getattr(node, "attr", None), getattr(node, "arg", None)}
            assert not forbidden & names, (f, node)
    assert not forbidden & set(WaitingVisitor.__slots__)


# ------------------------------------------------------------------ engine integration
def order(r):
    return [e.visitor_id for e in r.trace if e.kind == "START"]


STARVATION_ROWS = [(0, 2, 1), (0.5, 1, 3), (1, 2, 1), (2, 2, 1)]  # (arrival, service, class)


def run_policy(rows, policy, c=1, h=40.0, cap=None):
    return simulate(scripted_p(rows), EngineParams(c, h, cap), policy, record_trace=True)


def test_static_starves_low_priority_while_aging_serves_it_earlier():
    """Hand-computed (c=1). At t=2 waiting: v1(c3,A=.5), v2(c1,A=1), v3(c1,A=2).
    static: v0, v2, v3, v1.  aging r=2: t=2 -> p(v1)=0,p(v2)=-1,p(v3)=1 -> v2;
    t=4 -> p(v1)=-4, p(v3)=-3 -> v1 before v3: v0, v2, v1, v3."""
    assert order(run_policy(STARVATION_ROWS, StaticPriorityPolicy())) == [0, 2, 3, 1]
    assert order(run_policy(STARVATION_ROWS, AgingPriorityPolicy(2.0))) == [0, 2, 1, 3]
    assert order(run_policy(STARVATION_ROWS, AgingPriorityPolicy(0.0))) == [0, 2, 3, 1]
    assert order(run_policy(STARVATION_ROWS, FcfsPolicy())) == [0, 1, 2, 3]
    r = run_policy(STARVATION_ROWS, StaticPriorityPolicy())
    o = {x.visitor_id: x for x in r.outcomes}
    assert o[1].service_start == 6.0  # waited 5.5 min behind later, higher-priority arrivals


def test_same_arrival_ties_use_visitor_id_through_the_engine():
    rows = [(1, 2, 2), (1, 2, 2), (1, 2, 2)]
    for policy in (StaticPriorityPolicy(), AgingPriorityPolicy(0.7), FcfsPolicy()):
        assert order(run_policy(rows, policy)) == [0, 1, 2]


def test_simultaneous_arrival_and_completion_with_priorities():
    """At t=4 v0 completes and v1(c3, A=1) waits; v2(c1) arrives at t=4: both policies pick v2."""
    rows = [(0, 4, 1), (1, 1, 3), (4, 1, 1)]
    assert order(run_policy(rows, StaticPriorityPolicy())) == [0, 2, 1]
    # aging r=0.5: p(v1)=3-0.5*3=1.5, p(v2)=1 -> v2 still first
    assert order(run_policy(rows, AgingPriorityPolicy(0.5))) == [0, 2, 1]
    # aging r=1: p(v1)=0, p(v2)=1 -> aged v1 wins
    assert order(run_policy(rows, AgingPriorityPolicy(1.0))) == [0, 1, 2]


def check_selection_oracle(r, workload, key_fn):
    """For every START event, the chosen visitor has the minimal key among those waiting at that
    moment, reconstructed from the event trace (independent of the policy implementation)."""
    cls = {v.visitor_id: v.priority_class for v in workload.visitors}
    arr = {v.visitor_id: v.arrival_time for v in workload.visitors}
    waiting: set[int] = set()
    starts = 0
    for e in r.trace:
        if e.kind == "ARRIVE":
            waiting.add(e.visitor_id)
        elif e.kind == "START":
            k = key_fn(cls[e.visitor_id], arr[e.visitor_id], e.visitor_id, e.time)
            assert e.visitor_id in waiting
            assert all(k <= key_fn(cls[j], arr[j], j, e.time) for j in waiting)
            waiting.discard(e.visitor_id)
            starts += 1
        elif e.kind in ("ABANDON", "BLOCK"):
            waiting.discard(e.visitor_id)
    return starts


def static_key(c, a, i, t):
    return (c, a, i)


def aging_key(rate):
    return lambda c, a, i, t: (round(c - rate * (t - a), 9), a, i)


def test_oracle_on_generated_workload_all_policies():
    cfg = base_config()  # 3 counters, rho~2: long queues, 3 groups, classes 2/2/1, abandonment on
    w = generate_workload(cfg, 20261024, 0)
    params = EngineParams(cfg.counters, cfg.horizon_minutes)
    results = {}
    for key, pol, kf in [
        ("static", StaticPriorityPolicy(), static_key),
        ("aging", AgingPriorityPolicy(0.05), aging_key(0.05)),
    ]:
        r = simulate(w, params, pol, record_trace=True)
        assert check_selection_oracle(r, w, kf) > 100
        assert r.workload_fingerprint == w.fingerprint
        results[key] = r
    fcfs = simulate(w, params, FcfsPolicy(), record_trace=True)
    assert len({r.workload_fingerprint for r in (*results.values(), fcfs)}) == 1  # same exogenous inputs
    assert order(results["static"]) != order(fcfs) and order(results["aging"]) != order(results["static"])

    # international (class 1) waits less under static than under FCFS: policy effect is real
    def mean_wait(r, g):
        xs = [o.service_start - o.arrival_time for o in r.outcomes if o.group_key == g and o.service_start is not None]
        return sum(xs) / len(xs)

    assert mean_wait(results["static"], "international") < mean_wait(fcfs, "international")


halves = st.integers(0, 39).map(lambda i: i / 2.0)
rows_st = st.lists(
    st.tuples(halves, st.integers(1, 10).map(lambda i: i / 2.0), st.integers(1, 4),
              st.one_of(st.just(INF), st.integers(1, 12).map(lambda i: i / 2.0))),
    min_size=0, max_size=30,
).map(lambda rows: sorted(rows, key=lambda r: r[0]))  # fmt: skip


@settings(max_examples=200, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3), cap=st.one_of(st.none(), st.integers(0, 3)),
       rate=st.sampled_from([0.0, 0.25, 0.5, 2.0]))  # fmt: skip
def test_property_selection_oracle_and_capacity(rows, c, cap, rate):
    w = scripted_p(rows)
    for pol, kf in ((StaticPriorityPolicy(), static_key), (AgingPriorityPolicy(rate), aging_key(rate))):
        r = simulate(w, EngineParams(c, 25.0, cap), pol, record_trace=True)
        check_selection_oracle(r, w, kf)
        assert all(p.busy_counters <= c for p in r.steps)
        assert len(r.outcomes) == len(rows)


@settings(max_examples=100, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3))
def test_property_equal_classes_collapse_to_fcfs(rows, c):
    """With a single priority class, static and aging (any rate) are exactly FCFS."""
    flat = [(a, s, 1, p) for a, s, _, p in rows]
    w = scripted_p(flat)
    base = simulate(w, EngineParams(c, 25.0), FcfsPolicy(), record_trace=True)
    for pol in (StaticPriorityPolicy(), AgingPriorityPolicy(0.0), AgingPriorityPolicy(3.0)):
        r = simulate(w, EngineParams(c, 25.0), pol, record_trace=True)
        assert r.outcomes == base.outcomes and r.steps == base.steps


@settings(max_examples=100, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3))
def test_property_zero_rate_aging_equals_static_and_huge_rate_equals_fcfs(rows, c):
    uniq = {}
    for a, s, p, pat in rows:  # distinct arrival times so FCFS has no id-vs-class tie to disagree on
        uniq.setdefault(a, (a, s, p, pat))
    w = scripted_p(sorted(uniq.values()))
    static = simulate(w, EngineParams(c, 25.0), StaticPriorityPolicy())
    assert simulate(w, EngineParams(c, 25.0), AgingPriorityPolicy(0.0)).outcomes == static.outcomes
    fcfs = simulate(w, EngineParams(c, 25.0), FcfsPolicy())
    # classes span <= 3 units, arrivals differ by >= 0.5 min: r=100 makes waiting time decisive
    assert simulate(w, EngineParams(c, 25.0), AgingPriorityPolicy(100.0)).outcomes == fcfs.outcomes


# ------------------------------------------------------------------ reproducibility
def digest(policy_key: str, params: dict | None = None, seed: int = 42, rep: int = 0) -> str:
    cfg = base_config(counters=4)
    w = generate_workload(cfg, seed, rep)
    r = simulate(
        w,
        EngineParams(cfg.counters, cfg.horizon_minutes),
        create_policy(policy_key, params),
        record_trace=True,
    )
    return hashlib.sha256(repr((r.outcomes, r.steps, r.trace, r.workload_fingerprint)).encode()).hexdigest()


@pytest.mark.parametrize(
    ("key", "params"),
    [("fcfs", None), ("static_priority", None), ("aging_priority", {"aging_rate_per_minute": 0.1})],
)
def test_reproducible_same_process_and_fresh_process(key, params):
    assert digest(key, params) == digest(key, params)
    assert digest(key, params) != digest(key, params, rep=1)
    code = f"from tests.test_policies import digest; print(digest({key!r}, {params!r}))"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
        env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
    ).stdout.strip()  # fmt: skip
    assert out == digest(key, params)


def test_policies_differ_on_identical_workload():
    d = {k: digest(k, p) for k, p in [("fcfs", None), ("static_priority", None),
                                      ("aging_priority", {"aging_rate_per_minute": 0.1})]}  # fmt: skip
    assert len(set(d.values())) == 3
