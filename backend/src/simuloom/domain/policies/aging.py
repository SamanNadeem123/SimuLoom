"""Aging priority: waiting improves a visitor's effective priority linearly.

    p_eff_i(t) = priority_class_i - r * w_i(t),   w_i(t) = t - arrival_time_i   (minutes)

``r`` = ``aging_rate_per_minute`` (priority-class units per minute, r >= 0). The smallest
``p_eff`` is served first. Ties (after rounding ``p_eff`` to 9 decimals so float noise cannot
decide a result) are broken by (arrival_time, visitor_id) ascending.

Limits: r = 0 is identical to static priority; as r grows the order approaches FCFS.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from simuloom.domain.policies.base import DecisionContext, Selection, WaitingVisitor

_ROUND_DECIMALS = 9


class AgingPriorityPolicy:
    key = "aging_priority"
    version = "1.0.0"

    def __init__(self, aging_rate_per_minute: float) -> None:
        if isinstance(aging_rate_per_minute, bool) or not isinstance(aging_rate_per_minute, int | float):
            raise ValueError("aging_rate_per_minute must be a number")
        if not math.isfinite(aging_rate_per_minute) or aging_rate_per_minute < 0:
            raise ValueError("aging_rate_per_minute must be finite and >= 0")
        self.aging_rate_per_minute = float(aging_rate_per_minute)

    def effective_priority(self, visitor: WaitingVisitor, now: float) -> float:
        return visitor.priority_class - self.aging_rate_per_minute * (now - visitor.arrival_time)

    def select(self, waiting: Sequence[WaitingVisitor], ctx: DecisionContext) -> Selection:
        chosen = min(
            waiting,
            key=lambda v: (
                round(self.effective_priority(v, ctx.now), _ROUND_DECIMALS),
                v.arrival_time,
                v.visitor_id,
            ),
        )
        return Selection(chosen.visitor_id, "lowest (round(p_eff, 9), arrival_time, visitor_id)")
