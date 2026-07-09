"""Momentum / trend / oscillator indicator functions."""


def compute_ema(values, period):
    """Compute EMA from a list of values (oldest first).

    Returns (final_ema, full_series).
    """
    if len(values) < period:
        return None, []
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    series = [ema]
    for price in values[period:]:
        ema = price * k + ema * (1 - k)
        series.append(ema)
    return ema, series


def compute_sma(values, period):
    """Simple moving average of the last `period` values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def compute_rsi(closes, period=14):
    """Compute RSI using Wilder exponential smoothing."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas[:period]]
    losses = [max(-d, 0) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def classify_rsi(rsi):
    """Classify RSI into human-readable signal."""
    if rsi is None:
        return "UNKNOWN"
    if rsi < 30:
        return "OVERSOLD"
    elif rsi < 40:
        return "APPROACHING OVERSOLD"
    elif rsi <= 60:
        return "NEUTRAL"
    elif rsi <= 70:
        return "APPROACHING OVERBOUGHT"
    else:
        return "OVERBOUGHT"


def compute_squeeze(closes, highs, lows, bb_period=20, bb_mult=2.0, kc_period=20, kc_mult=1.5):
    """Squeeze momentum: returns (squeeze_on, momentum, direction)."""
    if len(closes) < bb_period + 1 or len(highs) < bb_period + 1 or len(lows) < bb_period + 1:
        return None, None, None

    bb_closes = closes[-bb_period:]
    bb_mean = sum(bb_closes) / bb_period
    bb_std = (sum((c - bb_mean) ** 2 for c in bb_closes) / bb_period) ** 0.5
    bb_upper = bb_mean + bb_mult * bb_std
    bb_lower = bb_mean - bb_mult * bb_std

    trs = []
    for i in range(-kc_period, 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr = sum(trs) / len(trs)
    kc_upper = bb_mean + kc_mult * atr
    kc_lower = bb_mean - kc_mult * atr

    squeeze_on = bb_lower > kc_lower and bb_upper < kc_upper

    mid_hl = (max(highs[-bb_period:]) + min(lows[-bb_period:])) / 2
    mid_val = (mid_hl + bb_mean) / 2
    momentum = closes[-1] - mid_val

    direction = None
    if len(closes) >= bb_period + 1:
        prev_mid_hl = (max(highs[-bb_period - 1 : -1]) + min(lows[-bb_period - 1 : -1])) / 2
        prev_bb = sum(closes[-bb_period - 1 : -1]) / bb_period
        prev_mom = closes[-2] - (prev_mid_hl + prev_bb) / 2
        direction = "increasing" if momentum > prev_mom else "decreasing"

    return squeeze_on, momentum, direction


def classify_squeeze(momentum, direction):
    """Classify squeeze momentum into a signal string."""
    if momentum is None:
        return "UNKNOWN"
    if momentum > 0 and direction == "increasing":
        return "BULLISH"
    elif momentum > 0:
        return "BULLISH FADING"
    elif momentum < 0 and direction == "decreasing":
        return "BEARISH"
    elif momentum < 0:
        return "BEARISH FADING"
    return "FLAT"


def compute_obv(closes, volumes):
    """On-Balance Volume: running total, adding on up days, subtracting on down."""
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


def compute_obv_trend(closes, volumes, sma_period=20):
    """On-Balance Volume trend: 'rising' or 'falling' vs SMA."""
    if len(closes) < sma_period + 1:
        return None
    obv = compute_obv(closes, volumes)
    sma = sum(obv[-sma_period:]) / sma_period
    return "rising" if obv[-1] > sma else "falling"


def detect_crossover(short_series, long_series, lookback=5):
    """Detect if short EMA crossed long EMA within the last `lookback` bars.

    Returns: 'golden_cross', 'death_cross', or None.
    """
    if len(short_series) < lookback + 1 or len(long_series) < lookback + 1:
        return None

    min_len = min(len(short_series), len(long_series))
    short = short_series[-min_len:]
    long_ = long_series[-min_len:]

    for i in range(-lookback, 0):
        prev_above = short[i - 1] > long_[i - 1]
        curr_above = short[i] > long_[i]
        if not prev_above and curr_above:
            return "golden_cross"
        if prev_above and not curr_above:
            return "death_cross"
    return None


def ema_slope_pct(series, period=5):
    """Slope as percentage change over `period` intervals (period+1 data points)."""
    if len(series) < period + 1:
        return None
    return (series[-1] - series[-period - 1]) / series[-period - 1] * 100


def classify_ema_trend(ema21, ema50, price):
    """Classify EMA trend into (signal, score).

    Returns one of: BULLISH(2), BEARISH(-2), LEAN_BULLISH(1),
    LEAN_BEARISH(-1), UNKNOWN(0).
    """
    if ema21 is None or ema50 is None:
        return "UNKNOWN", 0
    if ema21 > ema50 and price > ema21:
        return "BULLISH", 2
    if ema21 < ema50 and price < ema21:
        return "BEARISH", -2
    if price > ema21:
        return "LEAN_BULLISH", 1
    return "LEAN_BEARISH", -1


def compute_macd(closes, fast=12, slow=26, signal=9):
    """MACD: returns (macd_line, signal_line, histogram)."""
    n = len(closes)
    _, ema_fast_full = compute_ema(closes, fast)
    _, ema_slow_full = compute_ema(closes, slow)

    fast_padded = [None] * (n - len(ema_fast_full)) + ema_fast_full
    slow_padded = [None] * (n - len(ema_slow_full)) + ema_slow_full

    macd_line = [f - s if f is not None and s is not None else None for f, s in zip(fast_padded, slow_padded)]

    valid = [v for v in macd_line if v is not None]
    if len(valid) < signal:
        sig_padded = [None] * n
        histogram = [None] * n
        return macd_line, sig_padded, histogram

    _, sig_full = compute_ema(valid, signal)
    sig_padded = [None] * (n - len(sig_full)) + sig_full
    histogram = [(m - s) if m is not None and s is not None else None for m, s in zip(macd_line, sig_padded)]
    return macd_line, sig_padded, histogram


def detect_obv_divergence(closes, volumes, swing_window=14, lookback=28):
    """Detect bullish or bearish OBV divergence."""
    n = len(closes)
    if n < lookback * 2:
        return None
    obv = compute_obv(closes, volumes)
    recent = range(n - lookback, n)
    prior = range(n - lookback * 2, n - lookback)

    recent_low_idx = min(recent, key=lambda i: closes[i])
    prior_low_idx = min(prior, key=lambda i: closes[i])

    if closes[recent_low_idx] < closes[prior_low_idx] and obv[recent_low_idx] > obv[prior_low_idx]:
        return "bullish"

    recent_high_idx = max(recent, key=lambda i: closes[i])
    prior_high_idx = max(prior, key=lambda i: closes[i])

    if closes[recent_high_idx] > closes[prior_high_idx] and obv[recent_high_idx] < obv[prior_high_idx]:
        return "bearish"

    return None
