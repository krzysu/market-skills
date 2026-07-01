"""market-trend — Trend structure analysis: EMA alignment, HH/HL, slope."""

from analysis.formatting import safe_round
from analysis.indicators import (
    compute_ema,
    detect_crossover,
    ema_slope_pct,
    extract_ohlcv,
    find_swing_highs,
    find_swing_lows,
    swing_window_for_interval,
)


def _detect_majority_hh_hl(highs, lows, window):
    """Detect HH/HL using majority of swing pairs over the lookback window.

    Returns (higher_high, higher_low) as True/False/None.
    None when fewer than 2 swing points available.
    """
    swing_highs = find_swing_highs(highs, window)
    swing_lows = find_swing_lows(lows, window)

    higher_high = None
    if len(swing_highs) >= 2:
        recent = swing_highs[-1]
        hh_count = sum(1 for older in swing_highs[:-1] if recent > older)
        pairs = len(swing_highs) - 1
        if hh_count / pairs >= 0.6:
            higher_high = True
        else:
            higher_high = False

    higher_low = None
    if len(swing_lows) >= 2:
        recent = swing_lows[-1]
        hl_count = sum(1 for older in swing_lows[:-1] if recent > older)
        pairs = len(swing_lows) - 1
        if hl_count / pairs >= 0.6:
            higher_low = True
        else:
            higher_low = False

    return higher_high, higher_low


def analyze(candles, interval="1d", period="1y"):
    """Analyze trend structure from OHLC candles.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with trend indicators, score, signal, zone
    """
    if not candles or len(candles) < 50:
        return {"error": f"insufficient data (need 50+ candles, got {len(candles) if candles else 0})"}

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]

    # Compute EMAs
    ema_21, ema_21_series = compute_ema(closes, 21)
    ema_50, ema_50_series = compute_ema(closes, 50)
    ema_100, ema_100_series = compute_ema(closes, 100)
    ema_200, ema_200_series = compute_ema(closes, 200)

    emas = [ema_21, ema_50, ema_100, ema_200]

    # Alignment
    if any(e is None for e in emas):
        # Fall back to fewer EMAs
        valid_emas = [(21, ema_21), (50, ema_50), (100, ema_100), (200, ema_200)]
        valid = [(p, v) for p, v in valid_emas if v is not None]

        if len(valid) < 2:
            alignment = "UNKNOWN"
        else:
            # Check alignment on available EMAs
            ascending = all(valid[i][1] < valid[i + 1][1] for i in range(len(valid) - 1))
            descending = all(valid[i][1] > valid[i + 1][1] for i in range(len(valid) - 1))
            if ascending:
                alignment = "PARTIAL_BULL"
            elif descending:
                alignment = "PARTIAL_BEAR"
            else:
                alignment = "TANGLED"
    elif (
        ema_21 is not None
        and ema_50 is not None
        and ema_100 is not None
        and ema_200 is not None
        and ema_21 > ema_50 > ema_100 > ema_200
    ):
        alignment = "FULL_BULL"
    elif (
        ema_21 is not None
        and ema_50 is not None
        and ema_100 is not None
        and ema_200 is not None
        and ema_200 > ema_100 > ema_50 > ema_21
    ):
        alignment = "FULL_BEAR"
    elif ema_21 is not None and ema_50 is not None and ema_21 > ema_50:
        alignment = "PARTIAL_BULL"
    elif ema_21 is not None and ema_50 is not None and ema_50 > ema_21:
        alignment = "PARTIAL_BEAR"
    else:
        alignment = "TANGLED"

    # Price position vs EMAs
    above_count = sum(1 for e in emas if e is not None and current_price > e)

    # Slope
    slope_21 = ema_slope_pct(ema_21_series) if ema_21_series else None
    slope_50 = ema_slope_pct(ema_50_series) if ema_50_series else None

    # Crossover
    crossover = None
    if ema_21_series and ema_50_series:
        crossover = detect_crossover(ema_21_series, ema_50_series, lookback=5)

    # Swing structure — multi-swing HH/HL detection (window scales with interval)
    swing_window = swing_window_for_interval(interval)
    higher_high, higher_low = _detect_majority_hh_hl(highs, lows, swing_window)

    # Score: -4 to +4
    score = 0

    if alignment == "FULL_BULL":
        score = 2
    elif alignment == "PARTIAL_BULL":
        score = 1
    elif alignment == "FULL_BEAR":
        score = -2
    elif alignment == "PARTIAL_BEAR":
        score = -1

    if higher_high is True:
        score += 1
    elif higher_high is False:
        score -= 0.5

    if higher_low is True:
        score += 1
    elif higher_low is False:
        score -= 0.5

    # Clamp to [-4, 4]
    score = max(-4, min(4, score))

    # Crossover adjustment
    if crossover == "golden_cross" and score < 1:
        score = 1
    elif crossover == "death_cross" and score > -1:
        score = -1

    # Signal
    if score >= 3:
        signal = "STRONG_UPTREND"
        zone = "bullish"
    elif score >= 1:
        signal = "UPTREND"
        zone = "bullish"
    elif score <= -3:
        signal = "STRONG_DOWNTREND"
        zone = "bearish"
    elif score <= -1:
        signal = "DOWNTREND"
        zone = "bearish"
    else:
        signal = "SIDEWAYS"
        zone = "neutral"

    return {
        "current_price": safe_round(current_price, 2),
        "ema_21": safe_round(ema_21, 2) if ema_21 else None,
        "ema_50": safe_round(ema_50, 2) if ema_50 else None,
        "ema_100": safe_round(ema_100, 2) if ema_100 else None,
        "ema_200": safe_round(ema_200, 2) if ema_200 else None,
        "alignment": alignment,
        "price_above_emas": above_count,
        "higher_high": higher_high,
        "higher_low": higher_low,
        "slope_21_pct": safe_round(slope_21, 3) if slope_21 is not None else None,
        "slope_50_pct": safe_round(slope_50, 3) if slope_50 is not None else None,
        "crossover": crossover,
        "score": score,
        "signal": signal,
        "zone": zone,
    }
