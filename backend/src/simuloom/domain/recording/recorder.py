"""Append-only in-memory run record with lifecycle validation (AC-04)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from simuloom.domain.workload.generator import VisitorSpec


class VisitorState(StrEnum):
    PENDING = "PENDING"  # internal, before arrival
    WAITING = "WAITING"  # internal
    IN_SERVICE = "IN_SERVICE"  # internal
    NO_SHOW = "NO_SHOW"
    BLOCKED = "BLOCKED"
    ABANDONED = "ABANDONED"
    COMPLETED = "COMPLETED"
    IN_SERVICE_AT_HORIZON = "IN_SERVICE_AT_HORIZON"
    WAITING_AT_HORIZON = "WAITING_AT_HORIZON"


_ALLOWED: dict[VisitorState, frozenset[VisitorState]] = {
    VisitorState.PENDING: frozenset({VisitorState.WAITING, VisitorState.NO_SHOW}),
    VisitorState.WAITING: frozenset(
        {
            VisitorState.IN_SERVICE,
            VisitorState.ABANDONED,
            VisitorState.BLOCKED,
            VisitorState.WAITING_AT_HORIZON,
        }
    ),
    VisitorState.IN_SERVICE: frozenset({VisitorState.COMPLETED, VisitorState.IN_SERVICE_AT_HORIZON}),
}


class LifecycleError(RuntimeError):
    pass


class TraceEvent(NamedTuple):
    seq: int
    time: float
    kind: str  # ARRIVE NOSHOW START COMPLETE ABANDON BLOCK
    visitor_id: int


class StepPoint(NamedTuple):
    """Post-settle state, right-continuous: valid from ``time`` until the next point."""

    time: float
    queue_length: int
    busy_counters: int


@dataclass(frozen=True, slots=True)
class VisitorOutcome:
    visitor_id: int
    group_key: str
    arrival_time: float
    status: VisitorState
    service_start: float | None
    completion: float | None
    abandonment: float | None


@dataclass(frozen=True)
class RunRecord:
    outcomes: tuple[VisitorOutcome, ...]
    steps: tuple[StepPoint, ...]
    trace: tuple[TraceEvent, ...]
    horizon_minutes: float
    counters: int
    policy_key: str
    policy_version: str
    workload_fingerprint: str
    settle_count: int
    policy_flags: tuple[tuple[str, int], ...] = ()


class Recorder:
    def __init__(self, visitors: tuple[VisitorSpec, ...], record_trace: bool) -> None:
        self._spec = {v.visitor_id: v for v in visitors}
        self._state = {v.visitor_id: VisitorState.PENDING for v in visitors}
        self._b: dict[int, float] = {}
        self._c: dict[int, float] = {}
        self._z: dict[int, float] = {}
        self._steps: list[StepPoint] = [StepPoint(0.0, 0, 0)]
        self._trace: list[TraceEvent] = []
        self._record_trace = record_trace
        self._seq = 0

    def state(self, vid: int) -> VisitorState:
        return self._state[vid]

    def transition(self, t: float, vid: int, new: VisitorState, kind: str | None = None) -> None:
        old = self._state[vid]
        if new not in _ALLOWED.get(old, frozenset()):
            raise LifecycleError(f"visitor {vid}: illegal transition {old} -> {new}")
        if t < self._spec[vid].arrival_time:
            raise LifecycleError(f"visitor {vid}: event time {t} precedes arrival")
        self._state[vid] = new
        if new is VisitorState.IN_SERVICE:
            self._b[vid] = t
        elif new is VisitorState.COMPLETED:
            self._c[vid] = t
        elif new is VisitorState.ABANDONED:
            self._z[vid] = t
        if self._record_trace and kind is not None:
            self._trace.append(TraceEvent(self._seq, t, kind, vid))
            self._seq += 1

    def step(self, t: float, queue_length: int, busy: int) -> None:
        last = self._steps[-1]
        if t < last.time:
            raise LifecycleError("step time went backwards")
        if t == last.time:  # same timestamp: keep only the final post-settle value
            self._steps[-1] = StepPoint(t, queue_length, busy)
        else:
            self._steps.append(StepPoint(t, queue_length, busy))

    def finalize(self) -> tuple[VisitorOutcome, ...]:
        for vid, st in list(self._state.items()):
            if st is VisitorState.PENDING:
                raise LifecycleError(f"visitor {vid} never arrived before the horizon")
            if st is VisitorState.WAITING:
                self._state[vid] = VisitorState.WAITING_AT_HORIZON
            elif st is VisitorState.IN_SERVICE:
                self._state[vid] = VisitorState.IN_SERVICE_AT_HORIZON
        return tuple(
            VisitorOutcome(
                v,
                self._spec[v].group_key,
                self._spec[v].arrival_time,
                self._state[v],
                self._b.get(v),
                self._c.get(v),
                self._z.get(v),
            )
            for v in sorted(self._spec)
        )

    @property
    def steps(self) -> tuple[StepPoint, ...]:
        return tuple(self._steps)

    @property
    def trace(self) -> tuple[TraceEvent, ...]:
        return tuple(self._trace)
