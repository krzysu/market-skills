"""Volatility indicator functions (ATR, realized vol, etc.)."""

import math


def stdev(values, period):
    """Population standard deviation of the last `period` values."""
    if len(values) < period:
        return None
    subset = values[-period:]
    mean = sum(subset) / period
    variance = sum((x - mean) ** 2 for x in subset) / period
    return variance**0.5


def true_range(candles):
    """Compute true range series from OHLC candles.

    Candles: list of [[ts, open, high, low, close, volume], ...]
    """
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        prev_close = float(candles[i - 1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return trs


def compute_atr(highs, lows, closes, period=14):
    """Average True Range from price arrays."""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def compute_atr_from_candles(candles, period=14):
    """Average True Range directly from OHLC candles."""
    trs = true_range(candles)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def log_returns(closes):
    """Compute log returns from a list of closing prices."""
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def realized_vol(returns, window):
    """Annualized realized volatility over a rolling window.

    Annualized with sqrt(252) for trading days.
    """
    if len(returns) < window:
        return None
    subset = returns[-window:]
    mean = sum(subset) / len(subset)
    variance = sum((r - mean) ** 2 for r in subset) / (len(subset) - 1)
    return math.sqrt(variance) * math.sqrt(252) * 100
