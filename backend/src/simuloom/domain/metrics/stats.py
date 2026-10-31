"""Statistical helpers: percentile convention and replication-level t-intervals (spec 7.6)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps  # type: ignore[import-untyped,unused-ignore]


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (Hyndman-Fan type 7, the NumPy default)."""
    if not values:
        raise ValueError("percentile of an empty sample")
    return float(np.percentile(np.asarray(values, dtype=float), q, method="linear"))


@dataclass(frozen=True, slots=True)
class Interval:
    mean: float
    std: float | None  # sample standard deviation (n-1); None when n < 2
    n: int
    ci_low: float | None
    ci_high: float | None
    level: float
    zero_variance: bool


def t_interval(values: Sequence[float], level: float, min_n: int) -> Interval:
    """Student-t interval for the mean of *replication-level* values.

    The interval is only produced when ``len(values) >= max(min_n, 2)`` (an engineering default,
    not a statistical guarantee); otherwise bounds are None. Assumes independent replications and
    approximately normal means, which can fail for skewed metrics (documented limitation).
    """
    n = len(values)
    if n == 0:
        raise ValueError("t_interval of an empty sample")
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if n < 2:
        return Interval(mean, None, n, None, None, level, False)
    std = float(arr.std(ddof=1))
    if n < min_n:
        return Interval(mean, std, n, None, None, level, std == 0.0)
    if std == 0.0:
        return Interval(mean, std, n, mean, mean, level, True)
    tq = float(sps.t.ppf(0.5 + level / 2.0, n - 1))
    half = tq * std / math.sqrt(n)
    return Interval(mean, std, n, mean - half, mean + half, level, False)
