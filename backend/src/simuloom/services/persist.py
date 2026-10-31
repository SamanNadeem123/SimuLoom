"""Persistence of replication results and experiment aggregates (writes only; reads live in ``reading``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from simuloom.db.models import (
    ConstraintEvaluation,
    Experiment,
    ExperimentStatusEvent,
    MetricResult,
    PolicyRun,
    Replication,
    RunTimeSeries,
    VisitorOutcomeRow,
    new_id,
    utcnow,
)
from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.fairness.aggregate import AggregateConstraintResult, MetricAggregate
from simuloom.domain.metrics.compute import metric_rows
from simuloom.domain.metrics.types import CALC_VERSION
from simuloom.services.runner import ExperimentAggregate, PolicySpec, ReplicationResult, finite_or_none

CHUNK = 2000


def _bulk(session: Session, model: type[Any], rows: Sequence[dict[str, Any]]) -> None:
    for i in range(0, len(rows), CHUNK):
        session.execute(insert(model), list(rows[i : i + CHUNK]))


def add_status_event(session: Session, exp_id: Any, old: str | None, new: str, detail: str | None = None) -> None:
    session.add(ExperimentStatusEvent(experiment_id=exp_id, from_status=old, to_status=new, detail=detail))


def persist_replication(
    session: Session,
    exp_id: Any,
    config: ScenarioConfig,
    specs: Sequence[PolicySpec],
    rep: ReplicationResult,
    base_seed: int,
) -> None:
    """One transaction per replication: replication row, policy runs, run-level metrics and constraints."""
    snap = {c.id: c.model_dump(mode="json") for c in config.constraints}
    rep_id = new_id()
    session.add(
        Replication(
            id=rep_id,
            experiment_id=exp_id,
            replication_index=rep.replication,
            seed_entropy=base_seed,
            workload_fingerprint=rep.workload_fingerprint,
            visitor_count=rep.visitor_count,
            status="COMPLETED",
        )
    )
    session.flush()
    by_label = {s.label: s for s in specs}
    metric_dicts: list[dict[str, Any]] = []
    constraint_dicts: list[dict[str, Any]] = []
    for label, res in rep.results.items():
        run_id = new_id()
        session.add(
            PolicyRun(
                id=run_id,
                replication_id=rep_id,
                policy_label=label,
                policy_key=res.policy_key,
                policy_version=res.policy_version,
                policy_params=dict(by_label[label].params),
                resolution_notes=list(res.resolution_notes),
                policy_flags=res.policy_flags,
                settle_count=res.settle_count,
                warnings=list(res.metrics.warnings),
            )
        )
        session.flush()
        for key, group, mv in metric_rows(res.metrics):
            metric_dicts.append(
                {
                    "level": "RUN", "policy_run_id": run_id, "metric_key": key, "group_key": group, "unit": mv.unit,
                    "value": finite_or_none(mv.value), "n": mv.n, "status": mv.status.value, "reason": mv.reason,
                    "calc_version": CALC_VERSION,
                }
            )  # fmt: skip
        for c in res.constraints:
            constraint_dicts.append(
                {
                    "level": "RUN", "policy_run_id": run_id, "constraint_id": c.constraint_id, "type": c.type.value,
                    "group_key": c.group_key, "status": c.status.value, "reason_code": c.reason_code,
                    "observed": finite_or_none(c.observed), "limit_value": c.limit, "unit": c.unit,
                    "comparator": c.comparator, "violation_magnitude": finite_or_none(c.violation_magnitude),
                    "attempted_online": c.attempted_online, "explanation": c.explanation,
                    "details": _json_safe(c.details), "constraint_snapshot": snap.get(c.constraint_id, {}),
                    "zero_variance": False,
                }
            )  # fmt: skip
        if res.outcomes is not None:
            _bulk(session, VisitorOutcomeRow, [
                {"policy_run_id": run_id, "visitor_seq": o.visitor_id, "group_key": o.group_key,
                 "arrival_time": o.arrival_time, "status": o.status.value, "service_start": o.service_start,
                 "completion": o.completion, "abandonment": o.abandonment} for o in res.outcomes])  # fmt: skip
        if res.series is not None:
            session.add(RunTimeSeries(policy_run_id=run_id, points=[[t, q, b] for t, q, b in res.series]))
    session.flush()
    _bulk(session, MetricResult, metric_dicts)
    _bulk(session, ConstraintEvaluation, constraint_dicts)


def persist_failed_replication(session: Session, exp_id: Any, index: int, base_seed: int, error: str) -> None:
    session.add(
        Replication(
            experiment_id=exp_id, replication_index=index, seed_entropy=base_seed, workload_fingerprint="",
            visitor_count=0, status="FAILED", error=error[:2000],
        )
    )  # fmt: skip


def _json_safe(obj: Any) -> Any:
    """Make nested detail dicts JSON-safe (NaN/inf -> None, tuples -> lists)."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return finite_or_none(obj)
    return obj


