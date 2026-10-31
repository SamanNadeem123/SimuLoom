"""Pure experiment runner: replications x policies on identical workloads, then aggregation.

No database, no I/O. The execution layer (child process) calls ``run_replication`` per replication and
``aggregate_experiment`` once at the end. Scheduling (policy.select) and post-run evaluation stay separate.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.fairness.aggregate import (
    AggregateConstraintResult,
    MetricAggregate,
    PairedComparison,
    aggregate_constraints,
    aggregate_metric,
    experiment_label,
    paired_difference,
    policy_label,
)
from simuloom.domain.fairness.evaluator import ConstraintResult, evaluate_run_constraints
from simuloom.domain.metrics.compute import RunMetrics, compute_run_metrics
from simuloom.domain.metrics.types import ALL_GROUPS, MetricValue
from simuloom.domain.policies.registry import create_policy
from simuloom.domain.recording.recorder import StepPoint, VisitorOutcome
from simuloom.domain.simulation.engine import EngineParams, simulate
from simuloom.domain.workload.generator import generate_workload

MAX_POLICIES = 6
MAX_SERIES_POINTS = 600
# metrics compared pairwise against the baseline policy (overall) and per group
PAIRED_OVERALL = ("wait_mean", "wait_p95", "sla_attainment", "abandonment_rate", "utilization", "throughput_per_hour")
PAIRED_GROUP = ("wait_mean", "sla_attainment")


@dataclass(frozen=True)
class PolicySpec:
    label: str
    key: str
    params: Mapping[str, Any] = field(default_factory=dict)


def validate_specs(specs: Sequence[PolicySpec], config: ScenarioConfig) -> None:
    """Raise ValueError on duplicate labels, too many policies, or invalid policy parameters."""
    if not specs:
        raise ValueError("at least one policy is required")
    if len(specs) > MAX_POLICIES:
        raise ValueError(f"at most {MAX_POLICIES} policies per experiment")
    labels = [s.label for s in specs]
    if len(set(labels)) != len(labels):
        raise ValueError("policy labels must be unique")
    for s in specs:
        if not s.label or len(s.label) > 64:
            raise ValueError("policy label must be 1-64 characters")
        try:
            create_policy(s.key, dict(s.params), config)  # validates key and parameters
        except KeyError as exc:
            raise ValueError(str(exc.args[0])) from exc


@dataclass(frozen=True)
class PolicyRepResult:
    label: str
    replication: int
    policy_key: str
    policy_version: str
    metrics: RunMetrics
    constraints: list[ConstraintResult]
    policy_flags: dict[str, int]
    resolution_notes: tuple[str, ...]
    settle_count: int
    outcomes: tuple[VisitorOutcome, ...] | None = None  # kept for replication 0 only
    series: tuple[tuple[float, int, int], ...] | None = None  # (time, queue_length, busy), replication 0 only


@dataclass(frozen=True)
class ReplicationResult:
    replication: int
    workload_fingerprint: str
    visitor_count: int
    results: dict[str, PolicyRepResult]


def downsample_steps(
    steps: Sequence[StepPoint], horizon: float, max_points: int = MAX_SERIES_POINTS
) -> tuple[tuple[float, int, int], ...]:
    """Reduce right-continuous steps to <= max_points by keeping the last point of each time bin."""
    if len(steps) <= max_points:
        return tuple((p.time, p.queue_length, p.busy_counters) for p in steps)
    width = horizon / max_points
    last: dict[int, StepPoint] = {}
    for p in steps:
        last[min(int(p.time / width), max_points - 1)] = p
    return tuple((p.time, p.queue_length, p.busy_counters) for _, p in sorted(last.items()))


def run_replication(
    config: ScenarioConfig, base_seed: int, replication: int, specs: Sequence[PolicySpec], keep_detail: bool = False
) -> ReplicationResult:
    """One workload, every policy. All policies see the identical workload (fingerprint asserted)."""
    workload = generate_workload(config, base_seed, replication)
    params = EngineParams(config.counters, config.horizon_minutes, config.queue_capacity)
    out: dict[str, PolicyRepResult] = {}
    for spec in specs:
        policy = create_policy(spec.key, dict(spec.params), config)
        rec = simulate(workload, params, policy)
        if rec.workload_fingerprint != workload.fingerprint:
            raise RuntimeError("workload fingerprint changed during simulation")  # pragma: no cover
        rm = compute_run_metrics(rec, config)
        out[spec.label] = PolicyRepResult(
            label=spec.label,
            replication=replication,
            policy_key=rec.policy_key,
            policy_version=rec.policy_version,
            metrics=rm,
            constraints=evaluate_run_constraints(rm, config, rec.policy_key),
            policy_flags=dict(rec.policy_flags),
            resolution_notes=tuple(getattr(policy, "notes", ()) or ()),
            settle_count=rec.settle_count,
            outcomes=rec.outcomes if keep_detail else None,
            series=downsample_steps(rec.steps, config.horizon_minutes) if keep_detail else None,
        )
    return ReplicationResult(replication, workload.fingerprint, len(workload.visitors), out)


@dataclass(frozen=True)
class PolicyAggregate:
    label: str
    policy_key: str
    metrics: list[MetricAggregate]
    constraints: list[AggregateConstraintResult]
    constraint_label: str
    flags_total: dict[str, int]


@dataclass(frozen=True)
class ExperimentAggregate:
    policies: dict[str, PolicyAggregate]
    comparisons: list[tuple[str, PairedComparison]]  # (candidate label, comparison vs baseline)
    baseline_label: str
    experiment_label: str
    replications: int
    warnings: list[str]


def _metric_values(reps: Sequence[ReplicationResult], label: str, key: str, group: str) -> list[MetricValue]:
    vals: list[MetricValue] = []
    for r in reps:
        rm = r.results[label].metrics
        src = rm.overall if group == ALL_GROUPS else rm.groups.get(group, {})
        mv = src.get(key)
        vals.append(mv if mv is not None else MetricValue.not_evaluable("", 0, "NO_OBSERVATIONS"))
    return vals


def aggregate_experiment(
    config: ScenarioConfig,
    specs: Sequence[PolicySpec],
    reps: Sequence[ReplicationResult],
    baseline_label: str | None = None,
) -> ExperimentAggregate:
    if not reps:
        raise ValueError("no replications to aggregate")
    labels = [s.label for s in specs]
    baseline = baseline_label or labels[0]
    if baseline not in labels:
        raise ValueError(f"unknown baseline policy label {baseline!r}")
    st = config.statistics
    group_keys = [g.key for g in config.enabled_groups()]
    first = reps[0].results[labels[0]].metrics
    policies: dict[str, PolicyAggregate] = {}
    for spec in specs:
        metrics: list[MetricAggregate] = []
        for key in sorted(first.overall):
            metrics.append(aggregate_metric(_metric_values(reps, spec.label, key, ALL_GROUPS), key, ALL_GROUPS, st))
        for g in group_keys:
            for key in sorted(first.groups.get(g, {})):
                metrics.append(aggregate_metric(_metric_values(reps, spec.label, key, g), key, g, st))
        cons = aggregate_constraints([r.results[spec.label].constraints for r in reps], st)
        flags: dict[str, int] = {}
        for r in reps:
            for k, v in r.results[spec.label].policy_flags.items():
                flags[k] = flags.get(k, 0) + v
        policies[spec.label] = PolicyAggregate(spec.label, spec.key, metrics, cons, policy_label(cons), flags)
    comparisons: list[tuple[str, PairedComparison]] = []
    for spec in specs:
        if spec.label == baseline:
            continue
        for key in PAIRED_OVERALL:
            comparisons.append((spec.label, paired_difference(
                _metric_values(reps, spec.label, key, ALL_GROUPS), _metric_values(reps, baseline, key, ALL_GROUPS),
                key, ALL_GROUPS, st)))  # fmt: skip
        for g in group_keys:
            for key in PAIRED_GROUP:
                cand = _metric_values(reps, spec.label, key, g)
                base = _metric_values(reps, baseline, key, g)
                comparisons.append((spec.label, paired_difference(cand, base, key, g, st)))
    warnings: list[str] = []
    if len(reps) < st.min_replications:
        warnings.append(
            f"Only {len(reps)} replications: at least {st.min_replications} are needed for confidence intervals "
            "and constraint verdicts."
        )
    for spec in specs:
        if spec.key == "simuloom_fair":
            warnings.append(
                f"Policy '{spec.label}': online fairness terms are heuristics; constraint status is evaluated "
                "after the run and no aggregate fairness guarantee is claimed."
            )
            break
    return ExperimentAggregate(
        policies=policies,
        comparisons=comparisons,
        baseline_label=baseline,
        experiment_label=experiment_label({k: v.constraints for k, v in policies.items()}),
        replications=len(reps),
        warnings=warnings,
    )


def finite_or_none(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)
