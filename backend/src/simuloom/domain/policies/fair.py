"""SimuLoom fairness-constrained policy v0.1 (spec section 8.3).

Score (higher is served first):  Score_i(t) = alpha * w_i/T_ref + beta * q_i + gamma * P_g(t)

* ``w_i = t - A_i`` (minutes) and ``T_ref`` (minutes) give a dimensionless urgency that grows with waiting.
* ``q_i`` in [0, 1] is the priority term (1 = highest priority class).
* ``P_g(t)`` in [0, p_max] is group-fairness pressure: how far the online mean wait ``R_g`` of group g exceeds
  the lowest group's, in units of ``D_scale`` minutes. ``R_g`` includes visitors that already started service,
  abandoned, or are waiting now, so a group whose members are stuck in the queue is noticed before anyone is served.
* Overdue guard: if any visitor has waited ``>= W_guard`` minutes, only overdue visitors are considered and the
  earliest arrival wins.
* Deterministic tie-break: ``(arrival_time, visitor_id)`` ascending; scores are compared after rounding to 9 decimals.

This is an online *heuristic*. It does NOT guarantee any aggregate fairness constraint or any maximum wait; those
are evaluated after the run. The one provable property is bounded overtaking (see ``overtaking_bound_minutes``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simuloom.domain.config.models import ConstraintType, ScenarioConfig
from simuloom.domain.policies.base import DecisionContext, Selection, WaitingVisitor

_ROUND_DECIMALS = 9
_USER_PARAMS = {
    "alpha",
    "beta",
    "gamma",
    "p_max",
    "t_ref_minutes",
    "w_guard_minutes",
    "d_scale_minutes",
    "n_pressure_min",
}


def _pos(name: str, v: float, *, strict: bool) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    if (strict and v <= 0) or (not strict and v < 0):
        raise ValueError(f"{name} must be {'> 0' if strict else '>= 0'}")
    return float(v)


@dataclass(frozen=True)
class FairParams:
    alpha: float = 1.0  # urgency weight (dimensionless, > 0)
    beta: float = 0.25  # priority weight (dimensionless, >= 0)
    gamma: float = 0.5  # group-pressure weight (dimensionless, >= 0)
    p_max: float = 2.0  # pressure cap (dimensionless, > 0)
    t_ref: float = 15.0  # urgency normaliser, minutes (> 0)
    d_scale: float = 15.0  # pressure normaliser, minutes (> 0)
    w_guard: float = 45.0  # overdue threshold, minutes (> 0)
    n_pressure_min: int = 30  # observations a group needs before it exerts/receives pressure
    c_min: int = 1  # smallest priority class among enabled groups
    c_max: int = 1  # largest priority class among enabled groups

    def __post_init__(self) -> None:
        _pos("alpha", self.alpha, strict=True)
        _pos("beta", self.beta, strict=False)
        _pos("gamma", self.gamma, strict=False)
        _pos("p_max", self.p_max, strict=True)
        _pos("t_ref", self.t_ref, strict=True)
        _pos("d_scale", self.d_scale, strict=True)
        _pos("w_guard", self.w_guard, strict=True)
        if self.n_pressure_min < 1:
            raise ValueError("n_pressure_min must be >= 1")
        if self.c_min > self.c_max:
            raise ValueError("c_min must be <= c_max")

    def as_dict(self) -> dict[str, float | int]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def resolve_fair_params(params: Mapping[str, Any], scenario: ScenarioConfig) -> tuple[FairParams, list[str]]:
    """Merge user parameters with scenario-derived defaults. Returns (params, notes on defaults applied)."""
    unknown = sorted(set(params) - _USER_PARAMS)
    if unknown:
        raise ValueError(f"simuloom_fair: unknown parameters {unknown}")
    notes: list[str] = []
    theta = scenario.service_level.threshold_minutes
    enabled = [c for c in scenario.constraints if c.enabled]
    if "t_ref_minutes" in params:
        t_ref = _pos("t_ref_minutes", params["t_ref_minutes"], strict=True)
    else:
        t_ref = theta
        notes.append(f"t_ref defaulted to the service-level threshold ({theta} min)")
    if "d_scale_minutes" in params:
        d_scale = _pos("d_scale_minutes", params["d_scale_minutes"], strict=True)
    else:
        limits = [c.limit for c in enabled if c.type is ConstraintType.MAX_MEAN_WAIT_DISPARITY and c.limit > 0]
        if limits:
            d_scale = min(limits)
            notes.append(f"d_scale defaulted to the smallest mean-wait disparity limit ({d_scale} min)")
        else:
            d_scale = t_ref
            notes.append(f"d_scale fell back to t_ref ({t_ref} min): no positive mean-wait disparity limit configured")
    if "w_guard_minutes" in params:
        w_guard = _pos("w_guard_minutes", params["w_guard_minutes"], strict=True)
    else:
        lims = [c.limit for c in enabled if c.type is ConstraintType.MAX_INDIVIDUAL_WAIT and c.limit > 0]
        if lims:
            w_guard = min(lims)
            notes.append(f"w_guard defaulted to the smallest individual-wait limit ({w_guard} min)")
        else:
            w_guard = 3.0 * theta
            notes.append(f"w_guard defaulted to 3 x the service-level threshold ({w_guard} min)")
    n_min = int(params["n_pressure_min"]) if "n_pressure_min" in params else scenario.min_sample.group_n
    classes = [g.priority_class for g in scenario.enabled_groups()]
    kw: dict[str, float] = {
        k: _pos(k, params[k], strict=k in ("alpha", "p_max")) for k in ("alpha", "beta", "gamma", "p_max") if k in params
    }
    fp = FairParams(
        t_ref=t_ref,
        d_scale=d_scale,
        w_guard=w_guard,
        n_pressure_min=n_min,
        c_min=min(classes),
        c_max=max(classes),
        **kw,
    )
    return fp, notes


class SimuLoomFairPolicy:
    key = "simuloom_fair"
    version = "0.1.0"

    def __init__(self, params: FairParams, notes: Sequence[str] = ()) -> None:
        self.params = params
        self.notes = tuple(notes)

    @property
    def overtaking_bound_minutes(self) -> float:
        """tau = T_ref (beta + gamma p_max) / alpha: a visitor can only be overtaken by arrivals at most
        tau minutes later (proved from the score definition; bounds overtaking, NOT waiting)."""
        p = self.params
        return p.t_ref * (p.beta + p.gamma * p.p_max) / p.alpha

    def _priority_term(self, priority_class: int) -> float:
        p = self.params
        if p.c_max == p.c_min:
            return 0.0
        return (p.c_max - priority_class) / (p.c_max - p.c_min)

    def select(self, waiting: Sequence[WaitingVisitor], ctx: DecisionContext) -> Selection:
        p, now = self.params, ctx.now
        overdue = [v for v in waiting if now - v.arrival_time >= p.w_guard]
        if overdue:  # tier 1: individual protection, earliest arrival first
            chosen = min(overdue, key=lambda v: (v.arrival_time, v.visitor_id))
            flags = ["GUARD_SELECTED"]
            if len(overdue) > ctx.idle_counters:
                flags.append("GUARD_SATURATED")
            return Selection(chosen.visitor_id, f"guard: {len(overdue)} overdue, earliest arrival", tuple(flags))

        sums = {g: s.closed_wait_sum for g, s in ctx.group_stats.items()}
        cnts = {g: s.closed_count for g, s in ctx.group_stats.items()}
        for v in waiting:
            sums[v.group_key] = sums.get(v.group_key, 0.0) + (now - v.arrival_time)
            cnts[v.group_key] = cnts.get(v.group_key, 0) + 1
        mean_wait = {g: sums[g] / cnts[g] for g in cnts if cnts[g] > 0}
        eligible = {g: m for g, m in mean_wait.items() if cnts[g] >= p.n_pressure_min}
        r_ref = min(eligible.values()) if eligible else 0.0
        pressure = {g: min(p.p_max, max(0.0, (m - r_ref) / p.d_scale)) for g, m in eligible.items()}
        waiting_groups = {v.group_key for v in waiting}
        flags_l: list[str] = []
        if any(g not in eligible for g in waiting_groups):
            flags_l.append("PRESSURE_UNAVAILABLE")

        def score(v: WaitingVisitor) -> float:
            return (
                p.alpha * (now - v.arrival_time) / p.t_ref
                + p.beta * self._priority_term(v.priority_class)
                + p.gamma * pressure.get(v.group_key, 0.0)
            )

        chosen = min(waiting, key=lambda v: (-round(score(v), _ROUND_DECIMALS), v.arrival_time, v.visitor_id))
        reason = f"score={score(chosen):.6f} group={chosen.group_key} pressure={pressure.get(chosen.group_key, 0.0):.4f}"
        return Selection(chosen.visitor_id, reason, tuple(flags_l))
