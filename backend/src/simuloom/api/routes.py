"""HTTP routes (spec 6.2, with the v2 route renames: /scenario-validations, /demo-scenarios, /experiment-comparisons)."""

from __future__ import annotations

import importlib.metadata as md
import platform
import sys
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from simuloom.api.errors import ApiError, not_found
from simuloom.api.policy_meta import POLICY_META
from simuloom.api.schemas import ExperimentCreate, ScenarioCreate, ScenarioPatch, ValidationRequest
from simuloom.db.base import session_scope
from simuloom.db.models import Experiment, Scenario
from simuloom.domain.config.models import (
    MAX_COUNTERS,
    MAX_EXPECTED_VISITORS,
    MAX_GROUPS,
    MAX_HORIZON_MINUTES,
    MAX_REPLICATIONS,
)
from simuloom.domain.metrics.types import CALC_VERSION, METRIC_CATALOG
from simuloom.services import experiments as exp_svc
from simuloom.services import reading, scenarios
from simuloom.services.runner import MAX_POLICIES
from simuloom.settings import APP_VERSION, Settings

router = APIRouter(prefix="/api/v1")


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as s:
        yield s


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


DB = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_settings)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def _page(items: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ------------------------------------------------------------------------------------------ system
@router.get("/metadata", tags=["system"])
def metadata(settings: Cfg) -> dict[str, Any]:
    return {
        "app_version": APP_VERSION,
        "calculation_version": CALC_VERSION,
        "engine": {
            "python": sys.version.split()[0],
            "numpy": md.version("numpy"),
            "scipy": md.version("scipy"),
            "simpy": md.version("simpy"),
            "platform": platform.platform(),
        },
        "limits": {
            "max_counters": MAX_COUNTERS,
            "max_groups": MAX_GROUPS,
            "max_replications": MAX_REPLICATIONS,
            "max_horizon_minutes": MAX_HORIZON_MINUTES,
            "max_expected_visitors_per_run": MAX_EXPECTED_VISITORS,
            "max_policies_per_experiment": MAX_POLICIES,
            "max_visitor_simulations": settings.max_total_visitor_simulations,
            "max_pending_experiments": settings.max_pending,
            "experiment_timeout_seconds": settings.experiment_timeout_seconds,
        },
        "metrics": [{"key": k, "unit": u, "description": d} for k, (u, d) in METRIC_CATALOG.items()],
        "constraint_types": [
            "MAX_MEAN_WAIT_DISPARITY",
            "MAX_RELATIVE_WAIT_DISPARITY",
            "MAX_SLA_GAP",
            "MAX_LONG_WAIT_RATE",
            "MIN_SLA_ATTAINMENT",
            "MAX_INDIVIDUAL_WAIT",
        ],
        "limitations": list(reading.LIMITATIONS),
    }


@router.get("/policies", tags=["policies"])
def list_policies() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in POLICY_META.items()]


# ------------------------------------------------------------------------------------------ scenarios
@router.post("/scenarios", status_code=201, tags=["scenarios"])
def create_scenario(body: ScenarioCreate, db: DB, response: Response) -> dict[str, Any]:
    s = scenarios.create_scenario(db, body.name, body.description, body.config)
    response.headers["Location"] = f"/api/v1/scenarios/{s.id}"
    return scenarios.scenario_dict(db, s)


@router.get("/scenarios", tags=["scenarios"])
def list_scenarios(
    db: DB,
    limit: Limit = 20,
    offset: Offset = 0,
    include_archived: bool = False,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> dict[str, Any]:
    rows, total = scenarios.list_scenarios(db, limit, offset, include_archived, q)
    return _page([scenarios.scenario_dict(db, s) for s in rows], total, limit, offset)


@router.post("/scenario-validations", tags=["scenarios"])
def validate_scenario(body: ValidationRequest, db: DB) -> dict[str, Any]:
    if (body.config is None) == (body.scenario_id is None):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Provide exactly one of 'config' or 'scenario_id'.",
            [{"path": "config", "code": "exactly_one", "message": "Provide config or scenario_id, not both."}],
        )
    if body.scenario_id is not None:
        s = scenarios.get_scenario(db, body.scenario_id)
        return scenarios.validate_config(scenarios.scenario_dict(db, s)["latest_version"]["config"])
    assert body.config is not None
    return scenarios.validate_config(body.config)


