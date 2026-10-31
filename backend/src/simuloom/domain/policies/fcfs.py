"""First-Come, First-Served: key (arrival_time, visitor_id) ascending."""

from __future__ import annotations

from collections.abc import Sequence

from simuloom.domain.policies.base import DecisionContext, Selection, WaitingVisitor


class FcfsPolicy:
    key = "fcfs"
    version = "1.0.0"

    def select(self, waiting: Sequence[WaitingVisitor], ctx: DecisionContext) -> Selection:
        chosen = min(waiting, key=lambda v: (v.arrival_time, v.visitor_id))
        return Selection(chosen.visitor_id, "earliest (arrival_time, visitor_id)")
