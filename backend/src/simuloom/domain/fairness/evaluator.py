"""Per-run constraint evaluation (spec 7.4-7.6). Consumes RunMetrics; never simulates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations
from typing import Any

from simuloom.domain.config.models import ConstraintConfig, ConstraintType, ScenarioConfig
from simuloom.domain.metrics.compute import RunMetrics
from simuloom.domain.metrics.types import (
    ALL_GROUPS,
    BASELINE_TOO_SMALL,
    INSUFFICIENT_GROUPS,
    NO_OBSERVATIONS,
    MetricValue,
)


class ConstraintStatus(StrEnum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


UPPER = "<="
LOWER = ">="

_COMPARATOR: dict[ConstraintType, str] = {
    ConstraintType.MAX_MEAN_WAIT_DISPARITY: UPPER,
    ConstraintType.MAX_RELATIVE_WAIT_DISPARITY: UPPER,
    ConstraintType.MAX_SLA_GAP: UPPER,
    ConstraintType.MAX_LONG_WAIT_RATE: UPPER,
    ConstraintType.MIN_SLA_ATTAINMENT: LOWER,
    ConstraintType.MAX_INDIVIDUAL_WAIT: UPPER,
}
_PER_GROUP = {ConstraintType.MAX_LONG_WAIT_RATE, ConstraintType.MIN_SLA_ATTAINMENT}
_ONLINE_ATTEMPT = {ConstraintType.MAX_MEAN_WAIT_DISPARITY, ConstraintType.MAX_INDIVIDUAL_WAIT}
FAIR_POLICY_KEY = "simuloom_fair"


def comparator_for(ctype: ConstraintType) -> str:
    return _COMPARATOR[ctype]


def is_per_group(ctype: ConstraintType) -> bool:
    return ctype in _PER_GROUP


def violation_magnitude(observed: float, limit: float, comparator: str) -> float:
    """Spec 7.4 F: upper bound max(0, obs - limit); lower bound max(0, limit - obs)."""
    return max(0.0, observed - limit) if comparator == UPPER else max(0.0, limit - observed)


def attempted_online(ctype: ConstraintType, policy_key: str) -> bool:
    return policy_key == FAIR_POLICY_KEY and ctype in _ONLINE_ATTEMPT


def fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} min" if unit == "minute" else f"{value:.3f}"


@dataclass(frozen=True)
class ConstraintResult:
    """Evaluation of one constraint on one policy run (or one group of it)."""

    constraint_id: str
    type: ConstraintType
    group_key: str
    status: ConstraintStatus
    reason_code: str | None
    observed: float | None
    limit: float
    unit: str
    comparator: str
    violation_magnitude: float | None
    attempted_online: bool
    explanation: str
    details: dict[str, Any] = field(default_factory=dict)


def _decide(
    c: ConstraintConfig, group_key: str, observed: float, policy_key: str, what: str, details: dict[str, Any]
) -> ConstraintResult:
    comp = _COMPARATOR[c.type]
    mag = violation_magnitude(observed, c.limit, comp)
    status = ConstraintStatus.VIOLATED if mag > 0 else ConstraintStatus.SATISFIED
    word = "violates" if mag > 0 else "satisfies"
    expl = (
        f"{what} was {fmt(observed, c.unit)} against a limit of {comp} {fmt(c.limit, c.unit)}: "
        f"{word} the limit (violation {fmt(mag, c.unit)}). Evaluated after the run; no guarantee is made."
    )
    return ConstraintResult(
        c.id, c.type, group_key, status, None, observed, c.limit, c.unit, comp, mag,
        attempted_online(c.type, policy_key), expl, details,
    )  # fmt: skip


def _not_evaluable(
    c: ConstraintConfig, group_key: str, reason: str, why: str, policy_key: str, details: dict[str, Any] | None = None
) -> ConstraintResult:
    return ConstraintResult(
        c.id, c.type, group_key, ConstraintStatus.NOT_EVALUABLE, reason, None, c.limit, c.unit,
        _COMPARATOR[c.type], None, attempted_online(c.type, policy_key), f"Not evaluable: {why}.", details or {},
    )  # fmt: skip


def _group_values(rm: RunMetrics, key: str, group_keys: Sequence[str]) -> tuple[dict[str, float], dict[str, str]]:
    ok: dict[str, float] = {}
    missing: dict[str, str] = {}
    for g in group_keys:
        mv: MetricValue | None = rm.groups.get(g, {}).get(key)
        if mv is not None and mv.evaluable and mv.value is not None:
            ok[g] = mv.value
        else:
            missing[g] = mv.reason if mv is not None and mv.reason else NO_OBSERVATIONS
    return ok, missing


def _pairwise(c: ConstraintConfig, rm: RunMetrics, config: ScenarioConfig, policy_key: str) -> ConstraintResult:
    gkeys = [g.key for g in config.enabled_groups()]
    if c.type is ConstraintType.MAX_SLA_GAP:
        metric_key, label = "sla_attainment", "service-level attainment gap"
    elif c.wait_basis == "INCLUDING_CENSORED":
        metric_key, label = "wait_mean_incl_censored", "group mean-wait difference (including censored waits)"
    else:
        metric_key, label = "wait_mean", "group mean-wait difference"
    vals, missing = _group_values(rm, metric_key, gkeys)
    det: dict[str, Any] = {"metric": metric_key, "group_values": vals, "missing_groups": missing}
    if len(vals) < 2:
        why = f"fewer than two evaluable groups (missing: {', '.join(f'{g}={r}' for g, r in missing.items()) or 'none'})"
        return _not_evaluable(c, ALL_GROUPS, INSUFFICIENT_GROUPS, why, policy_key, det)
    eps = config.statistics.relative_baseline_epsilon_minutes
    best: tuple[float, str, str] | None = None
    skipped: list[str] = []
    for g, h in combinations([k for k in gkeys if k in vals], 2):
        a, b = vals[g], vals[h]
        if c.type is ConstraintType.MAX_RELATIVE_WAIT_DISPARITY:
            if max(a, b) < eps:
                skipped.append(f"{g}/{h}")
                continue
            d = abs(a - b) / max(a, b)
        else:
            d = abs(a - b)
        if best is None or d > best[0]:
            best = (d, g, h)
    det["skipped_pairs_baseline_too_small"] = skipped
    if best is None:
        return _not_evaluable(
            c, ALL_GROUPS, BASELINE_TOO_SMALL, f"every group pair has a baseline below {eps} min", policy_key, det
        )
    d, g, h = best
    det["pair"] = [g, h]
    what = f"The largest pairwise {label} ({g} vs {h})"
    if skipped:
        what += f" (pairs {', '.join(skipped)} skipped: baseline too small)"
    if missing:
        what += f" (groups not evaluable: {', '.join(missing)})"
    return _decide(c, ALL_GROUPS, d, policy_key, what, det)


def _individual(c: ConstraintConfig, rm: RunMetrics, policy_key: str) -> ConstraintResult:
    mv = rm.individual_max_wait
    if not mv.evaluable or mv.value is None:
        return _not_evaluable(c, ALL_GROUPS, mv.reason or NO_OBSERVATIONS, "no eligible visitor waits were observed", policy_key)
    return _decide(
        c, ALL_GROUPS, mv.value, policy_key,
        "The longest realised or elapsed wait of any eligible visitor", {"n": mv.n},
    )  # fmt: skip


def combine_group_rows(rows: Sequence[ConstraintResult]) -> ConstraintResult:
    """Combine per-group rows of one constraint into the ``__all__`` row (spec 7.5)."""
    first = rows[0]
    evaluable = [r for r in rows if r.status is not ConstraintStatus.NOT_EVALUABLE]
    skipped = [r.group_key for r in rows if r.status is ConstraintStatus.NOT_EVALUABLE]
    det: dict[str, Any] = {
        "group_status": {r.group_key: r.status.value for r in rows},
        "group_observed": {r.group_key: r.observed for r in rows},
        "not_evaluable_groups": skipped,
    }
    if not evaluable:
        return ConstraintResult(
            first.constraint_id, first.type, ALL_GROUPS, ConstraintStatus.NOT_EVALUABLE, NO_OBSERVATIONS, None,
            first.limit, first.unit, first.comparator, None, first.attempted_online,
            "Not evaluable: no group had enough observations.", det,
        )  # fmt: skip
    violated = [r for r in evaluable if r.status is ConstraintStatus.VIOLATED]
    if violated:
        pick = max(violated, key=lambda r: r.violation_magnitude or 0.0)
        status = ConstraintStatus.VIOLATED
    else:
        pick = (
            max(evaluable, key=lambda r: r.observed or 0.0)
            if first.comparator == UPPER
            else min(evaluable, key=lambda r: r.observed or 0.0)
        )
        status = ConstraintStatus.SATISFIED
    expl = (
        f"{len(violated)} of {len(evaluable)} evaluable groups violate the limit {first.comparator} "
        f"{fmt(first.limit, first.unit)}; worst/closest group {pick.group_key} at {fmt(pick.observed, first.unit)}."
    )
    if skipped:
        expl += f" Groups not evaluable: {', '.join(skipped)}."
    return ConstraintResult(
        first.constraint_id, first.type, ALL_GROUPS, status, None, pick.observed, first.limit, first.unit,
        first.comparator, pick.violation_magnitude, first.attempted_online, expl, det,
    )  # fmt: skip


def _per_group(c: ConstraintConfig, rm: RunMetrics, config: ScenarioConfig, policy_key: str) -> list[ConstraintResult]:
    metric_key = "long_wait_rate" if c.type is ConstraintType.MAX_LONG_WAIT_RATE else "sla_attainment"
    label = "long-wait rate" if metric_key == "long_wait_rate" else "service-level attainment"
    gkeys = [g.key for g in config.enabled_groups() if c.group in ("*", g.key)]
    rows: list[ConstraintResult] = []
    for g in gkeys:
        mv = rm.groups.get(g, {}).get(metric_key)
        if mv is None or not mv.evaluable or mv.value is None:
            reason = mv.reason if mv is not None and mv.reason else NO_OBSERVATIONS
            rows.append(_not_evaluable(c, g, reason, f"group {g} {label} ({reason})", policy_key))
        else:
            rows.append(_decide(c, g, mv.value, policy_key, f"The {label} of group {g}", {"n": mv.n}))
    return [*rows, combine_group_rows(rows)]


def evaluate_run_constraints(rm: RunMetrics, config: ScenarioConfig, policy_key: str) -> list[ConstraintResult]:
    """Evaluate every *enabled* constraint on one run. Disabled constraints produce no rows."""
    out: list[ConstraintResult] = []
    for c in config.constraints:
        if not c.enabled:
            continue
        if c.type in _PER_GROUP:
            out.extend(_per_group(c, rm, config, policy_key))
        elif c.type is ConstraintType.MAX_INDIVIDUAL_WAIT:
            out.append(_individual(c, rm, policy_key))
        else:
            out.append(_pairwise(c, rm, config, policy_key))
    return out


Evaluator = Callable[[RunMetrics, ScenarioConfig, str], list[ConstraintResult]]
