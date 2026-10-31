"""THROWAWAY spike (Phase 0 spec 4.6): step-driven settle dispatcher on SimPy. Not shipped."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import simpy

INF = math.inf
P_COMPLETE, P_EXPIRE, P_ARRIVE = 1, 2, 3


@dataclass(frozen=True)
class V:
    id: int
    arrival: float
    service: float
    patience: float = INF
    noshow: bool = False
    prio: int = 0


def fcfs(pool: Sequence[V], now: float) -> V:
    return min(pool, key=lambda v: (v.arrival, v.id))


def lifo(pool: Sequence[V], now: float) -> V:
    return max(pool, key=lambda v: (v.arrival, v.id))


def static(pool: Sequence[V], now: float) -> V:
    return min(pool, key=lambda v: (v.prio, v.arrival, v.id))


def idmod(pool: Sequence[V], now: float) -> V:
    return min(pool, key=lambda v: (-(v.id % 3), v.id))


@dataclass
class Result:
    trace: list[tuple[float, str, int]] = field(default_factory=list)
    outcome: dict[int, dict] = field(default_factory=dict)
    settle_log: list[tuple[float, float]] = field(default_factory=list)  # (now, peek at settle)
    max_busy: int = 0
    max_res_count: int = 0
    steps: int = 0
    steps_with_state_change_outside_settle: int = 0


def run(
    visitors: Sequence[V],
    counters: int,
    horizon: float,
    select: Callable[[Sequence[V], float], V] = fcfs,
    capacity: int | None = None,
    creation_order: Sequence[int] | None = None,
) -> Result:
    env = simpy.Environment()
    res = simpy.Resource(env, capacity=counters)
    R = Result()
    byid = {v.id: v for v in visitors}
    inbox: list[tuple[int, int]] = []
    pool: dict[int, V] = {}
    state = {"in_settle": False, "busy": 0}
    for v in visitors:
        assert 0 <= v.arrival < horizon and v.service > 0 and v.patience > 0
        R.outcome[v.id] = {"status": "NEW", "B": None, "C": None, "Z": None}

    def mutate(vid: int, **kw: object) -> None:
        if not state["in_settle"]:
            R.steps_with_state_change_outside_settle += 1
            raise AssertionError("state mutated outside settle")
        R.outcome[vid].update(kw)

    def post(phase: int, vid: int) -> None:
        inbox.append((phase, vid))

    def job(v: V):  # ServiceJob process
        with res.request() as rq:
            assert rq.triggered, "request not granted immediately"
            yield rq
            yield env.timeout(v.service)
        post(P_COMPLETE, v.id)

    def settle(t: float) -> None:
        recs = sorted(inbox)
        inbox.clear()
        state["in_settle"] = True
        R.settle_log.append((t, env.peek()))
        arrivals_now: list[int] = []
        for phase, vid in recs:  # P1, P2, P3 in phase order, id order
            if phase == P_COMPLETE:
                state["busy"] -= 1
                mutate(vid, status="COMPLETED", C=t)
                R.trace.append((t, "COMPLETE", vid))
            elif phase == P_EXPIRE:
                if vid in pool:  # (stale timers already filtered at post time)
                    del pool[vid]
                    mutate(vid, status="ABANDONED", Z=t)
                    R.trace.append((t, "ABANDON", vid))
            else:
                v = byid[vid]
                if v.noshow:
                    mutate(vid, status="NO_SHOW")
                    R.trace.append((t, "NOSHOW", vid))
                    continue
                pool[vid] = v
                arrivals_now.append(vid)
                mutate(vid, status="WAITING")
                R.trace.append((t, "ARRIVE", vid))
                if v.patience != INF:
                    ev = env.timeout(v.patience)
                    ev.callbacks.append(lambda _e, vid=vid: post(P_EXPIRE, vid) if vid in pool else None)
        while state["busy"] < counters and pool:  # P4
            v = select(list(pool.values()), t)
            del pool[v.id]
            state["busy"] += 1
            mutate(v.id, status="IN_SERVICE", B=t)
            R.trace.append((t, "START", v.id))
            env.process(job(v))
        if capacity is not None and len(pool) > capacity:  # P5
            excess = len(pool) - capacity
            cands = sorted((i for i in arrivals_now if i in pool), reverse=True)
            if len(cands) < excess:
                raise AssertionError("capacity overflow not attributable to same-time arrivals")
            for vid in cands[:excess]:
                del pool[vid]
                mutate(vid, status="BLOCKED")
                R.trace.append((t, "BLOCK", vid))
        R.max_busy = max(R.max_busy, state["busy"])
        state["in_settle"] = False
        assert not inbox

    order = list(creation_order) if creation_order is not None else [v.id for v in visitors]
    for vid in order:  # absolute scheduling from t=0 => exact float timestamps
        v = byid[vid]
        ev = env.timeout(v.arrival)
        ev.callbacks.append(lambda _e, vid=vid: post(P_ARRIVE, vid))

    while True:  # NOTE: spec 4.4 pseudo-loop was wrong (final settle could strand new events)
        nxt = env.peek()
        if inbox and nxt > env.now:
            settle(env.now)
            continue
        if nxt > horizon:
            break
        env.step()
        R.steps += 1
        R.max_res_count = max(R.max_res_count, res.count)
        assert res.count <= counters and state["busy"] <= counters
    for _vid, o in R.outcome.items():  # finalize at horizon
        if o["status"] == "WAITING":
            o["status"] = "WAITING_AT_HORIZON"
        elif o["status"] == "IN_SERVICE":
            o["status"] = "IN_SERVICE_AT_HORIZON"
    return R
