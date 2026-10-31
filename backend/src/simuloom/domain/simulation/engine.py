"""SimPy-backed simulation engine: step-driven settle dispatcher (spec 4.4, verified by spikes/).

SimPy events at a timestamp only append records to an inbox. After every SimPy event at that
timestamp has been processed (``peek() > now``), one settle step applies, in fixed order:
P1 completions, P2 patience expiries, P3 arrivals, P4 dispatch, P5 capacity overflow, P6 snapshot.
Insertion-id order in SimPy therefore never influences results.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

import simpy

from simuloom.domain.policies.base import (
    DecisionContext,
    GroupWaitStats,
    SchedulingPolicy,
    WaitingVisitor,
)
from simuloom.domain.recording.recorder import Recorder, RunRecord, VisitorState
from simuloom.domain.workload.generator import VisitorSpec, Workload

_P_COMPLETE, _P_EXPIRE, _P_ARRIVE = 1, 2, 3


class EngineError(RuntimeError):
    """Internal invariant violated (never swallowed)."""


class PolicyError(EngineError):
    """A policy returned something other than a member of the waiting set."""


@dataclass(frozen=True)
class EngineParams:
    counters: int
    horizon_minutes: float
    queue_capacity: int | None = None


def simulate(
    workload: Workload,
    params: EngineParams,
    policy: SchedulingPolicy,
    *,
    record_trace: bool = False,
) -> RunRecord:
    if params.counters < 1 or params.horizon_minutes <= 0:
        raise ValueError("counters must be >= 1 and horizon > 0")
    env = simpy.Environment()
    slots = simpy.Resource(env, capacity=params.counters)
    rec = Recorder(workload.visitors, record_trace)
    specs: dict[int, VisitorSpec] = {}
    for v in workload.visitors:
        if not (0.0 <= v.arrival_time < params.horizon_minutes):
            raise ValueError(f"visitor {v.visitor_id}: arrival outside [0, horizon)")
        if v.service_minutes <= 0 or v.patience_minutes <= 0:
            raise ValueError(f"visitor {v.visitor_id}: service and patience must be > 0")
        specs[v.visitor_id] = v
    inbox: list[tuple[int, int]] = []
    pool: dict[int, WaitingVisitor] = {}
    busy = 0
    settles = 0
    closed_sum: dict[str, float] = {}
    closed_cnt: dict[str, int] = {}
    flag_counts: Counter[str] = Counter()
    in_settle = False

    def post(phase: int, vid: int) -> None:
        inbox.append((phase, vid))

    def cb(phase: int, vid: int, guard_pool: bool = False) -> Callable[[Any], None]:
        def _cb(_e: Any) -> None:
            if not guard_pool or vid in pool:  # stale patience timers are ignored
                post(phase, vid)

        return _cb

    def job(vid: int) -> Generator[Any, Any, None]:
        with slots.request() as req:
            if not req.triggered:
                raise EngineError("counter slot not immediately available")
            yield req
            yield env.timeout(specs[vid].service_minutes)
        post(_P_COMPLETE, vid)

    def settle(t: float) -> None:
        nonlocal busy, settles, in_settle
        recs = sorted(inbox)
        inbox.clear()
        in_settle = True
        settles += 1
        new_arrivals: list[int] = []
        for phase, vid in recs:
            if phase == _P_COMPLETE:  # P1
                busy -= 1
                rec.transition(t, vid, VisitorState.COMPLETED, "COMPLETE")
            elif phase == _P_EXPIRE:  # P2 (stale expiries were filtered when posted)
                if vid in pool:
                    w = pool.pop(vid)
                    rec.transition(t, vid, VisitorState.ABANDONED, "ABANDON")
                    closed_sum[w.group_key] = closed_sum.get(w.group_key, 0.0) + (t - w.arrival_time)
                    closed_cnt[w.group_key] = closed_cnt.get(w.group_key, 0) + 1
            else:  # P3
                v = specs[vid]
                if v.no_show:
                    rec.transition(t, vid, VisitorState.NO_SHOW, "NOSHOW")
                    continue
                rec.transition(t, vid, VisitorState.WAITING, "ARRIVE")
                pool[vid] = WaitingVisitor(vid, v.arrival_time, v.group_key, v.priority_class)
                new_arrivals.append(vid)
                if math.isfinite(v.patience_minutes):
                    ev = env.timeout(v.patience_minutes)
                    ev.callbacks.append(cb(_P_EXPIRE, vid, guard_pool=True))
        while busy < params.counters and pool:  # P4
            waiting = sorted(pool.values(), key=lambda w: w.visitor_id)
            stats = {g: GroupWaitStats(closed_sum[g], closed_cnt[g]) for g in closed_cnt}
            sel = policy.select(waiting, DecisionContext(t, params.counters - busy, stats))
            if sel.visitor_id not in pool:
                raise PolicyError(f"{policy.key} selected id {sel.visitor_id} not in waiting set")
            flag_counts.update(sel.flags)
            chosen = pool.pop(sel.visitor_id)
            closed_sum[chosen.group_key] = closed_sum.get(chosen.group_key, 0.0) + (t - chosen.arrival_time)
            closed_cnt[chosen.group_key] = closed_cnt.get(chosen.group_key, 0) + 1
            busy += 1
            rec.transition(t, sel.visitor_id, VisitorState.IN_SERVICE, "START")
            env.process(job(sel.visitor_id))
        cap = params.queue_capacity
        if cap is not None and len(pool) > cap:  # P5
            excess = len(pool) - cap
            cands = sorted((i for i in new_arrivals if i in pool), reverse=True)
            if len(cands) < excess:
                raise EngineError("queue overflow not attributable to same-timestamp arrivals")
            for vid in cands[:excess]:
                del pool[vid]
                rec.transition(t, vid, VisitorState.BLOCKED, "BLOCK")
        rec.step(t, len(pool), busy)  # P6
        in_settle = False
        if inbox:
            raise EngineError("inbox not empty after settle")

    for v in workload.visitors:  # absolute scheduling from t=0 keeps float timestamps exact
        env.timeout(v.arrival_time).callbacks.append(cb(_P_ARRIVE, v.visitor_id))

    horizon = params.horizon_minutes
    while True:
        nxt = env.peek()
        if inbox and nxt > env.now:
            settle(env.now)
            continue
        if nxt > horizon:
            break
        env.step()
        if slots.count > params.counters or busy > params.counters:
            raise EngineError("counter capacity exceeded")
    if in_settle:
        raise EngineError("engine stopped inside settle")
    outcomes = rec.finalize()
    return RunRecord(
        outcomes=outcomes,
        steps=rec.steps,
        trace=rec.trace,
        horizon_minutes=horizon,
        counters=params.counters,
        policy_key=policy.key,
        policy_version=policy.version,
        workload_fingerprint=workload.fingerprint,
        settle_count=settles,
        policy_flags=tuple(sorted(flag_counts.items())),
    )
