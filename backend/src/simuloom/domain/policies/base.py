"""Common scheduling-policy contract (spec 8.1). Policies are pure and deterministic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WaitingVisitor:
    """What a policy may see: no service duration, patience or no-show flag (not online info)."""

    visitor_id: int
    arrival_time: float
    group_key: str
    priority_class: int  # lower number = served first


@dataclass(frozen=True, slots=True)
class GroupWaitStats:
    """Online statistics of one group: visitors that already started service or abandoned.

    ``closed_wait_sum`` is the sum of their realised waits (minutes); waiting visitors are *not*
    included (a policy adds their elapsed waits from the waiting list it is given).
    """

    closed_wait_sum: float = 0.0
    closed_count: int = 0


@dataclass(frozen=True, slots=True)
class DecisionContext:
    now: float
    idle_counters: int
    group_stats: Mapping[str, GroupWaitStats] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Selection:
    visitor_id: int
    reason: str
    flags: tuple[str, ...] = ()  # machine-readable decision flags, counted per run by the engine


class SchedulingPolicy(Protocol):
    key: str
    version: str

    def select(self, waiting: Sequence[WaitingVisitor], ctx: DecisionContext) -> Selection:
        """Choose exactly one member of ``waiting`` (non-empty). Must not mutate inputs."""
        ...
