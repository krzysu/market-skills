"""market-breakout — L2 pattern detection: composes L1 indicators to detect breakouts."""

from analysis.indicators import compute_sma, extract_ohlcv
from analysis.skill_loader import load_skill


def analyze(candles, interval="1d", period="1y"):
    """Compose L1 indicators to detect breakout pattern.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with pattern, signals, input_scores, narrative
    """
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {
            "pattern": {
                "present": False,
                "confidence": 1,
                "max_confidence": 5,
                "classification": None,
                "type": "BREAKOUT",
            },
            "signals": {},
            "input_scores": {},
            "narrative": f"insufficient data (need 50+ candles, got {cc})",
        }

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)

    # Load L1 modules
    trend_mod = load_skill("market-trend")
    vol_mod = load_skill("market-volume")
    sr_mod = load_skill("market-s-r")
    sqz_mod = load_skill("market-squeeze")

    # Run L1 analyzers
    err = {"error": "unavailable"}
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    sqz_result = sqz_mod.analyze(candles, interval=interval, period=period) if sqz_mod else err

    # --- Evaluate sub-signals ---
    signals = {}
    total_weight = 0.0
    weighted_sum = 0.0

    # 1. Structure break (weight 0.35)
    alignment = trend_result.get("alignment") if "error" not in trend_result else None
    signal_value = trend_result.get("signal") if "error" not in trend_result else None
    struct_break = alignment in ("FULL_BULL", "FULL_BEAR") or signal_value in ("STRONG_UPTREND", "STRONG_DOWNTREND")
    signals["structure_break"] = {"present": struct_break, "weight": 0.35}
    weighted_sum += 0.35 if struct_break else 0.0
    total_weight += 0.35

    # Determine breakout direction for conditional sub-signals
    is_bullish_break = alignment == "FULL_BULL" or signal_value == "STRONG_UPTREND"
    is_bearish_break = alignment == "FULL_BEAR" or signal_value == "STRONG_DOWNTREND"

    # 2. Volume confirmation (weight 0.25)
    vol_confirmed = False
    if "error" not in vol_result:
        vr = vol_result.get("volume_ratio")
        vol_confirmed = vr is not None and vr > 1.5
    signals["volume_confirmation"] = {"present": vol_confirmed, "weight": 0.25}
    weighted_sum += 0.25 if vol_confirmed else 0.0
    total_weight += 0.25

    # 3. OBV confirmation (weight 0.15)
    obv_confirmed = False
    if "error" not in vol_result:
        obv_trend = vol_result.get("obv_trend")
        if obv_trend == "rising" and is_bullish_break:
            obv_confirmed = True
        elif obv_trend == "falling" and is_bearish_break:
            obv_confirmed = True
    signals["obv_confirmation"] = {"present": obv_confirmed, "weight": 0.15}
    weighted_sum += 0.15 if obv_confirmed else 0.0
    total_weight += 0.15

    # 4. Squeeze release (weight 0.15)
    sqz_on = sqz_result.get("squeeze_on") if "error" not in sqz_result else None
    sqz_dir = sqz_result.get("direction") if "error" not in sqz_result else None
    sqz_mom = sqz_result.get("momentum") if "error" not in sqz_result else None
    sqz_released = sqz_on is False and sqz_dir == "increasing" and sqz_mom is not None and sqz_mom > 0
    signals["squeeze_release"] = {"present": sqz_released, "weight": 0.15}
    weighted_sum += 0.15 if sqz_released else 0.0
    total_weight += 0.15

    # 5. Retest holding (weight 0.10)
    retest_holding = False
    if "error" not in sr_result:
        retest_holding = sr_result.get("sits_on_level") is True
    signals["retest_holding"] = {"present": retest_holding, "weight": 0.10}
    weighted_sum += 0.10 if retest_holding else 0.0
    total_weight += 0.10

    # --- Compute pattern ---
    # Trigger mirrors the bug-scan Shape #1 (absent-with-subs): require at least
    # 2 sub-signals present AND combined weight > 0.30. The pre-fix
    # ``ratio >= 0.40`` threshold dropped the volume_confirmation +
    # retest_holding case (wsum 0.35) into present=False with subs populated —
    # the ghost shape the bug-scan catches. Same pattern as ALGO 4h
    # (BUG-2026-06-24-01) and VVV 4h trend-quality (BUG-2026-06-24-02).
    n_present = sum(1 for sig in signals.values() if sig.get("present"))
    if total_weight > 0:
        confidence = max(1, min(5, round(weighted_sum * 5)))
        present = n_present >= 2 and weighted_sum > 0.30
    else:
        present = False
        confidence = 1

    # Post-squeeze retest sub-shape: squeeze_release + retest_holding both firing
    # is the post-squeeze retest pattern — momentum has broken out of compression,
    # pulled back to the breakout level, and is now holding. Combined weight is
    # 0.25, below the 0.40 threshold, but two corroborating L1s (squeeze + S/R)
    # plus the implied direction from squeeze are meaningful. Trust the combo
    # and classify.
    if not present and sqz_released and retest_holding:
        present = True
        confidence = 3  # two corroborating L1s — middle of the 1..5 range

    # --- Staleness heuristic ---
    stale = False
    if present and struct_break and len(closes) >= 60:
        sma_50 = compute_sma(closes, 50)
        if sma_50 is not None:
            if is_bullish_break and sum(1 for c in closes[-10:] if c > sma_50) >= 9:
                stale = True
            elif is_bearish_break and sum(1 for c in closes[-10:] if c < sma_50) >= 9:
                stale = True

    # --- Classification ---
    classification = None
    if present:
        if signal_value == "SIDEWAYS" and not (sqz_released and retest_holding):
            # SIDEWAYS normally means the structure_break reversed and price returned
            # to consolidation. But the post-squeeze retest sub-shape is a meaningful
            # breakout pattern in its own right — don't downgrade it to FAILED.
            classification = "FAILED"
        elif stale:
            classification = "STALE"
        elif retest_holding:
            classification = "CONFIRMED"
        else:
            classification = "FRESH"

    # --- Build input_scores ---
    input_scores = {}
    if "error" not in trend_result:
        input_scores["market-trend"] = trend_result
    if "error" not in vol_result:
        input_scores["market-volume"] = vol_result
    if "error" not in sr_result:
        input_scores["market-s-r"] = sr_result
    if "error" not in sqz_result:
        input_scores["market-squeeze"] = sqz_result

    # --- Narrative ---
    narrative = _build_narrative(
        classification,
        present,
        struct_break,
        vol_confirmed,
        obv_confirmed,
        sqz_released,
        retest_holding,
        is_bullish_break,
        is_bearish_break,
    )

    return {
        "pattern": {
            "present": present,
            "confidence": confidence,
            "max_confidence": 5,
            "classification": classification,
            "type": "BREAKOUT",
        },
        "signals": signals,
        "input_scores": input_scores,
        "narrative": narrative,
    }


def _build_narrative(
    classification,
    present,
    struct_break,
    vol_confirmed,
    obv_confirmed,
    sqz_released,
    retest_holding,
    is_bullish_break,
    is_bearish_break,
):
    if not present:
        return "No breakout pattern detected; price lacks directional conviction."

    direction = "bullish" if is_bullish_break else "bearish" if is_bearish_break else "directional"

    if classification == "FAILED":
        return "Breakout failed: structure break reversed and price returned to consolidation."
    if classification == "STALE":
        return (
            "Breakout pattern is stale: price has been in breakout territory"
            " for an extended period without continuation."
        )
    if classification == "CONFIRMED":
        return f"Breakout confirmed: {direction} structure with successful retest holding at a key level."
    if classification == "FRESH":
        parts = [f"Fresh {direction} breakout detected"]
        if vol_confirmed:
            parts.append("with above-average volume")
        if obv_confirmed:
            parts.append("and OBV confirmation")
        return parts[0] + (" " + parts[1] if len(parts) > 1 else "") + "."

    return f"{direction.title()} breakout pattern detected with mixed signals."
