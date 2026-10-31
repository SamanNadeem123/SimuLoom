"""Request bodies (strict: unknown fields are rejected)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioCreate(_Req):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    config: dict[str, Any]


class ScenarioPatch(_Req):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] | None = None


class ValidationRequest(_Req):
    config: dict[str, Any] | None = None
    scenario_id: uuid.UUID | None = None


class PolicyRequest(_Req):
    key: str = Field(min_length=1, max_length=40)
    label: str | None = Field(default=None, min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class ExperimentCreate(_Req):
    scenario_id: uuid.UUID
    scenario_version: int | None = Field(default=None, ge=1)
    policies: list[PolicyRequest] = Field(min_length=1, max_length=6)
    replications: int | None = Field(default=None, ge=1, le=30)
    base_seed: int | None = Field(default=None, ge=0, le=2**62)
