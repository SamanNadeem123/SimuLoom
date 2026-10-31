"""Scenario configuration models (Pydantic). Time unit: minutes. Proportions in [0, 1]."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Limits (engineering defaults, spec 6.3). Assumptions, not statistical facts.
MAX_COUNTERS = 50
MAX_GROUPS = 8
MAX_HORIZON_MINUTES = 1440.0
MAX_EXPECTED_VISITORS = 20_000
MAX_REPLICATIONS = 30


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Exponential(_Model):
    kind: Literal["exponential"] = "exponential"
    mean_minutes: float = Field(gt=0, le=1440)

    def mean(self) -> float:
        return self.mean_minutes


class Lognormal(_Model):
    kind: Literal["lognormal"] = "lognormal"
    mean_minutes: float = Field(gt=0, le=1440)
    cv: float = Field(gt=0, le=10)  # coefficient of variation (dimensionless)

    def mean(self) -> float:
        return self.mean_minutes


class Gamma(_Model):
    kind: Literal["gamma"] = "gamma"
    mean_minutes: float = Field(gt=0, le=1440)
    cv: float = Field(gt=0, le=10)

    def mean(self) -> float:
        return self.mean_minutes


class Deterministic(_Model):
    kind: Literal["deterministic"] = "deterministic"
    value_minutes: float = Field(gt=0, le=1440)

    def mean(self) -> float:
        return self.value_minutes


class Uniform(_Model):
    kind: Literal["uniform"] = "uniform"
    low_minutes: float = Field(ge=0, le=1440)
    high_minutes: float = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def _order(self) -> Uniform:
        if self.high_minutes <= self.low_minutes:
            raise ValueError("high_minutes must be greater than low_minutes")
        return self

    def mean(self) -> float:
        return (self.low_minutes + self.high_minutes) / 2.0


DistributionSpec = Annotated[Exponential | Lognormal | Gamma | Deterministic | Uniform, Field(discriminator="kind")]


class PoissonArrival(_Model):
    process: Literal["poisson"] = "poisson"
    rate_per_minute: float = Field(gt=0, le=100)

    def rate(self) -> float:
        return self.rate_per_minute


class RenewalArrival(_Model):
    """Non-Poisson arrivals: i.i.d. interarrival times from any distribution."""

    process: Literal["renewal"] = "renewal"
    interarrival: DistributionSpec

    def rate(self) -> float:
        return 1.0 / self.interarrival.mean()


ArrivalConfig = Annotated[PoissonArrival | RenewalArrival, Field(discriminator="process")]


class GroupConfig(_Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    display_name: str = Field(min_length=1, max_length=80)
    arrival_share: float = Field(gt=0, le=1)
    priority_class: int = Field(ge=1, le=10)  # lower number = served first
    service: DistributionSpec | None = None
    enabled: bool = True


class AbandonmentConfig(_Model):
    enabled: bool = False
    patience: DistributionSpec | None = None

    @model_validator(mode="after")
    def _need_patience(self) -> AbandonmentConfig:
        if self.enabled and self.patience is None:
            raise ValueError("patience distribution is required when abandonment is enabled")
        return self


class NoShowConfig(_Model):
    enabled: bool = False
    probability: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _need_prob(self) -> NoShowConfig:
        if self.enabled and self.probability <= 0:
            raise ValueError("probability must be > 0 when no-show is enabled")
        return self


class ServiceLevelConfig(_Model):
    threshold_minutes: float = Field(gt=0, le=1440)


class MinSampleConfig(_Model):
    """Engineering defaults (assumptions), see spec 7.4."""

    group_n: int = Field(default=30, ge=2, le=100_000)
    p95_n: int = Field(default=100, ge=2, le=100_000)


class StatisticsConfig(_Model):
    """Engineering defaults for statistical evaluation (assumptions, NOT statistical guarantees)."""

    ci_level: float = Field(default=0.95, gt=0.5, lt=1)
    min_replications: int = Field(default=5, ge=2, le=30)
    min_valid_fraction: float = Field(default=0.8, gt=0, le=1)
    max_censored_fraction: float = Field(default=0.10, ge=0, le=1)
    relative_baseline_epsilon_minutes: float = Field(default=0.1, gt=0, le=60)


class ConstraintType(StrEnum):
    MAX_MEAN_WAIT_DISPARITY = "MAX_MEAN_WAIT_DISPARITY"
    MAX_RELATIVE_WAIT_DISPARITY = "MAX_RELATIVE_WAIT_DISPARITY"
    MAX_SLA_GAP = "MAX_SLA_GAP"
    MAX_LONG_WAIT_RATE = "MAX_LONG_WAIT_RATE"
    MIN_SLA_ATTAINMENT = "MIN_SLA_ATTAINMENT"
    MAX_INDIVIDUAL_WAIT = "MAX_INDIVIDUAL_WAIT"


_CONSTRAINT_UNIT: dict[ConstraintType, str] = {
    ConstraintType.MAX_MEAN_WAIT_DISPARITY: "minute",
    ConstraintType.MAX_RELATIVE_WAIT_DISPARITY: "proportion",
    ConstraintType.MAX_SLA_GAP: "proportion",
    ConstraintType.MAX_LONG_WAIT_RATE: "proportion",
    ConstraintType.MIN_SLA_ATTAINMENT: "proportion",
    ConstraintType.MAX_INDIVIDUAL_WAIT: "minute",
}
_PAIRWISE = {
    ConstraintType.MAX_MEAN_WAIT_DISPARITY,
    ConstraintType.MAX_RELATIVE_WAIT_DISPARITY,
    ConstraintType.MAX_SLA_GAP,
    ConstraintType.MAX_INDIVIDUAL_WAIT,
}


_MEAN_WAIT_TYPES = {
    ConstraintType.MAX_MEAN_WAIT_DISPARITY,
    ConstraintType.MAX_RELATIVE_WAIT_DISPARITY,
}


class ConstraintConfig(_Model):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    type: ConstraintType
    limit: float = Field(ge=0)
    unit: Literal["minute", "proportion"]
    group: str = "*"  # '*' = every group; pairwise/global types ignore it and require '*'
    enabled: bool = True
    wait_basis: Literal["STARTED_ONLY", "INCLUDING_CENSORED"] = "STARTED_ONLY"

    @model_validator(mode="after")
    def _check(self) -> ConstraintConfig:
        if self.unit != _CONSTRAINT_UNIT[self.type]:
            raise ValueError(f"{self.type.value} requires unit '{_CONSTRAINT_UNIT[self.type]}'")
        if self.unit == "proportion" and self.limit > 1:
            raise ValueError("proportion limits must be within [0, 1]")
        if self.type in _PAIRWISE and self.group != "*":
            raise ValueError(f"{self.type.value} does not take a group; use '*'")
        if self.wait_basis != "STARTED_ONLY" and self.type not in _MEAN_WAIT_TYPES:
            raise ValueError("wait_basis only applies to the mean-wait disparity constraint types")
        return self


class ScenarioConfig(_Model):
    schema_version: Literal[1] = 1
    time_unit: Literal["minute"] = "minute"
    horizon_minutes: float = Field(gt=0, le=MAX_HORIZON_MINUTES)
    warmup_minutes: float = Field(default=0.0, ge=0)
    counters: int = Field(ge=1, le=MAX_COUNTERS)
    arrival: ArrivalConfig
    default_service: DistributionSpec
    groups: list[GroupConfig] = Field(min_length=1, max_length=MAX_GROUPS)
    abandonment: AbandonmentConfig = AbandonmentConfig()
    no_show: NoShowConfig = NoShowConfig()
    queue_capacity: int | None = Field(default=None, ge=0, le=10_000)
    service_level: ServiceLevelConfig
    constraints: list[ConstraintConfig] = Field(default_factory=list)
    min_sample: MinSampleConfig = MinSampleConfig()
    statistics: StatisticsConfig = StatisticsConfig()
    replications: int = Field(default=10, ge=1, le=MAX_REPLICATIONS)

    @model_validator(mode="after")
    def _cross(self) -> ScenarioConfig:
        if self.warmup_minutes >= self.horizon_minutes:
            raise ValueError("warmup_minutes must be smaller than horizon_minutes")
        keys = [g.key for g in self.groups]
        if len(set(keys)) != len(keys):
            raise ValueError("group keys must be unique")
        enabled = [g for g in self.groups if g.enabled]
        if not enabled:
            raise ValueError("at least one group must be enabled")
        if not math.isclose(sum(g.arrival_share for g in enabled), 1.0, abs_tol=1e-6):
            raise ValueError("arrival_share of enabled groups must sum to 1")
        cids = [c.id for c in self.constraints]
        if len(set(cids)) != len(cids):
            raise ValueError("constraint ids must be unique")
        for c in self.constraints:
            if c.group != "*" and c.group not in keys:
                raise ValueError(f"constraint {c.id} references unknown group '{c.group}'")
        if self.expected_visitors() > MAX_EXPECTED_VISITORS:
            raise ValueError(f"expected visitors per run exceed the limit of {MAX_EXPECTED_VISITORS}")
        return self

    # ---- derived quantities -------------------------------------------------
    def enabled_groups(self) -> list[GroupConfig]:
        return [g for g in self.groups if g.enabled]

    def expected_visitors(self) -> float:
        return self.arrival.rate() * self.horizon_minutes

    def service_spec(self, group: GroupConfig) -> DistributionSpec:
        return group.service if group.service is not None else self.default_service

    def offered_load(self) -> float:
        """rho = lambda * E[S] / c, averaged over the enabled-group mixture. Ignores no-shows
        and abandonment (so it is an upper bound on the true load)."""
        mean_s = sum(g.arrival_share * self.service_spec(g).mean() for g in self.enabled_groups())
        return self.arrival.rate() * mean_s / self.counters

    def warnings(self) -> list[str]:
        out: list[str] = []
        rho = self.offered_load()
        if rho >= 1.0:
            out.append(f"offered load rho={rho:.2f} >= 1: the queue is unstable without abandonment")
        elif rho >= 0.9:
            out.append(f"offered load rho={rho:.2f} >= 0.9: near saturation")
        return out

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json().encode()).hexdigest()
