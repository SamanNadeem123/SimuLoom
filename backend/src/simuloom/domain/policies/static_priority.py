"""Static priority: lower ``priority_class`` number is served first (1 = highest priority).

Selection key, ascending: (priority_class, arrival_time, visitor_id).
Known limitation (documented in the spec): low-priority visitors can starve while
higher-priority visitors keep arriving.
"""

from __future__ import annotations

from collections.abc import Sequence

from simuloom.domain.policies.base import DecisionContext, Selection, WaitingVisitor


class StaticPriorityPolicy:
    key = "static_priority"
    version = "1.0.0"

    def select(self, waiting: Sequence[WaitingVisitor], ctx: DecisionContext) -> Selection:
        chosen = min(waiting, key=lambda v: (v.priority_class, v.arrival_time, v.visitor_id))
        return Selection(chosen.visitor_id, "lowest (priority_class, arrival_time, visitor_id)")
