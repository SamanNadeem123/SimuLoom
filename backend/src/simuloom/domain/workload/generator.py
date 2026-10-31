"""Seeded, policy-independent workload generation (spec 11.3).

Every visitor attribute is drawn here, before any policy runs, from separate random streams
derived with ``SeedSequence(entropy=base_seed, spawn_key=(replication, stream_id))``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from simuloom.domain.config.models import (
    Deterministic,
    DistributionSpec,
    Exponential,
    Gamma,
    Lognormal,
    ScenarioConfig,
    Uniform,
)

SEED_SCHEME_VERSION = 1
STREAM_ARRIVALS, STREAM_GROUPS, STREAM_SERVICE, STREAM_PATIENCE, STREAM_NOSHOW = 1, 2, 3, 4, 5
MIN_DURATION_MINUTES = 1e-6  # service/patience floor so nothing completes at its own start instant
MAX_REALISED_VISITORS = 30_000  # hard cap on a realised workload (limit is 20,000 *expected*)
_CHUNK = 1024


class WorkloadTooLargeError(ValueError):
    """Realised visitor count exceeded the hard cap."""


@dataclass(frozen=True, slots=True)
class VisitorSpec:
    visitor_id: int  # 0-based, ascending with (arrival_time, generation order)
    arrival_time: float  # minutes in [0, horizon)
    group_key: str
    priority_class: int
    service_minutes: float  # > 0, pre-drawn (hidden from policies)
    patience_minutes: float  # > 0, math.inf when abandonment is disabled
    no_show: bool


@dataclass(frozen=True)
class Workload:
    visitors: tuple[VisitorSpec, ...]
    fingerprint: str
    base_seed: int
    replication_index: int
    seed_scheme_version: int = SEED_SCHEME_VERSION


def stream_rng(base_seed: int, replication_index: int, stream_id: int) -> np.random.Generator:
    ss = np.random.SeedSequence(entropy=base_seed, spawn_key=(replication_index, stream_id))
    return np.random.Generator(np.random.PCG64(ss))


def sample(spec: DistributionSpec, rng: np.random.Generator) -> float:
    """One draw in minutes, floored at MIN_DURATION_MINUTES."""
    match spec:
        case Exponential():
            x = rng.exponential(spec.mean_minutes)
        case Lognormal():
            s2 = math.log1p(spec.cv**2)
            x = rng.lognormal(math.log(spec.mean_minutes) - s2 / 2.0, math.sqrt(s2))
        case Gamma():
            x = rng.gamma(1.0 / spec.cv**2, spec.mean_minutes * spec.cv**2)
        case Deterministic():
            x = spec.value_minutes
        case Uniform():
            x = rng.uniform(spec.low_minutes, spec.high_minutes)
    return max(float(x), MIN_DURATION_MINUTES)


def _arrival_times(config: ScenarioConfig, rng: np.random.Generator) -> list[float]:
    arrival = config.arrival
    out: list[float] = []
    t = 0.0
    horizon = config.horizon_minutes
    while True:
        if arrival.process == "poisson":
            gaps = rng.exponential(1.0 / arrival.rate_per_minute, _CHUNK)
        else:
            gaps = np.array([sample(arrival.interarrival, rng) for _ in range(_CHUNK)])
        for gap in gaps:
            t += float(gap)
            if t >= horizon:
                return out
            out.append(t)
            if len(out) > MAX_REALISED_VISITORS:
                raise WorkloadTooLargeError(f"more than {MAX_REALISED_VISITORS} visitors generated")


def fingerprint(visitors: tuple[VisitorSpec, ...]) -> str:
    h = hashlib.sha256()
    for v in visitors:
        h.update(
            f"{v.visitor_id}|{float(v.arrival_time).hex()}|{v.group_key}|{v.priority_class}|"
            f"{float(v.service_minutes).hex()}|{float(v.patience_minutes).hex()}|{int(v.no_show)}\n".encode()
        )
    return "sha256:" + h.hexdigest()


def generate_workload(config: ScenarioConfig, base_seed: int, replication_index: int) -> Workload:
    """Deterministic in (config, base_seed, replication_index); never touches global RNG state."""
    if base_seed < 0 or replication_index < 0:
        raise ValueError("base_seed and replication_index must be non-negative")
    arrivals = _arrival_times(config, stream_rng(base_seed, replication_index, STREAM_ARRIVALS))
    n = len(arrivals)
    groups = config.enabled_groups()
    cum = np.cumsum([g.arrival_share for g in groups])
    cum[-1] = 1.0
    gidx = np.searchsorted(cum, stream_rng(base_seed, replication_index, STREAM_GROUPS).random(n), side="right")
    gidx = np.minimum(gidx, len(groups) - 1)
    srng = stream_rng(base_seed, replication_index, STREAM_SERVICE)
    services = [sample(config.service_spec(groups[int(i)]), srng) for i in gidx]
    if config.abandonment.enabled and config.abandonment.patience is not None:
        prng = stream_rng(base_seed, replication_index, STREAM_PATIENCE)
        pat = [sample(config.abandonment.patience, prng) for _ in range(n)]
    else:
        pat = [math.inf] * n
    if config.no_show.enabled:
        ns = stream_rng(base_seed, replication_index, STREAM_NOSHOW).random(n) < config.no_show.probability
    else:
        ns = np.zeros(n, dtype=bool)
    visitors = tuple(
        VisitorSpec(
            visitor_id=k,
            arrival_time=arrivals[k],
            group_key=groups[int(gidx[k])].key,
            priority_class=groups[int(gidx[k])].priority_class,
            service_minutes=services[k],
            patience_minutes=pat[k],
            no_show=bool(ns[k]),
        )
        for k in range(n)
    )
    return Workload(visitors, fingerprint(visitors), base_seed, replication_index)
