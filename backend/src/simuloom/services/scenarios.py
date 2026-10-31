"""Scenario and scenario-version management."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from simuloom.api.errors import ApiError, not_found, validation_api_error
from simuloom.db.models import Scenario, ScenarioVersion, utcnow
from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.demos import DEMOS


def parse_config(raw: dict[str, Any]) -> ScenarioConfig:
    try:
        return ScenarioConfig.model_validate(raw)
    except ValidationError as exc:
        raise validation_api_error(exc, "config", "Scenario configuration is invalid.") from exc


def estimated_cost(cfg: ScenarioConfig, policies: int = 1, replications: int | None = None) -> dict[str, Any]:
    reps = replications if replications is not None else cfg.replications
    visitors = cfg.expected_visitors()
    return {
        "expected_visitors_per_run": round(visitors),
        "replications": reps,
        "policies": policies,
        "visitor_simulations": round(visitors * reps * policies),
    }


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Non-raising validation used by the validation endpoint: returns valid/errors/warnings/estimated_cost."""
    try:
        cfg = ScenarioConfig.model_validate(raw)
    except ValidationError as exc:
        from simuloom.api.errors import field_errors_from

        return {
            "valid": False,
            "errors": field_errors_from(exc.errors(include_url=False, include_context=False), "config"),
            "warnings": [],
            "estimated_cost": None,
            "config_hash": None,
        }
    return {
        "valid": True,
        "errors": [],
        "warnings": cfg.warnings(),
        "estimated_cost": estimated_cost(cfg),
        "config_hash": cfg.config_hash(),
    }


def version_dict(v: ScenarioVersion, include_config: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(v.id),
        "scenario_id": str(v.scenario_id),
        "version_number": v.version_number,
        "config_hash": v.config_hash,
        "created_at": v.created_at,
    }
    if include_config:
        d["config"] = v.config
    return d


def _latest(session: Session, scenario_id: uuid.UUID) -> ScenarioVersion:
    v = session.execute(
        select(ScenarioVersion)
        .where(ScenarioVersion.scenario_id == scenario_id)
        .order_by(ScenarioVersion.version_number.desc())
        .limit(1)
    ).scalar_one()
    return v


def scenario_dict(session: Session, s: Scenario) -> dict[str, Any]:
    latest = _latest(session, s.id)
    cfg = ScenarioConfig.model_validate(latest.config)
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "archived": s.archived_at is not None,
        "latest_version": version_dict(latest),
        "warnings": cfg.warnings(),
    }


def get_scenario(session: Session, scenario_id: uuid.UUID) -> Scenario:
    s = session.get(Scenario, scenario_id)
    if s is None:
        raise not_found("Scenario")
    return s


def create_scenario(session: Session, name: str, description: str, raw: dict[str, Any]) -> Scenario:
    cfg = parse_config(raw)
    s = Scenario(name=name, description=description)
    session.add(s)
    session.flush()
    session.add(
        ScenarioVersion(scenario_id=s.id, version_number=1, config=cfg.model_dump(mode="json"), config_hash=cfg.config_hash())
    )
    session.flush()
    return s


def list_scenarios(
    session: Session, limit: int, offset: int, include_archived: bool, q: str | None
) -> tuple[list[Scenario], int]:
    stmt = select(Scenario)
    if not include_archived:
        stmt = stmt.where(Scenario.archived_at.is_(None))
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(Scenario.name.ilike(pattern, escape="\\"))
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = session.execute(stmt.order_by(Scenario.updated_at.desc(), Scenario.id).limit(limit).offset(offset)).scalars().all()
    return list(rows), int(total)


def patch_scenario(
    session: Session, s: Scenario, name: str | None, description: str | None, raw: dict[str, Any] | None
) -> Scenario:
    changed = False
    if name is not None and name != s.name:
        s.name, changed = name, True
    if description is not None and description != s.description:
        s.description, changed = description, True
    if raw is not None:
        cfg = parse_config(raw)
        latest = _latest(session, s.id)
        if cfg.config_hash() != latest.config_hash:  # an identical config does not create a new version
            session.add(
                ScenarioVersion(
                    scenario_id=s.id,
                    version_number=latest.version_number + 1,
                    config=cfg.model_dump(mode="json"),
                    config_hash=cfg.config_hash(),
                )
            )
            changed = True
    if changed:
        s.updated_at = utcnow()
    session.flush()
    return s


def duplicate_scenario(session: Session, s: Scenario) -> Scenario:
    latest = _latest(session, s.id)
    new = Scenario(name=f"{s.name} (copy)"[:120], description=s.description)
    session.add(new)
    session.flush()
    session.add(ScenarioVersion(scenario_id=new.id, version_number=1, config=latest.config, config_hash=latest.config_hash))
    session.flush()
    return new


def archive_scenario(s: Scenario) -> None:
    if s.archived_at is None:
        s.archived_at = utcnow()
        s.updated_at = utcnow()


def list_versions(session: Session, scenario_id: uuid.UUID, limit: int, offset: int) -> tuple[list[ScenarioVersion], int]:
    base = select(ScenarioVersion).where(ScenarioVersion.scenario_id == scenario_id)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = session.execute(base.order_by(ScenarioVersion.version_number.desc()).limit(limit).offset(offset)).scalars().all()
    return list(rows), int(total)


def demo_list() -> list[dict[str, Any]]:
    out = []
    for key, d in DEMOS.items():
        cfg = ScenarioConfig.model_validate(d["config"])
        out.append(
            {
                "key": key,
                "name": d["name"],
                "description": d["description"],
                "synthetic": True,
                "offered_load": round(cfg.offered_load(), 3),
                "config": cfg.model_dump(mode="json"),
            }
        )
    return out


def instantiate_demo(session: Session, key: str) -> Scenario:
    d = DEMOS.get(key)
    if d is None:
        raise ApiError(404, "NOT_FOUND", f"Demo scenario '{key}' was not found.")
    return create_scenario(session, d["name"], d["description"], d["config"])
