"""Metric value types shared by the metrics engine, fairness evaluator and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

CALC_VERSION = "1.0.0"
ALL_GROUPS = "__all__"  # group_key sentinel for non-group metrics (spec 5.2)

# Machine-readable reason codes for NOT_EVALUABLE results.
NO_OBSERVATIONS = "NO_OBSERVATIONS"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
EXCESSIVE_CENSORING = "EXCESSIVE_CENSORING"
FEATURE_DISABLED = "FEATURE_DISABLED"
INSUFFICIENT_GROUPS = "INSUFFICIENT_GROUPS"
BASELINE_TOO_SMALL = "BASELINE_TOO_SMALL"
TOO_FEW_REPLICATIONS = "TOO_FEW_REPLICATIONS"
EXCESSIVE_EXCLUSION = "EXCESSIVE_EXCLUSION"


class EvalStatus(StrEnum):
    OK = "OK"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One metric result. ``value`` is None exactly when ``status`` is NOT_EVALUABLE."""

    value: float | None
    unit: str
    n: int  # sample size / denominator the value is based on
    status: EvalStatus = EvalStatus.OK
    reason: str | None = None

    @staticmethod
    def ok(value: float, unit: str, n: int) -> MetricValue:
        return MetricValue(float(value), unit, n)

    @staticmethod
    def not_evaluable(unit: str, n: int, reason: str) -> MetricValue:
        return MetricValue(None, unit, n, EvalStatus.NOT_EVALUABLE, reason)

    @property
    def evaluable(self) -> bool:
        return self.status is EvalStatus.OK and self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "n": self.n,
            "status": self.status.value,
            "reason": self.reason,
        }


# key -> (unit, description). Served by GET /api/v1/metadata and used by the UI for tooltips.
METRIC_CATALOG: dict[str, tuple[str, str]] = {
    "wait_mean": (
        "minute",
        "Mean waiting time of visitors who started service (started-only; biased low under overload).",
    ),
    "wait_mean_incl_censored": (
        "minute",
        "Mean wait including abandoned and still-waiting visitors (elapsed waits are lower bounds).",
    ),
    "wait_median": ("minute", "Median waiting time of visitors who started service."),
    "wait_p95": ("minute", "95th-percentile waiting time (linear interpolation, Hyndman-Fan type 7)."),
    "throughput_per_hour": (
        "visitor/hour",
        "Completed services in the observation window divided by its length in hours.",
    ),
    "utilization": (
        "proportion",
        "Busy counter-time divided by available counter-time in the observation window.",
    ),
    "queue_length_mean": (
        "visitor",
        "Time-weighted mean number of visitors waiting in the observation window.",
    ),
    "queue_length_peak": ("visitor", "Maximum number of visitors waiting in the observation window."),
    "abandonment_rate": (
        "proportion",
        "Abandoned visitors divided by eligible visitors who joined the queue (no-shows excluded).",
    ),
    "blocking_rate": (
        "proportion",
        "Visitors turned away by the queue capacity divided by eligible visitors.",
    ),
    "sla_attainment": (
        "proportion",
        "Share of determined visitors served within the service-level threshold (abandoned/blocked count as failures).",
    ),
    "long_wait_rate": (
        "proportion",
        "Share of determined visitors whose realised or elapsed wait exceeds the threshold.",
    ),
    "completed_count": ("visitor", "Services completed inside the observation window."),
    "eligible_count": ("visitor", "Visitors arriving in the observation window who were not no-shows."),
    "max_individual_wait": ("minute", "Largest realised or elapsed wait of any eligible visitor."),
}
