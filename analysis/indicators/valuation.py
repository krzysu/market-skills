"""Valuation / statistical functions (percentile rank, correlation, regression)."""

import math


def percentile_rank(value, series):
    """Where does value sit in the historical series (0-100)."""
    if not series:
        return None
    below = sum(1 for v in series if v < value)
    return below / len(series) * 100


def pearson_corr(xs, ys):
    """Pearson correlation coefficient between two equal-length sequences."""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def linreg(values, period):
    """Linear regression value (endpoint) over last `period` values."""
    if len(values) < period:
        return None
    subset = values[-period:]
    n = len(subset)
    sum_x = sum(range(n))
    sum_y = sum(subset)
    sum_xy = sum(i * y for i, y in enumerate(subset))
    sum_x2 = sum(i * i for i in range(n))
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return subset[-1]
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return intercept + slope * (n - 1)
