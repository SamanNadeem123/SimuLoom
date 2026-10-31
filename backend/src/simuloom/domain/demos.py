"""Deterministic, clearly synthetic demonstration scenarios (no real student data)."""

from __future__ import annotations

from typing import Any

_FEE_COUNTER: dict[str, Any] = {
    "horizon_minutes": 480,
    "warmup_minutes": 60,
    "counters": 3,
    "arrival": {"process": "poisson", "rate_per_minute": 0.4},
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
    "replications": 10,
}

_BALANCED: dict[str, Any] = {
    "horizon_minutes": 360,
    "warmup_minutes": 30,
    "counters": 4,
    "arrival": {"process": "poisson", "rate_per_minute": 0.5},
    "default_service": {"kind": "gamma", "mean_minutes": 5.0, "cv": 0.7},
    "groups": [
        {"key": "students", "display_name": "Students", "arrival_share": 0.7, "priority_class": 2},
        {"key": "staff", "display_name": "Staff", "arrival_share": 0.3, "priority_class": 1},
    ],
    "abandonment": {"enabled": False},
    "service_level": {"threshold_minutes": 10.0},
    "constraints": [
        {"id": "gap", "type": "MAX_SLA_GAP", "limit": 0.15, "unit": "proportion"},
        {"id": "long", "type": "MAX_LONG_WAIT_RATE", "limit": 0.2, "unit": "proportion"},
    ],
    "replications": 10,
}

_OVERLOADED: dict[str, Any] = {
    "horizon_minutes": 300,
    "warmup_minutes": 30,
    "counters": 2,
    "arrival": {"process": "poisson", "rate_per_minute": 0.55},
    "default_service": {"kind": "exponential", "mean_minutes": 4.0},
    "groups": [
        {"key": "walk_in", "display_name": "Walk-in", "arrival_share": 0.6, "priority_class": 2},
        {"key": "appointment", "display_name": "Appointment", "arrival_share": 0.4, "priority_class": 1},
    ],
    "abandonment": {"enabled": True, "patience": {"kind": "exponential", "mean_minutes": 20.0}},
    "queue_capacity": 40,
    "service_level": {"threshold_minutes": 12.0},
    "constraints": [
        {"id": "disp", "type": "MAX_RELATIVE_WAIT_DISPARITY", "limit": 0.3, "unit": "proportion"},
        {"id": "indiv", "type": "MAX_INDIVIDUAL_WAIT", "limit": 40.0, "unit": "minute"},
    ],
    "replications": 10,
}

DEMOS: dict[str, dict[str, Any]] = {
    "fee-counter-morning-peak": {
        "name": "Fee counter, morning peak (demo)",
        "description": "Synthetic demonstration: three student groups at a three-counter fee office, load about 0.88.",
        "config": _FEE_COUNTER,
    },
    "balanced-help-desk": {
        "name": "Balanced help desk (demo)",
        "description": "Synthetic demonstration: lightly loaded desk, two groups, no abandonment.",
        "config": _BALANCED,
    },
    "overloaded-walk-in-clinic": {
        "name": "Overloaded walk-in clinic (demo)",
        "description": "Synthetic demonstration: overloaded two-counter clinic with abandonment and a finite queue.",
        "config": _OVERLOADED,
    },
}
