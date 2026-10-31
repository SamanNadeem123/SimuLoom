"""Cross-replication aggregation of metrics and constraint results (spec 7.6).

The independent unit is the replication: one value per replication, never per visitor.
All thresholds come from ``StatisticsConfig`` and are engineering defaults, not guarantees.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from simuloom.domain.config.models import StatisticsConfig
from simuloom.domain.fairness.evaluator import (
    LOWER,
    UPPER,
    ConstraintResult,
    ConstraintStatus,
    fmt,
    is_per_group,
    violation_magnitude,
)
from simuloom.domain.metrics.stats import t_interval
from simuloom.domain.metrics.types import (
    ALL_GROUPS,
    EXCESSIVE_EXCLUSION,
    NO_OBSERVATIONS,
    TOO_FEW_REPLICATIONS,
    MetricValue,
)

RULE_MEAN_CI = "MEAN_CI"
RULE_ALL_REPLICATIONS = "ALL_REPLICATIONS"


class Certainty(StrEnum):
    CONFIRMED = "CONFIRMED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_CERT_TEXT = {
    Certainty.CONFIRMED: "interval is entirely on that side of the limit",
    Certainty.INCONCLUSIVE: "inconclusive: the interval straddles the limit",
    Certainty.NOT_APPLICABLE: "",
}


@dataclass(frozen=True)
class MetricAggregate:
    metric_key: str
    group_key: str
    unit: str
    n_valid: int
    n_total: int
    mean: float | None
    std: float | None
    ci_low: float | None
    ci_high: float | None
    ci_level: float
    reason: str | None  # set when mean is None (no valid replication)


def aggregate_metric(values: Sequence[MetricValue], key: str, group_key: str, stats: StatisticsConfig) -> MetricAggregate:
    """Aggregate one metric over replications. CI only when n_valid >= stats.min_replications."""
    unit = values[0].unit if values else ""
    valid = [v.value for v in values if v.evaluable and v.value is not None]
    if not valid:
        reasons = sorted({v.reason for v in values if v.reason})
        return MetricAggregate(key, group_key, unit, 0, len(values), None, None, None, None, stats.ci_level,
                               reasons[0] if reasons else NO_OBSERVATIONS)  # fmt: skip
    iv = t_interval(valid, stats.ci_level, stats.min_replications)
    return MetricAggregate(key, group_key, unit, len(valid), len(values), iv.mean, iv.std, iv.ci_low, iv.ci_high,
                           stats.ci_level, None)  # fmt: skip


@dataclass(frozen=True)
class AggregateConstraintResult:
    constraint_id: str
    type: str
    group_key: str
    rule: str
    status: ConstraintStatus
    reason_code: str | None
    certainty: Certainty
    observed: float | None  # replication mean (MEAN_CI) or worst observed value (ALL_REPLICATIONS)
    limit: float
    unit: str
    comparator: str
    violation_magnitude: float | None
    violation_ci_low: float | None
    violation_ci_high: float | None
    ci_low: float | None
    ci_high: float | None
    ci_level: float | None
    n_valid: int
    n_total: int
    replication_pass_rate: float | None
    zero_variance: bool
    attempted_online: bool
    explanation: str
    details: dict[str, Any] = field(default_factory=dict)


def _certainty(status: ConstraintStatus, comparator: str, lo: float, hi: float, limit: float) -> Certainty:
    if comparator == UPPER:
        ok = hi <= limit if status is ConstraintStatus.SATISFIED else lo > limit
    else:
        ok = lo >= limit if status is ConstraintStatus.SATISFIED else hi < limit
    return Certainty.CONFIRMED if ok else Certainty.INCONCLUSIVE


def aggregate_constraint(per_rep: Sequence[ConstraintResult], stats: StatisticsConfig) -> AggregateConstraintResult:
    """Aggregate one (constraint, group) row over replications, in replication order."""
    if not per_rep:
        raise ValueError("no replication results to aggregate")
    first = per_rep[0]
    n_total = len(per_rep)
    valid = [r for r in per_rep if r.status is not ConstraintStatus.NOT_EVALUABLE and r.observed is not None]
    n_valid = len(valid)
    passes = sum(1 for r in valid if r.status is ConstraintStatus.SATISFIED)
    pass_rate = passes / n_valid if n_valid else None
    rule = RULE_ALL_REPLICATIONS if first.type.value == "MAX_INDIVIDUAL_WAIT" else RULE_MEAN_CI
    common: dict[str, Any] = {
        "constraint_id": first.constraint_id, "type": first.type.value, "group_key": first.group_key,
        "rule": rule, "limit": first.limit, "unit": first.unit, "comparator": first.comparator,
        "n_valid": n_valid, "n_total": n_total, "replication_pass_rate": pass_rate,
        "attempted_online": first.attempted_online,
    }  # fmt: skip

    def ne(reason: str, why: str) -> AggregateConstraintResult:
        return AggregateConstraintResult(
            status=ConstraintStatus.NOT_EVALUABLE, reason_code=reason, certainty=Certainty.NOT_APPLICABLE,
            observed=None, violation_magnitude=None, violation_ci_low=None, violation_ci_high=None,
            ci_low=None, ci_high=None, ci_level=None, zero_variance=False,
            explanation=f"Not evaluable: {why} ({n_valid} of {n_total} replications evaluable).", **common,
        )  # fmt: skip

    if n_valid == 0:
        run_reasons = [r.reason_code for r in per_rep if r.reason_code]
        top = max(set(run_reasons), key=run_reasons.count) if run_reasons else NO_OBSERVATIONS  # most common cause
        return ne(top, "no replication produced an evaluable result")
    if rule == RULE_ALL_REPLICATIONS:
        if n_valid / n_total < stats.min_valid_fraction:
            return ne(EXCESSIVE_EXCLUSION, f"fewer than {stats.min_valid_fraction:.0%} of replications were evaluable")
        violating = [r for r in valid if r.status is ConstraintStatus.VIOLATED]
        worst = max(r.observed for r in valid if r.observed is not None)
        status = ConstraintStatus.VIOLATED if violating else ConstraintStatus.SATISFIED
        mag = violation_magnitude(worst, first.limit, first.comparator)
        expl = (
            f"{len(violating)} of {n_valid} evaluable replications exceeded the limit {first.comparator} "
            f"{fmt(first.limit, first.unit)}; worst observed {fmt(worst, first.unit)}. "
            "Worst-case target: any violating replication marks it violated. No guarantee is made."
        )
        return AggregateConstraintResult(
            status=status, reason_code=None, certainty=Certainty.NOT_APPLICABLE, observed=worst,
            violation_magnitude=mag, violation_ci_low=None, violation_ci_high=None, ci_low=None, ci_high=None,
            ci_level=None, zero_variance=False, explanation=expl,
            details={"violating_replications": len(violating)}, **common,
        )  # fmt: skip
    # ---- MEAN_CI
    if n_valid < stats.min_replications:
        return ne(TOO_FEW_REPLICATIONS, f"at least {stats.min_replications} evaluable replications are required")
    if n_valid / n_total < stats.min_valid_fraction:
        return ne(EXCESSIVE_EXCLUSION, f"fewer than {stats.min_valid_fraction:.0%} of replications were evaluable")
    xs = [r.observed for r in valid if r.observed is not None]
    iv = t_interval(xs, stats.ci_level, stats.min_replications)
    assert iv.ci_low is not None and iv.ci_high is not None
    mag = violation_magnitude(iv.mean, first.limit, first.comparator)
    status = ConstraintStatus.VIOLATED if mag > 0 else ConstraintStatus.SATISFIED
    cert = _certainty(status, first.comparator, iv.ci_low, iv.ci_high, first.limit)
    f_lo = violation_magnitude(iv.ci_low, first.limit, first.comparator)
    f_hi = violation_magnitude(iv.ci_high, first.limit, first.comparator)
    v_lo, v_hi = (min(f_lo, f_hi), max(f_lo, f_hi))
    expl = (
        f"Mean over {n_valid} replications was {fmt(iv.mean, first.unit)} "
        f"({stats.ci_level:.0%} CI {fmt(iv.ci_low, first.unit)} to {fmt(iv.ci_high, first.unit)}) against a limit "
        f"{first.comparator} {fmt(first.limit, first.unit)}: "
        f"{'violates' if mag > 0 else 'satisfies'} the limit at the point estimate "
        f"({_CERT_TEXT[cert]}); "
        f"{passes} of {n_valid} replications individually satisfied it. Evaluated after the run; no guarantee is made."
    )
    return AggregateConstraintResult(
        status=status, reason_code=None, certainty=cert, observed=iv.mean, violation_magnitude=mag,
        violation_ci_low=v_lo, violation_ci_high=v_hi, ci_low=iv.ci_low, ci_high=iv.ci_high, ci_level=stats.ci_level,
        zero_variance=iv.zero_variance, explanation=expl, **common,
    )  # fmt: skip


def combine_aggregate_group_rows(rows: Sequence[AggregateConstraintResult]) -> AggregateConstraintResult:
    """Combine aggregate per-group rows into the ``__all__`` row of a per-group constraint."""
    first = rows[0]
    evaluable = [r for r in rows if r.status is not ConstraintStatus.NOT_EVALUABLE]
    skipped = [r.group_key for r in rows if r.status is ConstraintStatus.NOT_EVALUABLE]
    det: dict[str, Any] = {
        "group_status": {r.group_key: r.status.value for r in rows},
        "group_certainty": {r.group_key: r.certainty.value for r in rows},
        "not_evaluable_groups": skipped,
    }
    base: dict[str, Any] = {
        "constraint_id": first.constraint_id, "type": first.type, "group_key": ALL_GROUPS, "rule": first.rule,
        "limit": first.limit, "unit": first.unit, "comparator": first.comparator,
        "attempted_online": first.attempted_online, "details": det,
    }  # fmt: skip
    if not evaluable:
        return AggregateConstraintResult(
            status=ConstraintStatus.NOT_EVALUABLE, reason_code=NO_OBSERVATIONS, certainty=Certainty.NOT_APPLICABLE,
            observed=None, violation_magnitude=None, violation_ci_low=None, violation_ci_high=None, ci_low=None,
            ci_high=None, ci_level=None, n_valid=0, n_total=first.n_total, replication_pass_rate=None,
            zero_variance=False, explanation="Not evaluable: no group was evaluable.", **base,
        )  # fmt: skip
    violated = [r for r in evaluable if r.status is ConstraintStatus.VIOLATED]
    if violated:
        status = ConstraintStatus.VIOLATED
        pick = max(violated, key=lambda r: r.violation_magnitude or 0.0)
        cert = Certainty.CONFIRMED if any(r.certainty is Certainty.CONFIRMED for r in violated) else Certainty.INCONCLUSIVE
    else:
        status = ConstraintStatus.SATISFIED
        pick = (
            max(evaluable, key=lambda r: r.observed or 0.0)
            if first.comparator == UPPER
            else min(evaluable, key=lambda r: r.observed or 0.0)
        )
        cert = Certainty.CONFIRMED if all(r.certainty is Certainty.CONFIRMED for r in evaluable) else Certainty.INCONCLUSIVE
    expl = (
        f"{len(violated)} of {len(evaluable)} evaluable groups violate the limit {first.comparator} "
        f"{fmt(first.limit, first.unit)} at the replication mean; worst/closest group {pick.group_key} "
        f"({fmt(pick.observed, first.unit)})."
    )
    if skipped:
        expl += f" Groups not evaluable: {', '.join(skipped)}."
    return AggregateConstraintResult(
        status=status, reason_code=None, certainty=cert, observed=pick.observed,
        violation_magnitude=pick.violation_magnitude, violation_ci_low=pick.violation_ci_low,
        violation_ci_high=pick.violation_ci_high, ci_low=pick.ci_low, ci_high=pick.ci_high, ci_level=pick.ci_level,
        n_valid=pick.n_valid, n_total=pick.n_total, replication_pass_rate=pick.replication_pass_rate,
        zero_variance=pick.zero_variance, explanation=expl, **base,
    )  # fmt: skip


def aggregate_constraints(
    per_rep_results: Sequence[Sequence[ConstraintResult]], stats: StatisticsConfig
) -> list[AggregateConstraintResult]:
    """``per_rep_results[r]`` = list of ConstraintResult of replication r (same structure each).

    Per-group constraints yield one aggregate row per group followed by the combined ``__all__`` row;
    every other constraint yields a single ``__all__`` row.
    """
    if not per_rep_results:
        return []
    keys = [(r.constraint_id, r.group_key) for r in per_rep_results[0]]
    by_key: dict[tuple[str, str], list[ConstraintResult]] = {k: [] for k in keys}
    for rep in per_rep_results:
        for r in rep:
            by_key[(r.constraint_id, r.group_key)].append(r)
    out: list[AggregateConstraintResult] = []
    for cid in dict.fromkeys(c for c, _ in keys):
        rows_keys = [k for k in keys if k[0] == cid]
        if is_per_group(by_key[rows_keys[0]][0].type):
            group_aggs = [aggregate_constraint(by_key[k], stats) for k in rows_keys if k[1] != ALL_GROUPS]
            out.extend(group_aggs)
            out.append(combine_aggregate_group_rows(group_aggs))
        else:
            out.extend(aggregate_constraint(by_key[k], stats) for k in rows_keys)
    return out


def policy_label(aggs: Sequence[AggregateConstraintResult]) -> str:
    """Policy-level constraint label (spec 7.6), over the ``__all__`` rows."""
    rows = [a for a in aggs if a.group_key == ALL_GROUPS]
    if not rows:
        return "NO_CONSTRAINTS"
    if any(a.status is ConstraintStatus.VIOLATED for a in rows):
        return "VIOLATES_AT_LEAST_ONE"
    if any(a.status is ConstraintStatus.NOT_EVALUABLE for a in rows):
        return "INCOMPLETE"
    if any(a.certainty is Certainty.INCONCLUSIVE for a in rows):
        return "SATISFIED_INCONCLUSIVE"
    return "SATISFIED_CONFIRMED"


def experiment_label(per_policy: dict[str, Sequence[AggregateConstraintResult]]) -> str:
    """NOT_SATISFIED_BY_ANY_TESTED_POLICY only if every policy has a CONFIRMED violation."""
    if not per_policy:
        return "NO_POLICIES"

    def has_confirmed_violation(aggs: Sequence[AggregateConstraintResult]) -> bool:
        return any(
            a.group_key == ALL_GROUPS and a.status is ConstraintStatus.VIOLATED and a.certainty is Certainty.CONFIRMED
            for a in aggs
        )

    if all(has_confirmed_violation(a) for a in per_policy.values()):
        return "NOT_SATISFIED_BY_ANY_TESTED_POLICY"
    return "AT_LEAST_ONE_POLICY_NOT_REFUTED"


@dataclass(frozen=True)
class PairedComparison:
    metric_key: str
    group_key: str
    unit: str
    n_pairs: int
    mean_difference: float | None  # candidate minus baseline
    ci_low: float | None
    ci_high: float | None
    ci_excludes_zero: bool | None  # None when no interval could be formed


def paired_difference(
    candidate: Sequence[MetricValue], baseline: Sequence[MetricValue], key: str, group_key: str, stats: StatisticsConfig
) -> PairedComparison:
    """Per-replication differences d_r = candidate_r - baseline_r over replications valid in both."""
    if len(candidate) != len(baseline):
        raise ValueError("candidate and baseline must cover the same replications")
    unit = candidate[0].unit if candidate else ""
    d = [
        c.value - b.value
        for c, b in zip(candidate, baseline, strict=True)
        if c.evaluable and b.evaluable and c.value is not None and b.value is not None
    ]
    if not d:
        return PairedComparison(key, group_key, unit, 0, None, None, None, None)
    iv = t_interval(d, stats.ci_level, stats.min_replications)
    excl = None if iv.ci_low is None or iv.ci_high is None else (iv.ci_low > 0 or iv.ci_high < 0)
    return PairedComparison(key, group_key, unit, len(d), iv.mean, iv.ci_low, iv.ci_high, excl)


__all__ = [
    "LOWER", "UPPER", "Certainty", "AggregateConstraintResult", "MetricAggregate", "PairedComparison",
    "aggregate_constraint", "aggregate_constraints", "aggregate_metric", "combine_aggregate_group_rows",
    "experiment_label", "paired_difference", "policy_label",
]  # fmt: skip