@router.get("/demo-scenarios", tags=["scenarios"])
def list_demo_scenarios() -> list[dict[str, Any]]:
    return scenarios.demo_list()


@router.post("/demo-scenarios/{key}/instantiate", status_code=201, tags=["scenarios"])
def instantiate_demo(key: str, db: DB, response: Response) -> dict[str, Any]:
    s = scenarios.instantiate_demo(db, key)
    response.headers["Location"] = f"/api/v1/scenarios/{s.id}"
    return scenarios.scenario_dict(db, s)


@router.get("/scenarios/{scenario_id}", tags=["scenarios"])
def get_scenario(scenario_id: uuid.UUID, db: DB) -> dict[str, Any]:
    return scenarios.scenario_dict(db, scenarios.get_scenario(db, scenario_id))


@router.patch("/scenarios/{scenario_id}", tags=["scenarios"])
def patch_scenario(scenario_id: uuid.UUID, body: ScenarioPatch, db: DB) -> dict[str, Any]:
    s = scenarios.patch_scenario(db, scenarios.get_scenario(db, scenario_id), body.name, body.description, body.config)
    return scenarios.scenario_dict(db, s)


@router.post("/scenarios/{scenario_id}/duplicate", status_code=201, tags=["scenarios"])
def duplicate_scenario(scenario_id: uuid.UUID, db: DB, response: Response) -> dict[str, Any]:
    new = scenarios.duplicate_scenario(db, scenarios.get_scenario(db, scenario_id))
    response.headers["Location"] = f"/api/v1/scenarios/{new.id}"
    return scenarios.scenario_dict(db, new)


@router.delete("/scenarios/{scenario_id}", status_code=204, tags=["scenarios"])
def archive_scenario(scenario_id: uuid.UUID, db: DB) -> Response:
    scenarios.archive_scenario(scenarios.get_scenario(db, scenario_id))
    return Response(status_code=204)


@router.get("/scenarios/{scenario_id}/versions", tags=["scenarios"])
def scenario_versions(scenario_id: uuid.UUID, db: DB, limit: Limit = 20, offset: Offset = 0) -> dict[str, Any]:
    scenarios.get_scenario(db, scenario_id)
    rows, total = scenarios.list_versions(db, scenario_id, limit, offset)
    return _page([scenarios.version_dict(v) for v in rows], total, limit, offset)


# ------------------------------------------------------------------------------------------ experiments
@router.post("/experiments", status_code=202, tags=["experiments"])
def submit_experiment(
    body: ExperimentCreate,
    db: DB,
    settings: Cfg,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    result = exp_svc.create_experiment(db, settings, body, idempotency_key)
    response.headers["Location"] = f"/api/v1/experiments/{result.experiment.id}"
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return reading.experiment_dict(db, result.experiment)


@router.get("/experiments", tags=["experiments"])
def list_experiments(
    db: DB,
    limit: Limit = 20,
    offset: Offset = 0,
    status: Annotated[str | None, Query(pattern="^(QUEUED|RUNNING|COMPLETED|FAILED)$")] = None,
    scenario_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    stmt = select(Experiment)
    if status:
        stmt = stmt.where(Experiment.status == status)
    if scenario_id:
        from simuloom.db.models import ScenarioVersion

        stmt = stmt.where(
            Experiment.scenario_version_id.in_(select(ScenarioVersion.id).where(ScenarioVersion.scenario_id == scenario_id))
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Experiment.created_at.desc(), Experiment.id).limit(limit).offset(offset)).scalars().all()
    return _page([reading.experiment_summary(e) for e in rows], int(total), limit, offset)


@router.get("/experiment-comparisons", tags=["experiments"])
def experiment_comparisons(db: DB, ids: Annotated[str, Query(min_length=36, max_length=400)]) -> dict[str, Any]:
    try:
        parsed = list(dict.fromkeys(uuid.UUID(x.strip()) for x in ids.split(",") if x.strip()))
    except ValueError as exc:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "ids must be comma-separated UUIDs.",
            [{"path": "ids", "code": "invalid_uuid", "message": str(exc)}],
        ) from exc
    if not 2 <= len(parsed) <= 5:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Provide between 2 and 5 experiment ids.",
            [{"path": "ids", "code": "count", "message": "2-5 ids"}],
        )
    exps = [exp_svc.get_experiment(db, i) for i in parsed]
    return reading.compare_experiments(db, exps)


