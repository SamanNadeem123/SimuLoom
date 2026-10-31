import math
from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from simuloom.domain.policies.base import DecisionContext, Selection, WaitingVisitor
from simuloom.domain.policies.fcfs import FcfsPolicy
from simuloom.domain.recording.recorder import (
    LifecycleError,
    Recorder,
)
from simuloom.domain.recording.recorder import (
    VisitorState as S,
)
from simuloom.domain.simulation.engine import EngineParams, PolicyError, simulate
from simuloom.domain.workload.generator import VisitorSpec, Workload, fingerprint
from tests.helpers import scripted

INF = math.inf


def run(rows, c=1, h=20.0, cap=None, trace=True):
    return simulate(scripted(rows), EngineParams(c, h, cap), FcfsPolicy(), record_trace=trace)


def by_id(r):
    return {o.visitor_id: o for o in r.outcomes}


def test_fcfs_scripted_hand_computed():
    r = run([(0, 3), (1, 2), (2, 1)])
    o = by_id(r)
    assert [(o[i].service_start, o[i].completion) for i in range(3)] == [(0, 3), (3, 5), (5, 6)]
    assert all(x.status is S.COMPLETED for x in r.outcomes)
    assert [(e.time, e.kind, e.visitor_id) for e in r.trace] == [
        (0, "ARRIVE", 0), (0, "START", 0), (1, "ARRIVE", 1), (2, "ARRIVE", 2),
        (3, "COMPLETE", 0), (3, "START", 1), (5, "COMPLETE", 1), (5, "START", 2), (6, "COMPLETE", 2),
    ]  # fmt: skip
    assert [e.seq for e in r.trace] == list(range(len(r.trace)))


def test_fcfs_ties_by_visitor_id():
    r = run([(1, 2), (1, 2), (1, 2)])
    assert [by_id(r)[i].service_start for i in range(3)] == [1, 3, 5]


def test_multi_counter_and_arrival_at_completion_instant():
    r = run([(0, 4), (0, 4), (0, 4), (4, 1)], c=2)
    o = by_id(r)
    assert [o[i].service_start for i in range(4)] == [0, 0, 4, 4]
    assert o[3].arrival_time == 4 and o[2].service_start == 4  # both served at 4 (two counters freed)


def test_abandonment_beats_service_at_same_instant():
    r = run([(0, 4), (1, 2, 3.0), (2, 2)])
    o = by_id(r)
    assert o[1].status is S.ABANDONED and o[1].abandonment == 4
    assert o[2].service_start == 4 and o[2].completion == 6


def test_stale_patience_timer_ignored():
    r = run([(0, 1), (0, 1, 5.0)], h=10)
    o = by_id(r)
    assert o[1].status is S.COMPLETED and o[1].abandonment is None
    assert 5.0 not in [s.time for s in r.steps]  # no settle at the stale timer's timestamp


def test_no_show_never_joins():
    vs = (VisitorSpec(0, 1.0, "a", 1, 2.0, INF, True), VisitorSpec(1, 2.0, "a", 1, 1.0, INF, False))
    r = simulate(Workload(vs, fingerprint(vs), 0, 0), EngineParams(1, 10.0), FcfsPolicy())
    o = by_id(r)
    assert o[0].status is S.NO_SHOW and o[0].service_start is None
    assert o[1].service_start == 2.0


def test_queue_capacity_blocking():
    r = run([(0, 10), (1, 1), (1, 1), (2, 1)], cap=1)
    o = by_id(r)
    assert {i for i in o if o[i].status is S.BLOCKED} == {2, 3} and o[1].service_start == 10
    r = run([(0, 10), (1, 1)], cap=0)
    assert by_id(r)[0].service_start == 0 and by_id(r)[1].status is S.BLOCKED
    r = run([(0, 5)] * 4, c=2, cap=1)
    o = by_id(r)
    assert {i for i in o if o[i].status is S.BLOCKED} == {3} and o[2].service_start == 5


def test_horizon_censoring_states():
    o = by_id(run([(0, 10), (9, 1, 1.0)], h=10))
    assert o[0].status is S.COMPLETED and o[0].completion == 10
    assert o[1].status is S.ABANDONED and o[1].abandonment == 10
    o = by_id(run([(0, 10.5), (1, 1)], h=10))
    assert o[0].status is S.IN_SERVICE_AT_HORIZON and o[0].completion is None
    assert o[1].status is S.WAITING_AT_HORIZON and o[1].service_start is None


def queue_integral(r, t0, t1):
    pts = [p for p in r.steps if p.time <= t1]
    total = 0.0
    for i, p in enumerate(pts):
        end = pts[i + 1].time if i + 1 < len(pts) else t1
        lo, hi = max(p.time, t0), min(end, t1)
        if hi > lo:
            total += p.queue_length * (hi - lo)
    return total


