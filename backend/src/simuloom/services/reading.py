"""Read models: experiment status, results, constraints, comparison, replications, time series, exports."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from simuloom.api.errors import CONFLICT, EXPERIMENT_NOT_READY, ApiError, not_found
from simuloom.db.models import (
    ConstraintEvaluation,
    Experiment,
    ExperimentStatusEvent,
    MetricResult,
    PolicyRun,
    Replication,
    RunTimeSeries,
    ScenarioVersion,
    VisitorOutcomeRow,
)
from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.fairness.aggregate import paired_difference
from simuloom.domain.metrics.types import ALL_GROUPS, CALC_VERSION, METRIC_CATALOG, EvalStatus, MetricValue
from simuloom.services.runner import PAIRED_GROUP, PAIRED_OVERALL

LIMITATIONS = (
    "Results come from a stochastic simulation of the configured scenario; they describe that model, not a real service centre.",
    "Distribution families, parameters, thresholds and constraint limits are the user's assumptions, not measured data.",
    "Constraint status is evaluated after each run. The fairness-constrained policy uses online heuristics and makes no "
    "guarantee that an aggregate constraint holds.",
    "Confidence intervals treat replications as independent and use a Student-t interval; they quantify simulation "
    "noise only, not model error.",
    "Waiting-time metrics over started visitors are biased low when many visitors are still waiting at the horizon; "
    "see the censoring-inclusive metrics and warnings.",
)


# ---------------------------------------------------------------------------------------- experiment resource
def _version_info(session: Session, exp: Experiment) -> tuple[str | None, int | None]:
    if exp.scenario_version_id is None:
        return None, None
    v = session.get(ScenarioVersion, exp.scenario_version_id)
    return (str(v.scenario_id), v.version_number) if v else (None, None)


def experiment_dict(session: Session, exp: Experiment) -> dict[str, Any]:
    scenario_id, version_number = _version_info(session, exp)
    cfg = ScenarioConfig.model_validate(exp.scenario_snapshot)
    warnings = list(cfg.warnings()) + list((exp.report or {}).get("warnings", []))
    return {
        "id": str(exp.id),
        "status": exp.status,
        "scenario_id": scenario_id,
        "scenario_version": version_number,
        "scenario_name": exp.scenario_name,
        "scenario_config_hash": exp.scenario_config_hash,
        "scenario_snapshot": exp.scenario_snapshot,
        "policies": exp.requested_policies,
        "replications_total": exp.replication_count,
        "replications_done": exp.replications_done,
        "base_seed": exp.base_seed,
        "limits": exp.limits,
        "engine_versions": exp.engine_versions,
        "warnings": warnings,
        "error_code": exp.error_code,
        "error_summary": exp.error_summary,
        "attempt": exp.attempt,
        "retry_of": str(exp.retry_of_id) if exp.retry_of_id else None,
        "experiment_constraint_label": (exp.report or {}).get("experiment_constraint_label"),
        "policy_constraint_labels": (exp.report or {}).get("policy_constraint_labels"),
        "created_at": exp.created_at,
        "started_at": exp.started_at,
        "finished_at": exp.finished_at,
        "heartbeat_at": exp.heartbeat_at,
    }


def experiment_summary(exp: Experiment) -> dict[str, Any]:
    return {
        "id": str(exp.id),
        "status": exp.status,
        "scenario_name": exp.scenario_name,
        "scenario_config_hash": exp.scenario_config_hash,
        "policies": [p["label"] for p in exp.requested_policies],
        "replications_total": exp.replication_count,
        "replications_done": exp.replications_done,
        "base_seed": exp.base_seed,
        "error_code": exp.error_code,
        "experiment_constraint_label": (exp.report or {}).get("experiment_constraint_label"),
        "created_at": exp.created_at,
        "finished_at": exp.finished_at,
    }


def status_events(session: Session, exp: Experiment) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ExperimentStatusEvent).where(ExperimentStatusEvent.experiment_id == exp.id).order_by(ExperimentStatusEvent.id)
    ).scalars()
    return [{"at": r.at, "from": r.from_status, "to": r.to_status, "detail": r.detail} for r in rows]


def require_completed(exp: Experiment) -> None:
    if exp.status != "COMPLETED":
        raise ApiError(409, EXPERIMENT_NOT_READY, f"The experiment is {exp.status}; results exist only once it is COMPLETED.")


# ---------------------------------------------------------------------------------------- results
def _metric_agg_dict(m: MetricResult) -> dict[str, Any]:
    return {
        "metric_key": m.metric_key,
        "group_key": m.group_key,
        "unit": m.unit,
        "mean": m.value,
        "std": m.std,
        "ci_low": m.ci_low,
        "ci_high": m.ci_high,
        "ci_level": m.ci_level,
        "n_valid": m.n,
        "n_total": m.n_total,
        "status": m.status,
        "reason": m.reason,
    }


def constraint_dict(c: ConstraintEvaluation) -> dict[str, Any]:
    return {
        "constraint_id": c.constraint_id,
        "type": c.type,
        "group_key": c.group_key,
        "level": c.level,
        "rule": c.rule,
        "status": c.status,
        "reason_code": c.reason_code,
        "certainty": c.certainty,
        "observed": c.observed,
        "limit": c.limit_value,
        "unit": c.unit,
        "comparator": c.comparator,
        "violation_magnitude": c.violation_magnitude,
        "violation_ci": [c.violation_ci_low, c.violation_ci_high] if c.violation_ci_low is not None else None,
        "ci": [c.ci_low, c.ci_high] if c.ci_low is not None and c.ci_high is not None else None,
        "ci_level": c.ci_level,
        "ci_straddles_limit": c.certainty == "INCONCLUSIVE" if c.certainty else None,
        "n_replications_valid": c.n_valid,
        "n_replications_total": c.n_total,
        "replication_pass_rate": c.pass_rate,
        "zero_variance": c.zero_variance,
        "enforcement": {"attempted_online": c.attempted_online, "guarantee": "none"},
        "explanation": c.explanation,
        "details": c.details,
        "constraint": c.constraint_snapshot,
    }


def _policy_meta(session: Session, exp: Experiment) -> dict[str, dict[str, Any]]:
    """Per label: key, version, params, notes taken from the first stored policy run."""
    rows = session.execute(
        select(PolicyRun, Replication.replication_index)
        .join(Replication, Replication.id == PolicyRun.replication_id)
        .where(Replication.experiment_id == exp.id, Replication.status == "COMPLETED")
        .order_by(Replication.replication_index)
    ).all()
    meta: dict[str, dict[str, Any]] = {}
    for run, _ in rows:
        m = meta.setdefault(
            run.policy_label,
            {
                "label": run.policy_label,
                "key": run.policy_key,
                "version": run.policy_version,
                "params": run.policy_params,
                "resolution_notes": run.resolution_notes,
                "flags": {},
            },
        )
        for k, v in (run.policy_flags or {}).items():
            m["flags"][k] = m["flags"].get(k, 0) + v
    for p in exp.requested_policies:  # keep requested order, also for policies without stored runs
        meta.setdefault(
            p["label"],
            {"label": p["label"], "key": p["key"], "version": None, "params": p["params"], "resolution_notes": [], "flags": {}},
        )
    return {p["label"]: meta[p["label"]] for p in exp.requested_policies}


def results_dict(session: Session, exp: Experiment) -> dict[str, Any]:
    require_completed(exp)
    meta = _policy_meta(session, exp)
    metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in session.execute(
        select(MetricResult)
        .where(MetricResult.experiment_id == exp.id, MetricResult.level == "AGGREGATE")
        .order_by(MetricResult.policy_label, MetricResult.group_key, MetricResult.metric_key)
    ).scalars():
        metrics[str(m.policy_label)].append(_metric_agg_dict(m))
    report = exp.report or {}
    policies = []
    for label, mt in meta.items():
        policies.append(
            {
                **mt,
                "constraint_label": (report.get("policy_constraint_labels") or {}).get(label),
                "metrics": metrics.get(label, []),
            }
        )
    return {
        "experiment_id": str(exp.id),
        "status": exp.status,
        "calculation_version": CALC_VERSION,
        "replications": exp.replication_count,
        "base_seed": exp.base_seed,
        "baseline": report.get("baseline_label"),
        "experiment_constraint_label": report.get("experiment_constraint_label"),
        "warnings": report.get("warnings", []),
        "group_keys": [g["key"] for g in exp.scenario_snapshot.get("groups", []) if g.get("enabled", True)],
        "metric_catalog": {k: {"unit": u, "description": d} for k, (u, d) in METRIC_CATALOG.items()},
        "policies": policies,
    }


def constraints_dict(session: Session, exp: Experiment) -> dict[str, Any]:
    require_completed(exp)
    report = exp.report or {}
    agg: dict[str, list[ConstraintEvaluation]] = defaultdict(list)
    for c in session.execute(
        select(ConstraintEvaluation)
        .where(ConstraintEvaluation.experiment_id == exp.id)
        .order_by(ConstraintEvaluation.policy_label, ConstraintEvaluation.constraint_id, ConstraintEvaluation.group_key)
    ).scalars():
        agg[str(c.policy_label)].append(c)
    per_rep: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rows = session.execute(
        select(ConstraintEvaluation, PolicyRun.policy_label, Replication.replication_index)
        .join(PolicyRun, PolicyRun.id == ConstraintEvaluation.policy_run_id)
        .join(Replication, Replication.id == PolicyRun.replication_id)
        .where(Replication.experiment_id == exp.id)
        .order_by(Replication.replication_index)
    ).all()
    for c, label, idx in rows:
        per_rep[(label, c.constraint_id, c.group_key)].append(
            {"replication": idx, "status": c.status, "observed": c.observed, "reason_code": c.reason_code}
        )
    labels = report.get("policy_constraint_labels") or {}
    out = []
    for p in exp.requested_policies:
        items = []
        for c in agg.get(p["label"], []):
            d = constraint_dict(c)
            d["per_replication"] = per_rep.get((p["label"], c.constraint_id, c.group_key), [])
            items.append(d)
        out.append(
            {"policy_label": p["label"], "policy_key": p["key"], "constraint_label": labels.get(p["label"]), "constraints": items}
        )
    return {
        "experiment_id": str(exp.id),
        "experiment_constraint_label": report.get("experiment_constraint_label"),
        "statement": "Constraint status is evaluated after the run. No policy is claimed to guarantee an aggregate constraint.",
        "policies": out,
    }


def _run_values(session: Session, exp: Experiment) -> dict[tuple[str, str, str], dict[int, MetricValue]]:
    """(policy_label, metric_key, group_key) -> {replication_index: MetricValue}."""
    rows = session.execute(
        select(MetricResult, PolicyRun.policy_label, Replication.replication_index)
        .join(PolicyRun, PolicyRun.id == MetricResult.policy_run_id)
        .join(Replication, Replication.id == PolicyRun.replication_id)
        .where(Replication.experiment_id == exp.id, Replication.status == "COMPLETED")
    ).all()
    out: dict[tuple[str, str, str], dict[int, MetricValue]] = defaultdict(dict)
    for m, label, idx in rows:
        ok = m.status == EvalStatus.OK.value and m.value is not None
        out[(label, m.metric_key, m.group_key)][idx] = (
            MetricValue.ok(m.value, m.unit, m.n) if ok else MetricValue.not_evaluable(m.unit, m.n, m.reason or "NO_OBSERVATIONS")
        )
    return out


def comparison_dict(session: Session, exp: Experiment, baseline: str | None) -> dict[str, Any]:
    require_completed(exp)
    labels = [p["label"] for p in exp.requested_policies]
    base = baseline or (exp.report or {}).get("baseline_label") or labels[0]
    if base not in labels:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            f"Unknown baseline policy '{base}'.",
            [{"path": "baseline", "code": "unknown_policy", "message": f"Choose one of: {', '.join(labels)}"}],
        )
    cfg = ScenarioConfig.model_validate(exp.scenario_snapshot)
    values = _run_values(session, exp)
    groups = [g.key for g in cfg.enabled_groups()]
    fingerprints = session.execute(
        select(Replication.replication_index, Replication.workload_fingerprint)
        .where(Replication.experiment_id == exp.id, Replication.status == "COMPLETED")
        .order_by(Replication.replication_index)
    ).all()

    def series(label: str, key: str, group: str) -> list[MetricValue]:
        d = values.get((label, key, group), {})
        return [d[i] for i in sorted(d)]

    comparisons: list[dict[str, Any]] = []
    for label in labels:
        if label == base:
            continue
        wanted = [(k, ALL_GROUPS) for k in PAIRED_OVERALL] + [(k, g) for g in groups for k in PAIRED_GROUP]
        for key, group in wanted:
            cand, ref = series(label, key, group), series(base, key, group)
            if not cand or len(cand) != len(ref):
                continue
            p = paired_difference(cand, ref, key, group, cfg.statistics)
            comparisons.append(
                {
                    "policy_label": label,
                    "baseline_label": base,
                    "metric_key": key,
                    "group_key": group,
                    "unit": p.unit,
                    "n_pairs": p.n_pairs,
                    "mean_difference": p.mean_difference,
                    "ci_low": p.ci_low,
                    "ci_high": p.ci_high,
                    "ci_level": cfg.statistics.ci_level,
                    "ci_excludes_zero": p.ci_excludes_zero,
                }
            )
    return {
        "experiment_id": str(exp.id),
        "baseline": base,
        "difference_definition": "candidate minus baseline, computed per replication on the identical workload",
        "equivalence": {
            "comparable": True,
            "checks": [
                {"name": "same_scenario_config_hash", "ok": True, "detail": exp.scenario_config_hash},
                {"name": "same_replication_seeds", "ok": True, "detail": f"base_seed={exp.base_seed}"},
                {
                    "name": "same_workload_fingerprint_per_replication",
                    "ok": True,
                    "detail": "one workload per replication, shared by all policies; verified by the runner",
                },
                {
                    "name": "replications_with_stored_results",
                    "ok": len(fingerprints) == exp.replication_count,
                    "detail": f"{len(fingerprints)} of {exp.replication_count}",
                },
            ],
            "workload_fingerprints": {int(i): f for i, f in fingerprints},
        },
        "comparisons": comparisons,
    }


def replications_dict(session: Session, exp: Experiment, limit: int, offset: int) -> dict[str, Any]:
    total = session.execute(select(func.count()).select_from(Replication).where(Replication.experiment_id == exp.id)).scalar_one()
    reps = (
        session.execute(
            select(Replication)
            .where(Replication.experiment_id == exp.id)
            .order_by(Replication.replication_index)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    items = []
    for r in reps:
        runs = []
        for run in session.execute(
            select(PolicyRun).where(PolicyRun.replication_id == r.id).order_by(PolicyRun.policy_label)
        ).scalars():
            head = {
                m.metric_key: m.value
                for m in session.execute(
                    select(MetricResult).where(
                        MetricResult.policy_run_id == run.id,
                        MetricResult.group_key == ALL_GROUPS,
                        MetricResult.metric_key.in_(("wait_mean", "sla_attainment", "abandonment_rate", "utilization")),
                    )
                ).scalars()
            }
            runs.append(
                {
                    "policy_label": run.policy_label,
                    "policy_key": run.policy_key,
                    "flags": run.policy_flags,
                    "warnings": run.warnings,
                    "headline_metrics": head,
                }
            )
        items.append(
            {
                "replication_index": r.replication_index,
                "seed_entropy": r.seed_entropy,
                "workload_fingerprint": r.workload_fingerprint,
                "visitor_count": r.visitor_count,
                "status": r.status,
                "error": r.error,
                "policy_runs": runs,
            }
        )
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


def timeseries_dict(session: Session, exp: Experiment, label: str, replication: int) -> dict[str, Any]:
    row = session.execute(
        select(RunTimeSeries.points)
        .join(PolicyRun, PolicyRun.id == RunTimeSeries.policy_run_id)
        .join(Replication, Replication.id == PolicyRun.replication_id)
        .where(
            Replication.experiment_id == exp.id,
            Replication.replication_index == replication,
            PolicyRun.policy_label == label,
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Time series (stored for replication 0 only)")
    cfg = exp.scenario_snapshot
    return {
        "policy_label": label,
        "replication": replication,
        "counters": cfg.get("counters"),
        "columns": ["time_minutes", "queue_length", "busy_counters"],
        "points": row,
        "note": "Right-continuous step series, downsampled to at most 600 points.",
    }


# ---------------------------------------------------------------------------------------- cross-experiment
def compare_experiments(session: Session, exps: list[Experiment]) -> dict[str, Any]:
    fp: dict[uuid.UUID, dict[int, str]] = {}
    for e in exps:
        rows = session.execute(
            select(Replication.replication_index, Replication.workload_fingerprint).where(
                Replication.experiment_id == e.id, Replication.status == "COMPLETED"
            )
        ).all()
        fp[e.id] = {int(i): f for i, f in rows}
    first = exps[0]
    checks = [
        {
            "name": "same_scenario_config_hash",
            "ok": all(e.scenario_config_hash == first.scenario_config_hash for e in exps),
        },
        {"name": "same_base_seed", "ok": all(e.base_seed == first.base_seed for e in exps)},
        {"name": "same_replication_count", "ok": all(e.replication_count == first.replication_count for e in exps)},
        {"name": "same_workload_fingerprint_per_replication", "ok": all(fp[e.id] == fp[first.id] for e in exps)},
    ]
    differences = []
    keys = sorted({k for e in exps for k in e.scenario_snapshot})
    for k in keys:
        vals = [e.scenario_snapshot.get(k) for e in exps]
        if any(v != vals[0] for v in vals):
            differences.append({"config_key": k, "values": {str(e.id): e.scenario_snapshot.get(k) for e in exps}})
    items = []
    for e in exps:
        headline: dict[str, dict[str, float | None]] = {}
        if e.status == "COMPLETED":
            for m in session.execute(
                select(MetricResult).where(
                    MetricResult.experiment_id == e.id,
                    MetricResult.level == "AGGREGATE",
                    MetricResult.group_key == ALL_GROUPS,
                    MetricResult.metric_key.in_(("wait_mean", "wait_p95", "sla_attainment", "abandonment_rate", "utilization")),
                )
            ).scalars():
                headline.setdefault(str(m.policy_label), {})[m.metric_key] = m.value
        items.append({**experiment_summary(e), "headline_metrics": headline})
    return {
        "comparable": all(c["ok"] for c in checks),
        "checks": checks,
        "differences": differences,
        "experiments": items,
    }


# ---------------------------------------------------------------------------------------- exports
def csv_safe(value: Any) -> Any:
    """Formula-injection defence (spec 11.1): text cells starting with = + - @ tab or CR get a leading quote."""
    if isinstance(value, str) and value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r.get(c) is None else csv_safe(r.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8")


def manifest_dict(session: Session, exp: Experiment) -> dict[str, Any]:
    meta = _policy_meta(session, exp)
    reps = session.execute(
        select(Replication).where(Replication.experiment_id == exp.id).order_by(Replication.replication_index)
    ).scalars()
    return {
        "manifest_version": 1,
        "experiment_id": str(exp.id),
        "status": exp.status,
        "scenario": {"name": exp.scenario_name, "config_hash": exp.scenario_config_hash, "snapshot": exp.scenario_snapshot},
        "policies": list(meta.values()),
        "base_seed": exp.base_seed,
        "replications": [
            {
                "index": r.replication_index,
                "seed_entropy": r.seed_entropy,
                "workload_fingerprint": r.workload_fingerprint,
                "visitor_count": r.visitor_count,
                "status": r.status,
            }
            for r in reps
        ],
        "engine_versions": exp.engine_versions,
        "calculation_version": CALC_VERSION,
        "timestamps": {"created_at": exp.created_at, "started_at": exp.started_at, "finished_at": exp.finished_at},
        "how_to_reproduce": "Submit the scenario snapshot with the same policies, replication count and base_seed; "
        "results match exactly only on the same Python/NumPy/SimPy versions (see engine_versions).",
    }


SUMMARY_COLUMNS = [
    "policy_label",
    "metric_key",
    "group_key",
    "unit",
    "mean",
    "std",
    "ci_low",
    "ci_high",
    "ci_level",
    "n_valid",
    "n_total",
    "status",
    "reason",
]
RUN_COLUMNS = ["policy_label", "replication_index", "metric_key", "group_key", "unit", "value", "n", "status", "reason"]
CONSTRAINT_COLUMNS = [
    "policy_label",
    "constraint_id",
    "type",
    "group_key",
    "rule",
    "status",
    "certainty",
    "reason_code",
    "observed",
    "limit",
    "unit",
    "comparator",
    "violation_magnitude",
    "ci_low",
    "ci_high",
    "n_valid",
    "n_total",
    "pass_rate",
    "attempted_online",
    "explanation",
]


def export_rows(session: Session, exp: Experiment, kind: str) -> tuple[list[dict[str, Any]], list[str]]:
    if kind == "summary":
        rows = [
            {"policy_label": m.policy_label, **{k: v for k, v in _metric_agg_dict(m).items()}}
            for m in session.execute(
                select(MetricResult)
                .where(MetricResult.experiment_id == exp.id, MetricResult.level == "AGGREGATE")
                .order_by(MetricResult.policy_label, MetricResult.group_key, MetricResult.metric_key)
            ).scalars()
        ]
        return rows, SUMMARY_COLUMNS
    if kind == "metrics":
        q = session.execute(
            select(MetricResult, PolicyRun.policy_label, Replication.replication_index)
            .join(PolicyRun, PolicyRun.id == MetricResult.policy_run_id)
            .join(Replication, Replication.id == PolicyRun.replication_id)
            .where(Replication.experiment_id == exp.id)
            .order_by(PolicyRun.policy_label, Replication.replication_index, MetricResult.group_key, MetricResult.metric_key)
        ).all()
        return [
            {
                "policy_label": lab,
                "replication_index": idx,
                "metric_key": m.metric_key,
                "group_key": m.group_key,
                "unit": m.unit,
                "value": m.value,
                "n": m.n,
                "status": m.status,
                "reason": m.reason,
            }
            for m, lab, idx in q
        ], RUN_COLUMNS
    if kind == "constraints":
        rows = []
        for c in session.execute(
            select(ConstraintEvaluation)
            .where(ConstraintEvaluation.experiment_id == exp.id)
            .order_by(ConstraintEvaluation.policy_label, ConstraintEvaluation.constraint_id, ConstraintEvaluation.group_key)
        ).scalars():
            d = constraint_dict(c)
            rows.append(
                {
                    "policy_label": c.policy_label,
                    **d,
                    "ci_low": c.ci_low,
                    "ci_high": c.ci_high,
                    "n_valid": c.n_valid,
                    "n_total": c.n_total,
                    "pass_rate": c.pass_rate,
                    "attempted_online": c.attempted_online,
                }
            )
        return rows, CONSTRAINT_COLUMNS
    raise ApiError(
        422,
        "VALIDATION_ERROR",
        f"Unknown export kind '{kind}'.",
        [{"path": "kind", "code": "invalid_choice", "message": "Use summary, metrics, constraints, manifest or report."}],
    )


def _json_default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def export_payload(session: Session, exp: Experiment, kind: str, fmt: str) -> tuple[bytes, str, str]:
    """Returns (body, media_type, filename)."""
    short = str(exp.id)[:8]
    if kind == "manifest":
        if fmt != "json":
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "The manifest is available as JSON only.",
                [{"path": "format", "code": "invalid_choice", "message": "Use format=json"}],
            )
        body = json.dumps(manifest_dict(session, exp), indent=2, default=_json_default).encode()
        return body, "application/json", f"simuloom-{short}-manifest.json"
    if kind == "report":
        if fmt not in ("md", "markdown"):
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "The report is available as Markdown (format=md).",
                [{"path": "format", "code": "invalid_choice", "message": "Use format=md"}],
            )
        from simuloom.services.report import build_report_markdown

        return (
            build_report_markdown(session, exp).encode("utf-8"),
            "text/markdown; charset=utf-8",
            f"simuloom-{short}-report.md",
        )
    if kind in ("summary", "metrics", "constraints"):
        require_completed(exp)
        rows, cols = export_rows(session, exp, kind)
        if fmt == "csv":
            return _csv(rows, cols), "text/csv; charset=utf-8", f"simuloom-{short}-{kind}.csv"
        if fmt == "json":
            body = json.dumps([{c: r.get(c) for c in cols} for r in rows], indent=2, default=_json_default).encode()
            return body, "application/json", f"simuloom-{short}-{kind}.json"
    raise ApiError(
        422,
        "VALIDATION_ERROR",
        "Unsupported export format.",
        [{"path": "format", "code": "invalid_choice", "message": "Use csv or json (PDF is not implemented)."}],
    )


def visitor_outcomes_count(session: Session, exp: Experiment) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(VisitorOutcomeRow)
            .join(PolicyRun, PolicyRun.id == VisitorOutcomeRow.policy_run_id)
            .join(Replication, Replication.id == PolicyRun.replication_id)
            .where(Replication.experiment_id == exp.id)
        ).scalar_one()
    )


__all__ = ["CONFLICT", "LIMITATIONS"]
