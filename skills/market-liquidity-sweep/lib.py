"""market-liquidity-sweep — L2 pattern detection: detects liquidity sweeps and fakeouts."""

from analysis.indicators import extract_ohlcv, find_swing_high, find_swing_low
from analysis.skill_loader import load_skill


def analyze(candles, interval="1d", period="1y"):
    """Compose L1 indicators to detect liquidity sweep / fakeout pattern.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with pattern, signals, input_scores, narrative
    """
    if not candles or len(candles) < 30:
        cc = len(candles) if candles else 0
        return {
            "pattern": {
                "present": False,
                "confidence": 1,
                "max_confidence": 5,
                "classification": None,
                "type": "SWEEP",
            },
            "signals": {},
            "input_scores": {},
            "narrative": f"insufficient data (need 30+ candles, got {cc})",
        }

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]

    # Load L1 modules
    sr_mod = load_skill("market-s-r")
    trend_mod = load_skill("market-trend")
    vol_mod = load_skill("market-volume")

    # Run L1 analyzers
    err = {"error": "unavailable"}
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err

    # Extract S/R values
    nearest_support = sr_result.get("nearest_support") if "error" not in sr_result else None
    nearest_resistance = sr_result.get("nearest_resistance") if "error" not in sr_result else None
    sits_on_level = sr_result.get("sits_on_level", False) if "error" not in sr_result else False

    # --- Evaluate sub-signals ---
    signals = {}
    total_weight = 0.0
    weighted_sum = 0.0

    # 1. Wick through S/R without close beyond (weight 0.35)
    wick_through_support = False
    wick_through_resistance = False
    if nearest_support is not None or nearest_resistance is not None:
        recent_lows = lows[-3:]
        recent_highs = highs[-3:]
        recent_closes = closes[-3:]

        if nearest_support is not None and not sits_on_level:
            for i in range(3):
                if recent_lows[i] < nearest_support and recent_closes[i] > nearest_support:
                    wick_through_support = True
                    break

        if nearest_resistance is not None and not sits_on_level:
            for i in range(3):
                if recent_highs[i] > nearest_resistance and recent_closes[i] < nearest_resistance:
                    wick_through_resistance = True
                    break

    wick_through_sr = wick_through_support or wick_through_resistance
    signals["wick_through_sr"] = {"present": wick_through_sr, "weight": 0.35}
    weighted_sum += 0.35 if wick_through_sr else 0.0
    total_weight += 0.35

    # 2. Immediate reclaim (weight 0.30)
    reclaim = False
    if wick_through_support and nearest_support is not None:
        reclaim = closes[-1] > nearest_support
    elif wick_through_resistance and nearest_resistance is not None:
        reclaim = closes[-1] < nearest_resistance
    signals["immediate_reclaim"] = {"present": reclaim, "weight": 0.30}
    weighted_sum += 0.30 if reclaim else 0.0
    total_weight += 0.30

    # 3. Old high/low taken then reversed (weight 0.20)
    swing_taken = False
    if len(candles) >= 10:
        swing_high_price, _ = find_swing_high(candles, window=5)
        swing_low_price, _ = find_swing_low(candles, window=5)

        if swing_high_price is not None:
            if highs[-1] > swing_high_price and closes[-1] < swing_high_price:
                swing_taken = True
        if not swing_taken and swing_low_price is not None:
            if lows[-1] < swing_low_price and closes[-1] > swing_low_price:
                swing_taken = True
    signals["swing_taken_reversed"] = {"present": swing_taken, "weight": 0.20}
    weighted_sum += 0.20 if swing_taken else 0.0
    total_weight += 0.20

    # 4. Above-avg volume on rejection candle (weight 0.15)
    vol_confirmed = False
    if "error" not in vol_result:
        sma_vol = vol_result.get("sma_volume_20")
        if sma_vol is not None and sma_vol > 0 and len(volumes) >= 5:
            max_recent_vol = max(volumes[-5:])
            vol_confirmed = (max_recent_vol / sma_vol) > 1.5
    signals["above_avg_volume"] = {"present": vol_confirmed, "weight": 0.15}
    weighted_sum += 0.15 if vol_confirmed else 0.0
    total_weight += 0.15

    # --- Compute pattern ---
    # Trigger mirrors bug-scan Shape #1 (absent-with-subs): require at least 2
    # sub-signals present AND combined weight > 0.30. The pre-fix threshold
    # ``ratio >= 0.5`` dropped the 2-sub ``swing_taken + volume`` case (wsum 0.35)
    # into present=False with subs populated — the ghost shape the bug-scan
    # catches. See BUG-2026-06-24-01.
    n_present = sum(1 for sig in signals.values() if sig["present"])
    if total_weight > 0:
        confidence = max(1, min(5, round(weighted_sum * 5)))
        present = n_present >= 2 and weighted_sum > 0.30
    else:
        present = False
        confidence = 1

    # --- Classification ---
    classification = None
    if present:
        if wick_through_support and reclaim:
            classification = "SUPPORT_SWEEP"
        elif wick_through_resistance and reclaim:
            classification = "RESISTANCE_SWEEP"
        elif swing_taken:
            classification = "DOUBLE_TEST"
        elif nearest_support is not None and nearest_resistance is not None:
            support_dist = abs(current_price - nearest_support)
            resistance_dist = abs(current_price - nearest_resistance)
            classification = "SUPPORT_SWEEP" if support_dist <= resistance_dist else "RESISTANCE_SWEEP"
        else:
            classification = "SUPPORT_SWEEP"

    # --- Build input_scores ---
    input_scores = {}
    if "error" not in sr_result:
        input_scores["market-s-r"] = sr_result
    if "error" not in trend_result:
        input_scores["market-trend"] = trend_result
    if "error" not in vol_result:
        input_scores["market-volume"] = vol_result

    # --- Narrative ---
    narrative = _build_narrative(
        classification,
        present,
        wick_through_sr,
        reclaim,
        swing_taken,
        vol_confirmed,
    )

    return {
        "pattern": {
            "present": present,
            "confidence": confidence,
            "max_confidence": 5,
            "classification": classification,
            "type": "SWEEP",
        },
        "signals": signals,
        "input_scores": input_scores,
        "narrative": narrative,
    }


def _build_narrative(classification, present, wick_through_sr, reclaim, swing_taken, vol_confirmed):
    if not present:
        return "No liquidity sweep pattern detected; price action lacks fakeout characteristics."

    if classification == "SUPPORT_SWEEP":
        parts = ["Liquidity sweep of support detected: price wicked below support then reclaimed"]
    elif classification == "RESISTANCE_SWEEP":
        parts = ["Liquidity sweep of resistance detected: price wicked above resistance then reclaimed"]
    elif classification == "DOUBLE_TEST":
        parts = ["Double test detected: old high/low was taken then reversed without clear S/R direction"]
    else:
        parts = ["Liquidity sweep pattern detected"]

    if vol_confirmed:
        parts.append("with above-average volume")
    return parts[0] + (" (" + parts[1] + ")" if len(parts) > 1 else "") + "."
