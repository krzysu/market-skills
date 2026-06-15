"""market-squeeze — L1 indicator: Bollinger Band / Keltner Channel squeeze momentum."""

from lib.formatting import safe_round
from lib.indicators import classify_squeeze, compute_squeeze, extract_ohlcv, true_range


def analyze(candles, interval="1d", period="1y"):
    bb_length = 20
    kc_length = 20
    needed = max(bb_length, kc_length) + 20

    if not candles or len(candles) < needed:
        return {"error": f"insufficient data (need {needed}+ candles, got {len(candles) if candles else 0})"}

    _, _, _, closes, _ = extract_ohlcv(candles)
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    current_price = closes[-1]

    trs = true_range(candles)

    # Compute squeeze history (last 30 bars)
    history_len = 30
    mom_vals = []
    squeeze_states = []

    for i in range(len(closes) - history_len, len(closes)):
        if i < bb_length:
            mom_vals.append(0.0)
            squeeze_states.append(False)
            continue

        window_closes = closes[: i + 1]
        window_highs = highs[: i + 1]
        window_lows = lows[: i + 1]

        bb_slice = window_closes[-bb_length:]
        bb_mean = sum(bb_slice) / bb_length
        bb_std = (sum((c - bb_mean) ** 2 for c in bb_slice) / bb_length) ** 0.5
        bb_upper = bb_mean + 2.0 * bb_std
        bb_lower = bb_mean - 2.0 * bb_std

        window_trs = trs[:i][-kc_length:]
        if len(window_trs) < kc_length:
            mom_vals.append(0.0)
            squeeze_states.append(False)
            continue

        kc_atr = sum(window_trs) / kc_length
        kc_upper = bb_mean + 1.5 * kc_atr
        kc_lower = bb_mean - 1.5 * kc_atr

        sqz = bb_lower > kc_lower and bb_upper < kc_upper

        mid_hl = (max(window_highs[-bb_length:]) + min(window_lows[-bb_length:])) / 2
        mid_val = (mid_hl + bb_mean) / 2
        mom = window_closes[-1] - mid_val

        mom_vals.append(mom)
        squeeze_states.append(sqz)

    squeeze_on, momentum, direction = compute_squeeze(closes, highs, lows)
    signal = classify_squeeze(momentum, direction)

    histogram = [safe_round(v, 4) if v is not None else None for v in mom_vals[-10:]]

    return {
        "current_price": safe_round(current_price, 2),
        "squeeze_on": squeeze_on,
        "momentum": safe_round(momentum, 4) if momentum is not None else None,
        "direction": direction,
        "signal": signal,
        "histogram_recent": histogram,
    }
