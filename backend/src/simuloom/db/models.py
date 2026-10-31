"""ORM models (spec section 5, with the v2 execution columns).

Deviations from the Phase 0 table (documented in the README): metric_result / constraint_evaluation /
status-event rows use bigint surrogate keys (high volume); VisitorOutcome and RunTimeSeries are persisted for
replication 0 only (bounded storage).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from simuloom.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


TS = DateTime(timezone=True)

# exactly one of (policy_run_id) / (experiment_id + policy_label) is set, matching `level`
_LEVEL_CHECK = (
    "(level = 'RUN' and policy_run_id is not null and experiment_id is null)"
    " or (level = 'AGGREGATE' and policy_run_id is null"
    " and experiment_id is not null and policy_label is not null)"
)

# experiment status values
QUEUED, RUNNING, COMPLETED, FAILED = "QUEUED", "RUNNING", "COMPLETED", "FAILED"
EXPERIMENT_STATUSES = (QUEUED, RUNNING, COMPLETED, FAILED)


class Scenario(Base):
    __tablename__ = "scenario"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    versions: Mapped[list[ScenarioVersion]] = relationship(back_populates="scenario", order_by="ScenarioVersion.version_number")
    __table_args__ = (Index("ix_scenario_updated_at", "updated_at"),)


class ScenarioVersion(Base):
    __tablename__ = "scenario_version"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenario.id", ondelete="RESTRICT"))
    version_number: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    scenario: Mapped[Scenario] = relationship(back_populates="versions")
    __table_args__ = (
        UniqueConstraint("scenario_id", "version_number", name="uq_scenario_version_number"),
        Index("ix_scenario_version_latest", "scenario_id", text("version_number DESC")),
    )


class Experiment(Base):
    __tablename__ = "experiment"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    scenario_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenario_version.id", ondelete="SET NULL"), nullable=True
    )
    scenario_name: Mapped[str] = mapped_column(String(120))
    scenario_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)  # immutable copy of the executed config
    scenario_config_hash: Mapped[str] = mapped_column(String(80))
    requested_policies: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    replication_count: Mapped[int] = mapped_column(Integer)
    base_seed: Mapped[int] = mapped_column(BigInteger)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    engine_versions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default=QUEUED)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    replications_done: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment.id", ondelete="SET NULL"), nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # labels, warnings, baseline
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    __table_args__ = (
        CheckConstraint("status in ('QUEUED','RUNNING','COMPLETED','FAILED')", name="ck_experiment_status"),
        CheckConstraint("replication_count between 1 and 30", name="ck_experiment_replications"),
        Index("ix_experiment_created_at", text("created_at DESC")),
        Index("ix_experiment_status", "status"),
        Index(
            "uq_experiment_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key is not null"),
        ),
    )


class ExperimentStatusEvent(Base):
    __tablename__ = "experiment_status_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (Index("ix_status_event_experiment", "experiment_id", "id"),)


class Replication(Base):
    __tablename__ = "replication"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment.id", ondelete="CASCADE"))
    replication_index: Mapped[int] = mapped_column(Integer)
    seed_entropy: Mapped[int] = mapped_column(BigInteger)
    workload_fingerprint: Mapped[str] = mapped_column(String(80))
    visitor_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="COMPLETED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runs: Mapped[list[PolicyRun]] = relationship(back_populates="replication")
    __table_args__ = (
        UniqueConstraint("experiment_id", "replication_index", name="uq_replication_index"),
        Index("ix_replication_experiment", "experiment_id"),
    )


class PolicyRun(Base):
    __tablename__ = "policy_run"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    replication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("replication.id", ondelete="CASCADE"))
    policy_label: Mapped[str] = mapped_column(String(64))
    policy_key: Mapped[str] = mapped_column(String(40))
    policy_version: Mapped[str] = mapped_column(String(20))
    policy_params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resolution_notes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    policy_flags: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    settle_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    replication: Mapped[Replication] = relationship(back_populates="runs")
    __table_args__ = (
        UniqueConstraint("replication_id", "policy_label", name="uq_policy_run_label"),
        Index("ix_policy_run_replication", "replication_id"),
    )


class VisitorOutcomeRow(Base):
    __tablename__ = "visitor_outcome"
    policy_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_run.id", ondelete="CASCADE"), primary_key=True)
    visitor_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_key: Mapped[str] = mapped_column(String(64))
    arrival_time: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24))
    service_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion: Mapped[float | None] = mapped_column(Float, nullable=True)
    abandonment: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (Index("ix_visitor_outcome_group", "policy_run_id", "group_key"),)


class RunTimeSeries(Base):
    __tablename__ = "run_timeseries"
    policy_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_run.id", ondelete="CASCADE"), primary_key=True)
    points: Mapped[list[list[float]]] = mapped_column(JSONB)  # [time, queue_length, busy_counters]


class MetricResult(Base):
    __tablename__ = "metric_result"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10))  # RUN | AGGREGATE
    policy_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_run.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment.id", ondelete="CASCADE"), nullable=True)
    policy_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_key: Mapped[str] = mapped_column(String(40))
    group_key: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(20))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)  # run value, or aggregate mean
    n: Mapped[int] = mapped_column(Integer)  # run: sample size; aggregate: n_valid
    n_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="OK")
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    std: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    calc_version: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        CheckConstraint(
            _LEVEL_CHECK,
            name="ck_metric_result_level",
        ),
        Index(
            "uq_metric_run",
            "policy_run_id",
            "metric_key",
            "group_key",
            unique=True,
            postgresql_where=text("level = 'RUN'"),
        ),
        Index(
            "uq_metric_aggregate",
            "experiment_id",
            "policy_label",
            "metric_key",
            "group_key",
            unique=True,
            postgresql_where=text("level = 'AGGREGATE'"),
        ),  # fmt: skip
    )


class ConstraintEvaluation(Base):
    __tablename__ = "constraint_evaluation"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10))
    policy_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_run.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment.id", ondelete="CASCADE"), nullable=True)
    policy_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    constraint_id: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(40))
    group_key: Mapped[str] = mapped_column(String(64))
    rule: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    certainty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    comparator: Mapped[str] = mapped_column(String(2))
    violation_magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    violation_ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    violation_ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_valid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    zero_variance: Mapped[bool] = mapped_column(Boolean, default=False)
    attempted_online: Mapped[bool] = mapped_column(Boolean, default=False)
    explanation: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    constraint_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        CheckConstraint(
            _LEVEL_CHECK,
            name="ck_constraint_eval_level",
        ),
        Index(
            "uq_constraint_run",
            "policy_run_id",
            "constraint_id",
            "group_key",
            unique=True,
            postgresql_where=text("level = 'RUN'"),
        ),  # fmt: skip
        Index(
            "uq_constraint_aggregate",
            "experiment_id",
            "policy_label",
            "constraint_id",
            "group_key",
            unique=True,
            postgresql_where=text("level = 'AGGREGATE'"),
        ),  # fmt: skip
    )