@router.get("/experiments/{experiment_id}", tags=["experiments"])
def get_experiment(experiment_id: uuid.UUID, db: DB, include_events: bool = False) -> dict[str, Any]:
    exp = exp_svc.get_experiment(db, experiment_id)
    d = reading.experiment_dict(db, exp)
    if include_events:
        d["status_events"] = reading.status_events(db, exp)
    return d


@router.get("/experiments/{experiment_id}/results", tags=["experiments"])
def experiment_results(experiment_id: uuid.UUID, db: DB) -> dict[str, Any]:
    return reading.results_dict(db, exp_svc.get_experiment(db, experiment_id))


@router.get("/experiments/{experiment_id}/replications", tags=["experiments"])
def experiment_replications(experiment_id: uuid.UUID, db: DB, limit: Limit = 20, offset: Offset = 0) -> dict[str, Any]:
    return reading.replications_dict(db, exp_svc.get_experiment(db, experiment_id), limit, offset)


@router.get("/experiments/{experiment_id}/comparison", tags=["experiments"])
def experiment_comparison(experiment_id: uuid.UUID, db: DB, baseline: str | None = None) -> dict[str, Any]:
    return reading.comparison_dict(db, exp_svc.get_experiment(db, experiment_id), baseline)


@router.get("/experiments/{experiment_id}/constraints", tags=["experiments"])
def experiment_constraints(experiment_id: uuid.UUID, db: DB) -> dict[str, Any]:
    return reading.constraints_dict(db, exp_svc.get_experiment(db, experiment_id))


@router.get("/experiments/{experiment_id}/timeseries", tags=["experiments"])
def experiment_timeseries(
    experiment_id: uuid.UUID,
    db: DB,
    policy: Annotated[str, Query(min_length=1, max_length=64)],
    replication: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    exp = exp_svc.get_experiment(db, experiment_id)
    reading.require_completed(exp)
    return reading.timeseries_dict(db, exp, policy, replication)


@router.get("/experiments/{experiment_id}/export", tags=["experiments"])
def export_experiment(
    experiment_id: uuid.UUID,
    db: DB,
    kind: Annotated[str, Query(pattern="^(summary|metrics|constraints|manifest|report)$")] = "summary",
    format: Annotated[str, Query(pattern="^(csv|json|md|markdown|pdf)$")] = "csv",
) -> Response:
    exp = exp_svc.get_experiment(db, experiment_id)
    body, media, filename = reading.export_payload(db, exp, kind, format)
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/experiments/{experiment_id}/retry", status_code=202, tags=["experiments"])
def retry_experiment(experiment_id: uuid.UUID, db: DB, settings: Cfg, response: Response) -> dict[str, Any]:
    new = exp_svc.retry_experiment(db, settings, exp_svc.get_experiment(db, experiment_id))
    response.headers["Location"] = f"/api/v1/experiments/{new.id}"
    return reading.experiment_dict(db, new)


@router.delete("/experiments/{experiment_id}", status_code=204, tags=["experiments"])
def delete_experiment(experiment_id: uuid.UUID, db: DB) -> Response:
    exp_svc.delete_experiment(db, exp_svc.get_experiment(db, experiment_id))
    return Response(status_code=204)


__all__ = ["Scenario", "not_found", "router"]