def _metric_agg_row(exp_id: Any, label: str, m: MetricAggregate) -> dict[str, Any]:
    return {
        "level": "AGGREGATE", "experiment_id": exp_id, "policy_label": label, "metric_key": m.metric_key,
        "group_key": m.group_key, "unit": m.unit, "value": finite_or_none(m.mean), "n": m.n_valid,
        "n_total": m.n_total, "status": "OK" if m.mean is not None else "NOT_EVALUABLE", "reason": m.reason,
        "std": finite_or_none(m.std), "ci_low": finite_or_none(m.ci_low), "ci_high": finite_or_none(m.ci_high),
        "ci_level": m.ci_level, "calc_version": CALC_VERSION,
    }  # fmt: skip


def _constraint_agg_row(exp_id: Any, label: str, a: AggregateConstraintResult, snap: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": "AGGREGATE", "experiment_id": exp_id, "policy_label": label, "constraint_id": a.constraint_id,
        "type": a.type, "group_key": a.group_key, "rule": a.rule, "status": a.status.value,
        "reason_code": a.reason_code, "certainty": a.certainty.value, "observed": finite_or_none(a.observed),
        "limit_value": a.limit, "unit": a.unit, "comparator": a.comparator,
        "violation_magnitude": finite_or_none(a.violation_magnitude),
        "violation_ci_low": finite_or_none(a.violation_ci_low),
        "violation_ci_high": finite_or_none(a.violation_ci_high),
        "ci_low": finite_or_none(a.ci_low), "ci_high": finite_or_none(a.ci_high), "ci_level": a.ci_level,
        "n_valid": a.n_valid, "n_total": a.n_total, "pass_rate": finite_or_none(a.replication_pass_rate),
        "zero_variance": a.zero_variance, "attempted_online": a.attempted_online, "explanation": a.explanation,
        "details": _json_safe(a.details), "constraint_snapshot": snap.get(a.constraint_id, {}),
    }  # fmt: skip


def persist_aggregate_and_complete(session: Session, exp: Experiment, config: ScenarioConfig, agg: ExperimentAggregate) -> None:
    """Write every aggregate row and flip the experiment to COMPLETED inside the caller's single transaction."""
    snap = {c.id: c.model_dump(mode="json") for c in config.constraints}
    metric_dicts: list[dict[str, Any]] = []
    constraint_dicts: list[dict[str, Any]] = []
    for label, pa in agg.policies.items():
        metric_dicts.extend(_metric_agg_row(exp.id, label, m) for m in pa.metrics)
        constraint_dicts.extend(_constraint_agg_row(exp.id, label, c, snap) for c in pa.constraints)
    _bulk(session, MetricResult, metric_dicts)
    _bulk(session, ConstraintEvaluation, constraint_dicts)
    exp.report = {
        "baseline_label": agg.baseline_label,
        "experiment_constraint_label": agg.experiment_label,
        "policy_constraint_labels": {k: v.constraint_label for k, v in agg.policies.items()},
        "policy_flags": {k: v.flags_total for k, v in agg.policies.items()},
        "warnings": agg.warnings,
        "calc_version": CALC_VERSION,
    }
    exp.status = "COMPLETED"
    exp.finished_at = utcnow()
    exp.lease_owner = None
    exp.lease_expires_at = None
    exp.error_code = None
    exp.error_summary = None
    add_status_event(session, exp.id, "RUNNING", "COMPLETED")
