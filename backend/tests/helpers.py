from __future__ import annotations

import math

from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.workload.generator import VisitorSpec, Workload, fingerprint


def scripted(rows: list[tuple[float, float]] | list[tuple[float, float, float]], group: str = "a") -> Workload:
    """rows: (arrival, service[, patience]); ids assigned in list order (must be arrival-sorted)."""
    vs = tuple(
        VisitorSpec(i, float(r[0]), group, 1, float(r[1]), float(r[2]) if len(r) > 2 else math.inf, False)
        for i, r in enumerate(rows)
    )
    return Workload(vs, fingerprint(vs), 0, 0)


def base_config(**over: object) -> ScenarioConfig:
    cfg: dict[str, object] = {
        "horizon_minutes": 480,
        "warmup_minutes": 60,
        "counters": 3,
        "arrival": {"process": "poisson", "rate_per_minute": 0.9},
        "default_service": {"kind": "lognormal", "mean_minutes": 6.0, "cv": 0.8},
        "groups": [
            {"key": "undergrad", "display_name": "Undergraduate", "arrival_share": 0.55, "priority_class": 2},
            {"key": "postgrad", "display_name": "Postgraduate", "arrival_share": 0.25, "priority_class": 2},
            {
                "key": "international",
                "display_name": "International",
                "arrival_share": 0.20,
                "priority_class": 1,
                "service": {"kind": "lognormal", "mean_minutes": 9.0, "cv": 0.8},
            },
        ],
        "abandonment": {"enabled": True, "patience": {"kind": "exponential", "mean_minutes": 30.0}},
        "service_level": {"threshold_minutes": 15.0},
        "constraints": [
            {"id": "c1", "type": "MAX_MEAN_WAIT_DISPARITY", "limit": 3.0, "unit": "minute"},
            {"id": "c2", "type": "MIN_SLA_ATTAINMENT", "limit": 0.8, "unit": "proportion"},
            {"id": "c3", "type": "MAX_INDIVIDUAL_WAIT", "limit": 45.0, "unit": "minute"},
        ],
    }
    cfg.update(over)
    return ScenarioConfig.model_validate(cfg)


def scripted_p(rows: list[tuple[float, ...]]) -> Workload:
    """rows: (arrival, service, priority_class[, patience]); ids in list order (arrival-sorted)."""
    vs = tuple(
        VisitorSpec(i, float(r[0]), "a", int(r[2]), float(r[1]), float(r[3]) if len(r) > 3 else math.inf, False)
        for i, r in enumerate(rows)
    )
    return Workload(vs, fingerprint(vs), 0, 0)


def scripted_g(rows: list[tuple], noshow: set[int] | None = None) -> Workload:
    """rows: (arrival, service, group[, patience[, priority_class]]); ids in list order."""
    vs = tuple(
        VisitorSpec(
            i,
            float(r[0]),
            str(r[2]),
            int(r[4]) if len(r) > 4 else 1,
            float(r[1]),
            float(r[3]) if len(r) > 3 and r[3] is not None else math.inf,
            bool(noshow and i in noshow),
        )
        for i, r in enumerate(rows)
    )
    return Workload(vs, fingerprint(vs), 0, 0)


def two_group_config(**over: object) -> ScenarioConfig:
    """Small config with groups x (class 1) and y (class 2), theta=3 min, tiny sample thresholds."""
    cfg: dict[str, object] = {
        "horizon_minutes": 20,
        "warmup_minutes": 0,
        "counters": 1,
        "arrival": {"process": "poisson", "rate_per_minute": 0.1},
        "default_service": {"kind": "deterministic", "value_minutes": 1},
        "groups": [
            {"key": "x", "display_name": "X", "arrival_share": 0.5, "priority_class": 1},
            {"key": "y", "display_name": "Y", "arrival_share": 0.5, "priority_class": 2},
        ],
        "service_level": {"threshold_minutes": 3},
        "min_sample": {"group_n": 2, "p95_n": 2},
    }
    cfg.update(over)
    return ScenarioConfig.model_validate(cfg)