def test_spec_worked_example_matches_recorded_steps():
    r = run([(0, 4), (1, 3), (2, 2), (3, 5), (10, 1)], h=20)
    o = by_id(r)
    assert [o[i].service_start - o[i].arrival_time for i in range(5)] == [0, 3, 5, 6, 4]
    assert [(p.time, p.queue_length, p.busy_counters) for p in r.steps] == [
        (0, 0, 1), (1, 1, 1), (2, 2, 1), (3, 3, 1), (4, 2, 1), (7, 1, 1), (9, 0, 1), (10, 1, 1),
        (14, 0, 1), (15, 0, 0),
    ]  # fmt: skip
    assert queue_integral(r, 0, 20) == 18.0  # spec 7.7: average queue 0.9
    assert sum(x.completion - x.service_start for x in r.outcomes) == 15.0  # utilization 0.75


class BadPolicy:
    key, version = "bad", "0"

    def select(self, waiting: Sequence[WaitingVisitor], ctx: DecisionContext) -> Selection:
        return Selection(999, "nonsense")


def test_policy_returning_foreign_id_raises():
    with pytest.raises(PolicyError):
        simulate(scripted([(0, 1)]), EngineParams(1, 5.0), BadPolicy())


def test_policy_sees_no_future_information():
    seen: list[WaitingVisitor] = []

    class Spy(FcfsPolicy):
        def select(self, waiting, ctx):  # type: ignore[no-untyped-def]
            seen.extend(waiting)
            return super().select(waiting, ctx)

    simulate(scripted([(0, 1), (0, 1, 3.0)]), EngineParams(1, 5.0), Spy())
    assert seen and not any(hasattr(w, "service_minutes") or hasattr(w, "patience_minutes") for w in seen)


def test_input_validation():
    with pytest.raises(ValueError, match="arrival outside"):
        run([(20.0, 1)], h=20.0)
    with pytest.raises(ValueError):
        run([(0, 0)])
    with pytest.raises(ValueError):
        simulate(scripted([(0, 1)]), EngineParams(0, 5.0), FcfsPolicy())


def test_recorder_rejects_illegal_transitions():
    vs = (VisitorSpec(0, 1.0, "a", 1, 1.0, INF, False),)
    rec = Recorder(vs, True)
    with pytest.raises(LifecycleError):
        rec.transition(1.0, 0, S.COMPLETED)  # PENDING -> COMPLETED
    rec.transition(1.0, 0, S.WAITING, "ARRIVE")
    with pytest.raises(LifecycleError):
        rec.transition(0.5, 0, S.IN_SERVICE)  # before arrival
    rec.transition(1.0, 0, S.IN_SERVICE, "START")
    with pytest.raises(LifecycleError):
        rec.transition(2.0, 0, S.IN_SERVICE)  # no second service
    rec.transition(2.0, 0, S.COMPLETED, "COMPLETE")
    with pytest.raises(LifecycleError):
        rec.transition(3.0, 0, S.ABANDONED)


# ---- property / invariant tests (independent recomputation from outcomes) -----------------
halves = st.integers(0, 39).map(lambda i: i / 2.0)
rows_st = st.lists(
    st.tuples(halves, st.integers(1, 10).map(lambda i: i / 2.0),
              st.one_of(st.just(INF), st.integers(1, 12).map(lambda i: i / 2.0))),
    min_size=0, max_size=30,
).map(lambda rows: sorted(rows, key=lambda r: r[0]))  # fmt: skip


@settings(max_examples=300, deadline=None)
@given(rows=rows_st, c=st.integers(1, 3), cap=st.one_of(st.none(), st.integers(0, 3)))
def test_invariants(rows, c, cap):
    r = run(rows, c=c, h=25.0, cap=cap)
    o = r.outcomes
    assert len(o) == len(rows)
    served = [x for x in o if x.service_start is not None]
    assert all(x.service_start >= x.arrival_time for x in served)  # W >= 0
    for x in o:
        spec = rows[x.visitor_id]
        if x.completion is not None:
            assert x.completion == x.service_start + spec[1]
        assert (x.status is S.COMPLETED) == (x.completion is not None)
        assert not (x.service_start is not None and x.abandonment is not None)  # one fate only
    # FCFS: service-start order follows (arrival, id)
    order = sorted(served, key=lambda x: (x.service_start, x.visitor_id))
    assert [x.visitor_id for x in order] == sorted(x.visitor_id for x in served)
    # independent recomputation of post-settle queue length and busy counters
    for p in r.steps:
        q = sum(
            1 for x in o
            if x.status is not S.BLOCKED and x.arrival_time <= p.time
            and (x.service_start is None or x.service_start > p.time)
            and (x.abandonment is None or x.abandonment > p.time)
        )  # fmt: skip
        b = sum(1 for x in served if x.service_start <= p.time and (x.completion is None or x.completion > p.time))
        assert (p.queue_length, p.busy_counters) == (q, b) and b <= c
    if cap is not None:
        assert all(p.queue_length <= cap for p in r.steps)
    assert [s.time for s in r.steps] == sorted({s.time for s in r.steps})


def test_arrival_and_completion_same_instant_phase_order():
    r = run([(0, 4), (4, 1)], c=1)
    assert by_id(r)[1].service_start == 4.0  # arrival uses the counter freed at the same instant
    assert [e.kind for e in r.trace if e.time == 4.0] == ["COMPLETE", "ARRIVE", "START"]
