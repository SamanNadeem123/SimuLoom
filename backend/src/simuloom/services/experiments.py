"""Experiment submission, idempotency, retry and deletion (writes)."""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import platform
import secrets
import sys
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from simuloom.api.api_types import SubmitResult
from simuloom.api.errors import CONFLICT, LIMIT_EXCEEDED, TOO_MANY_PENDING, VALIDATION_ERROR, ApiError, not_found
from simuloom.api.schemas import ExperimentCreate
from simuloom.db.models import QUEUED, RUNNING, Experiment, Scenario, ScenarioVersion
from simuloom.domain.config.models import ScenarioConfig
from simuloom.services.persist import add_status_event
from simuloom.services.runner import MAX_POLICIES, PolicySpec, validate_specs
from simuloom.settings import APP_VERSION, Settings

SEED_SCHEME = (
    "SeedSequence(entropy=base_seed, spawn_key=(replication, stream_id)); ARRIVALS=1 GROUPS=2 SERVICE=3 PATIENCE=4 NOSHOW=5"
)


def engine_versions() -> dict[str, str]:
    return {
        "simuloom": APP_VERSION,
        "python": sys.version.split()[0],
        "numpy": md.version("numpy"),
        "scipy": md.version("scipy"),
        "simpy": md.version("simpy"),
        "platform": platform.platform(),
        "seed_scheme": SEED_SCHEME,
    }


def _request_hash(body: ExperimentCreate) -> str:
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def build_specs(body: ExperimentCreate, config: ScenarioConfig) -> list[PolicySpec]:
    specs = [PolicySpec(p.label or p.key, p.key, p.params) for p in body.policies]
    try:
        validate_specs(specs, config)
    except ValueError as exc:
        raise ApiError(
            422,
            VALIDATION_ERROR,
            f"Invalid policy selection: {exc}",
            [{"path": "policies", "code": "invalid_policy", "message": str(exc)}],
        ) from exc
    return specs


def _pending_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Experiment).where(Experiment.status == QUEUED)).scalar_one())


def _check_pending(session: Session, settings: Settings) -> None:
    if _pending_count(session) >= settings.max_pending:
        raise ApiError(
            429,
            TOO_MANY_PENDING,
            f"{settings.max_pending} experiments are already waiting; try again shortly.",
            headers={"Retry-After": "5"},
        )


def create_experiment(session: Session, settings: Settings, body: ExperimentCreate, idempotency_key: str | None) -> SubmitResult:
    key = idempotency_key.strip() if idempotency_key else None
    if key is not None and not (1 <= len(key) <= 128):
        raise ApiError(
            422,
            VALIDATION_ERROR,
            "Idempotency-Key must be 1-128 characters.",
            [{"path": "header.Idempotency-Key", "code": "length", "message": "1-128 characters"}],
        )
    req_hash = _request_hash(body)
    if key:
        existing = session.execute(select(Experiment).where(Experiment.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            return _replay(existing, req_hash)
    scenario = session.get(Scenario, body.scenario_id)
    if scenario is None:
        raise not_found("Scenario")
    if scenario.archived_at is not None:
        raise ApiError(409, CONFLICT, "The scenario is archived; duplicate it to run new experiments.")
    vstmt = select(ScenarioVersion).where(ScenarioVersion.scenario_id == scenario.id)
    vstmt = (
        vstmt.where(ScenarioVersion.version_number == body.scenario_version)
        if body.scenario_version
        else vstmt.order_by(ScenarioVersion.version_number.desc()).limit(1)
    )
    version = session.execute(vstmt).scalar_one_or_none()
    if version is None:
        raise not_found("Scenario version")
    cfg = ScenarioConfig.model_validate(version.config)
    specs = build_specs(body, cfg)
    reps = body.replications if body.replications is not None else cfg.replications
    total = round(cfg.expected_visitors() * reps * len(specs))
    cap = settings.max_total_visitor_simulations
    if total > cap:
        raise ApiError(
            422,
            LIMIT_EXCEEDED,
            f"{total} expected visitor-simulations exceed the limit of {cap}.",
            [
                {
                    "path": "replications",
                    "code": "limit_exceeded",
                    "message": "Reduce replications, policies, horizon or arrival rate.",
                    "limit": {"max_visitor_simulations": cap, "estimated": total},
                }
            ],
        )
    _check_pending(session, settings)
    seed = body.base_seed if body.base_seed is not None else secrets.randbelow(2**31)
    exp = Experiment(
        scenario_version_id=version.id,
        scenario_name=scenario.name,
        scenario_snapshot=version.config,
        scenario_config_hash=version.config_hash,
        requested_policies=[{"label": s.label, "key": s.key, "params": dict(s.params)} for s in specs],
        replication_count=reps,
        base_seed=seed,
        limits={
            "max_policies": MAX_POLICIES,
            "max_visitor_simulations": settings.max_total_visitor_simulations,
            "timeout_seconds": settings.experiment_timeout_seconds,
            "estimated_visitor_simulations": total,
        },
        engine_versions=engine_versions(),
        status=QUEUED,
        idempotency_key=key,
        request_hash=req_hash,
    )
    session.add(exp)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        if key:  # lost a race with an identical concurrent request
            existing = session.execute(select(Experiment).where(Experiment.idempotency_key == key)).scalar_one_or_none()
            if existing is not None:
                return _replay(existing, req_hash)
        raise
    add_status_event(session, exp.id, None, QUEUED, "submitted")
    return SubmitResult(exp, False)


def _replay(existing: Experiment, req_hash: str) -> SubmitResult:
    if existing.request_hash != req_hash:
        raise ApiError(409, CONFLICT, "This Idempotency-Key was already used with a different request body.")
    return SubmitResult(existing, True)


def retry_experiment(session: Session, settings: Settings, source: Experiment) -> Experiment:
    if source.status != "FAILED":
        raise ApiError(409, CONFLICT, f"Only FAILED experiments can be retried (this one is {source.status}).")
    _check_pending(session, settings)
    exp = Experiment(
        scenario_version_id=source.scenario_version_id,
        scenario_name=source.scenario_name,
        scenario_snapshot=source.scenario_snapshot,
        scenario_config_hash=source.scenario_config_hash,
        requested_policies=source.requested_policies,
        replication_count=source.replication_count,
        base_seed=source.base_seed,
        limits=source.limits,
        engine_versions=engine_versions(),
        status=QUEUED,
        retry_of_id=source.id,
    )
    session.add(exp)
    session.flush()
    add_status_event(session, exp.id, None, QUEUED, f"retry of {source.id}")
    return exp


def delete_experiment(session: Session, exp: Experiment) -> None:
    if exp.status == RUNNING:
        raise ApiError(409, CONFLICT, "A running experiment cannot be deleted; wait for it to finish.")
    session.delete(exp)


def get_experiment(session: Session, experiment_id: uuid.UUID) -> Experiment:
    exp = session.get(Experiment, experiment_id)
    if exp is None:
        raise not_found("Experiment")
    return exp


def summarize_config(config: dict[str, Any]) -> dict[str, Any]:
    return {k: config.get(k) for k in ("horizon_minutes", "warmup_minutes", "counters", "queue_capacity")}
