"""market-trend — Trend structure analysis: EMA alignment, HH/HL, slope."""

from analysis.formatting import safe_round
from analysis.indicators import (
    compute_ema,
    detect_crossover,
    ema_slope_pct,
    extract_ohlcv,
    find_swing_high,
    find_swing_low,
)


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

    # Swing structure — HH/HL detection
    # Find most recent and previous swing highs/lows
    swing_high_price, swing_high_idx = find_swing_high(candles, window=5)
    swing_low_price, swing_low_idx = find_swing_low(candles, window=5)

    # Find previous swing with same window to ensure comparable HH/HL detection
    prev_swing_high_price, prev_swing_high_idx = (
        find_swing_high(candles[:swing_high_idx], window=5) if swing_high_idx > 10 else (None, None)
    )
    prev_swing_low_price, prev_swing_low_idx = (
        find_swing_low(candles[:swing_low_idx], window=5) if swing_low_idx > 10 else (None, None)
    )

    higher_high = None
    if swing_high_price is not None and prev_swing_high_price is not None:
        higher_high = swing_high_price > prev_swing_high_price

    higher_low = None
    if swing_low_price is not None and prev_swing_low_price is not None:
        higher_low = swing_low_price > prev_swing_low_price

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
        score -= 1

    if higher_low is True:
        score += 1
    elif higher_low is False:
        score -= 1

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
