"""Code registry of policies (spec decision D-7)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.policies.aging import AgingPriorityPolicy
from simuloom.domain.policies.base import SchedulingPolicy
from simuloom.domain.policies.fair import SimuLoomFairPolicy, resolve_fair_params
from simuloom.domain.policies.fcfs import FcfsPolicy
from simuloom.domain.policies.static_priority import StaticPriorityPolicy


def _no_params(key: str, params: Mapping[str, Any]) -> None:
    if params:
        raise ValueError(f"{key} takes no parameters, got {sorted(params)}")


_Factory = Callable[[Mapping[str, Any], ScenarioConfig | None], SchedulingPolicy]


def _fcfs(params: Mapping[str, Any], scenario: ScenarioConfig | None) -> SchedulingPolicy:
    _no_params("fcfs", params)
    return FcfsPolicy()


def _static(params: Mapping[str, Any], scenario: ScenarioConfig | None) -> SchedulingPolicy:
    _no_params("static_priority", params)
    return StaticPriorityPolicy()


def _aging(params: Mapping[str, Any], scenario: ScenarioConfig | None) -> SchedulingPolicy:
    unknown = sorted(set(params) - {"aging_rate_per_minute"})
    if unknown:
        raise ValueError(f"aging_priority: unknown parameters {unknown}")
    if "aging_rate_per_minute" not in params:
        raise ValueError("aging_priority requires parameter 'aging_rate_per_minute' (>= 0)")
    return AgingPriorityPolicy(params["aging_rate_per_minute"])


def _fair(params: Mapping[str, Any], scenario: ScenarioConfig | None) -> SchedulingPolicy:
    if scenario is None:
        raise ValueError("simuloom_fair needs the scenario (service-level threshold, constraints, groups)")
    fp, notes = resolve_fair_params(params, scenario)
    return SimuLoomFairPolicy(fp, notes)


_FACTORIES: dict[str, _Factory] = {
    "simuloom_fair": _fair,
    "fcfs": _fcfs,
    "static_priority": _static,
    "aging_priority": _aging,
}


def available_policies() -> list[str]:
    return sorted(_FACTORIES)


def create_policy(key: str, params: Mapping[str, Any] | None = None, scenario: ScenarioConfig | None = None) -> SchedulingPolicy:
    """Instantiate a policy. ``scenario`` is required only by ``simuloom_fair`` (parameter defaults)."""
    if key not in _FACTORIES:
        raise KeyError(f"unknown policy '{key}'; available: {available_policies()}")
    return _FACTORIES[key](params or {}, scenario)
