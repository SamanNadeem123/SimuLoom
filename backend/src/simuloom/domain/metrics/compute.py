"""Per-run metrics computed from a recorded run (spec section 7). Pure functions, no simulation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.metrics.stats import percentile
from simuloom.domain.metrics.types import (
    ALL_GROUPS,
    CALC_VERSION,
    EXCESSIVE_CENSORING,
    FEATURE_DISABLED,
    INSUFFICIENT_SAMPLE,
    NO_OBSERVATIONS,
    MetricValue,
)
from simuloom.domain.recording.recorder import RunRecord, StepPoint, VisitorState

_STARTED = {VisitorState.COMPLETED, VisitorState.IN_SERVICE_AT_HORIZON}
MIN, PROP, VIS, PER_HOUR = "minute", "proportion", "visitor", "visitor/hour"


@dataclass(frozen=True)
class RunMetrics:
    overall: dict[str, MetricValue]
    groups: dict[str, dict[str, MetricValue]]
    counts: dict[str, dict[str, int]]  # ALL_GROUPS and each group key
    individual_max_wait: MetricValue
    warnings: tuple[str, ...]
    calc_version: str = CALC_VERSION


@dataclass(frozen=True, slots=True)
class _Obs:
    group: str
    state: VisitorState
    wait: float | None  # realised wait, only for started visitors
    elapsed: float | None  # elapsed wait of abandoned / still-waiting visitors


def _integrate(steps: Sequence[StepPoint], a: float, b: float, pick: Callable[[StepPoint], int]) -> float:
    total = 0.0
    for i, p in enumerate(steps):
        end = steps[i + 1].time if i + 1 < len(steps) else b
        lo, hi = max(p.time, a), min(end, b)
        if hi > lo:
            total += pick(p) * (hi - lo)
    return total


def _peak(steps: Sequence[StepPoint], a: float, b: float, pick: Callable[[StepPoint], int]) -> int:
    best = 0
    for i, p in enumerate(steps):
        end = steps[i + 1].time if i + 1 < len(steps) else float("inf")
        if p.time <= b and end > a:
            best = max(best, pick(p))
    return best


def _classify(obs: Sequence[_Obs], theta: float) -> tuple[int, int, int, list[float]]:
    """Return (sla_successes, sla_failures, undetermined, determined_waits).

    determined_waits: realised-or-elapsed waits of E_det (excludes BLOCKED and undetermined U).
    """
    succ = fail = und = 0
    det_waits: list[float] = []
    for o in obs:
        if o.state in _STARTED:
            assert o.wait is not None
            det_waits.append(o.wait)
            if o.wait <= theta:
                succ += 1
            else:
                fail += 1
        elif o.state is VisitorState.ABANDONED:
            assert o.elapsed is not None
            det_waits.append(o.elapsed)
            fail += 1
        elif o.state is VisitorState.BLOCKED:
            fail += 1
        elif o.state is VisitorState.WAITING_AT_HORIZON:
            assert o.elapsed is not None
            if o.elapsed > theta:
                det_waits.append(o.elapsed)
                fail += 1
            else:
                und += 1
    return succ, fail, und, det_waits


def _block(
    obs: Sequence[_Obs],
    cfg: ScenarioConfig,
    *,
    n_min: int,
    p95_min: int,
    apply_cen: bool,
) -> tuple[dict[str, MetricValue], dict[str, int]]:
    theta = cfg.service_level.threshold_minutes
    max_cen = cfg.statistics.max_censored_fraction
    n_elig = len(obs)
    started = [o.wait for o in obs if o.state in _STARTED and o.wait is not None]
    incl = started + [o.elapsed for o in obs if o.elapsed is not None]
    n_wait_h = sum(1 for o in obs if o.state is VisitorState.WAITING_AT_HORIZON)
    n_aband = sum(1 for o in obs if o.state is VisitorState.ABANDONED)
    n_block = sum(1 for o in obs if o.state is VisitorState.BLOCKED)
    n_insvc = sum(1 for o in obs if o.state is VisitorState.IN_SERVICE_AT_HORIZON)
    succ, fail, und, det_waits = _classify(obs, theta)
    cen_excess = apply_cen and n_elig > 0 and (n_wait_h / n_elig) > max_cen

    def gate(n: int, need: int, unit: str, use_cen: bool = True) -> MetricValue | None:
        if n == 0:
            return MetricValue.not_evaluable(unit, n, NO_OBSERVATIONS)
        if n < need:
            return MetricValue.not_evaluable(unit, n, INSUFFICIENT_SAMPLE)
        if use_cen and cen_excess:
            return MetricValue.not_evaluable(unit, n, EXCESSIVE_CENSORING)
        return None

    m: dict[str, MetricValue] = {}
    g = gate(len(started), n_min, MIN)
    m["wait_mean"] = g or MetricValue.ok(sum(started) / len(started) if started else 0.0, MIN, len(started))
    g = gate(len(started), n_min, MIN)
    m["wait_median"] = g or MetricValue.ok(percentile(started, 50) if started else 0.0, MIN, len(started))
    g = gate(len(started), p95_min, MIN)
    m["wait_p95"] = g or MetricValue.ok(percentile(started, 95) if started else 0.0, MIN, len(started))
    g = gate(len(incl), n_min, MIN, use_cen=False)
    m["wait_mean_incl_censored"] = g or MetricValue.ok(sum(incl) / len(incl) if incl else 0.0, MIN, len(incl))

    n_det = succ + fail
    g = gate(n_det, n_min, PROP)
    m["sla_attainment"] = g or MetricValue.ok(succ / n_det if n_det else 0.0, PROP, n_det)
    n_edet = n_elig - n_block - und  # E_det: excludes BLOCKED and undetermined
    g = gate(n_edet, n_min, PROP)
    long_n = sum(1 for w in det_waits if w > theta)
    m["long_wait_rate"] = g or MetricValue.ok(long_n / n_edet if n_edet else 0.0, PROP, n_edet)

    joined = n_elig - n_block
    if not cfg.abandonment.enabled:
        m["abandonment_rate"] = MetricValue.not_evaluable(PROP, joined, FEATURE_DISABLED)
    else:
        g = gate(joined, n_min, PROP, use_cen=False)
        m["abandonment_rate"] = g or MetricValue.ok(n_aband / joined if joined else 0.0, PROP, joined)
    if cfg.queue_capacity is None:
        m["blocking_rate"] = MetricValue.not_evaluable(PROP, n_elig, FEATURE_DISABLED)
    else:
        g = gate(n_elig, n_min, PROP, use_cen=False)
        m["blocking_rate"] = g or MetricValue.ok(n_block / n_elig if n_elig else 0.0, PROP, n_elig)

    counts = {
        "eligible": n_elig,
        "started": len(started),
        "abandoned": n_aband,
        "blocked": n_block,
        "waiting_at_horizon": n_wait_h,
        "in_service_at_horizon": n_insvc,
        "undetermined": und,
        "sla_successes": succ,
        "sla_failures": fail,
    }
    return m, counts


def compute_run_metrics(record: RunRecord, config: ScenarioConfig) -> RunMetrics:
    horizon = record.horizon_minutes
    tw = config.warmup_minutes
    length = horizon - tw
    if length <= 0:
        raise ValueError("observation window must have positive length")
    if record.counters != config.counters:
        raise ValueError("record and config disagree on the number of counters")

    obs: list[_Obs] = []
    for o in record.outcomes:
        if not (tw <= o.arrival_time < horizon) or o.status is VisitorState.NO_SHOW:
            continue
        wait = o.service_start - o.arrival_time if o.service_start is not None else None
        elapsed: float | None = None
        if o.status is VisitorState.ABANDONED and o.abandonment is not None:
            elapsed = o.abandonment - o.arrival_time
        elif o.status is VisitorState.WAITING_AT_HORIZON:
            elapsed = horizon - o.arrival_time
        obs.append(_Obs(o.group_key, o.status, wait, elapsed))

    n_min_groups = config.min_sample.group_n
    overall, counts_all = _block(obs, config, n_min=1, p95_min=config.min_sample.p95_n, apply_cen=False)

    completed = sum(1 for o in record.outcomes if o.completion is not None and tw <= o.completion <= horizon)
    overall["completed_count"] = MetricValue.ok(completed, VIS, completed)
    overall["eligible_count"] = MetricValue.ok(len(obs), VIS, len(obs))
    overall["throughput_per_hour"] = MetricValue.ok(completed / (length / 60.0), PER_HOUR, completed)
    busy_int = _integrate(record.steps, tw, horizon, lambda p: p.busy_counters)
    overall["utilization"] = MetricValue.ok(busy_int / (record.counters * length), PROP, record.counters)
    q_int = _integrate(record.steps, tw, horizon, lambda p: p.queue_length)
    overall["queue_length_mean"] = MetricValue.ok(q_int / length, VIS, 1)
    overall["queue_length_peak"] = MetricValue.ok(_peak(record.steps, tw, horizon, lambda p: p.queue_length), VIS, 1)

    waits_incl = [o.wait if o.wait is not None else o.elapsed for o in obs]
    waits_incl_f = [w for w in waits_incl if w is not None]
    if waits_incl_f:
        indiv = MetricValue.ok(max(waits_incl_f), MIN, len(waits_incl_f))
    else:
        indiv = MetricValue.not_evaluable(MIN, 0, NO_OBSERVATIONS)
    overall["max_individual_wait"] = indiv

    groups: dict[str, dict[str, MetricValue]] = {}
    counts: dict[str, dict[str, int]] = {ALL_GROUPS: counts_all}
    for grp in config.enabled_groups():
        gobs = [o for o in obs if o.group == grp.key]
        gm, gc = _block(gobs, config, n_min=n_min_groups, p95_min=config.min_sample.p95_n, apply_cen=True)
        groups[grp.key] = gm
        counts[grp.key] = gc

    warnings: list[str] = []
    if counts_all["eligible"] and counts_all["waiting_at_horizon"] / counts_all["eligible"] > 0.05:
        warnings.append(
            f"{counts_all['waiting_at_horizon']} of {counts_all['eligible']} eligible visitors were still "
            "waiting at the horizon (censored); started-only waits are biased low."
        )
    if counts_all["undetermined"]:
        warnings.append(
            f"{counts_all['undetermined']} visitors have an undetermined service-level outcome "
            "(still waiting at the horizon within the threshold) and are excluded from SLA."
        )
    return RunMetrics(overall, groups, counts, indiv, tuple(warnings))


def metric_rows(rm: RunMetrics) -> list[tuple[str, str, MetricValue]]:
    """Flatten to (metric_key, group_key, value) rows for persistence/export."""
    rows: list[tuple[str, str, MetricValue]] = [(k, ALL_GROUPS, v) for k, v in sorted(rm.overall.items())]
    for g in sorted(rm.groups):
        rows.extend((k, g, v) for k, v in sorted(rm.groups[g].items()))
    return rows
