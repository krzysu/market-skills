"""Pure indicator functions — no external dependencies beyond stdlib.
All functions operate on plain Python lists of floats.
"""

from analysis.indicators.fib import compute_fib_levels
from analysis.indicators.momentum import (
    classify_ema_trend,
    classify_rsi,
    classify_squeeze,
    compute_ema,
    compute_macd,
    compute_obv,
    compute_obv_trend,
    compute_rsi,
    compute_sma,
    compute_squeeze,
    detect_crossover,
    detect_obv_divergence,
    ema_slope_pct,
)
from analysis.indicators.swing import (
    cluster_levels,
    find_sr_levels,
    find_swing_high,
    find_swing_highs,
    find_swing_low,
    find_swing_lows,
    swing_window_for_interval,
)
from analysis.indicators.valuation import linreg, pearson_corr, percentile_rank
from analysis.indicators.volatility import (
    compute_atr,
    compute_atr_from_candles,
    log_returns,
    realized_vol,
    stdev,
    true_range,
)


def extract_ohlcv(candles):
    """Extract price/volume arrays from raw candle data."""
    opens = [float(c[1]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]
    return opens, highs, lows, closes, volumes


__all__ = [
    "classify_ema_trend",
    "classify_rsi",
    "classify_squeeze",
    "cluster_levels",
    "compute_atr",
    "compute_atr_from_candles",
    "compute_ema",
    "compute_fib_levels",
    "compute_macd",
    "compute_obv",
    "compute_obv_trend",
    "compute_rsi",
    "compute_sma",
    "compute_squeeze",
    "detect_crossover",
    "detect_obv_divergence",
    "ema_slope_pct",
    "extract_ohlcv",
    "find_sr_levels",
    "find_swing_high",
    "find_swing_highs",
    "find_swing_low",
    "find_swing_lows",
    "linreg",
    "log_returns",
    "pearson_corr",
    "percentile_rank",
    "realized_vol",
    "stdev",
    "swing_window_for_interval",
    "true_range",
]
