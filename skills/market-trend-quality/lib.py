"""market-trend-quality — L2 pattern detection: assesses trend health by composing L1 indicators."""

from analysis.indicators import compute_atr_from_candles, extract_ohlcv
from analysis.skill_loader import load_skill


def _price_above_ema50(current_price, ema_50):
    return current_price is not None and ema_50 is not None and current_price > ema_50


def _present_sub_signal_count(signals: dict) -> int:
    """Count sub-signals whose `present` field is True.

    Used by the sub-signal count fallbacks to catch configurations where one
    sub-signal contributes negatively (e.g. deep pullback → -0.20) but the
    overall directional signal is still strong (3+ sub-signals present).
    """
    return sum(1 for s in signals.values() if isinstance(s, dict) and s.get("present") is True)


def analyze(candles, interval="1d", period="1y"):
    """Compose L1 indicators to assess trend quality/health.

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
                "type": "TREND_QUALITY",
            },
            "signals": {},
            "input_scores": {},
            "narrative": f"insufficient data (need 50+ candles, got {cc})",
        }

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)

    # Load L1 modules
    trend_mod = load_skill("market-trend")
    fib_mod = load_skill("market-fibonacci")
    vol_mod = load_skill("market-volume")
    ema_mod = load_skill("market-ema")

    # Run L1 analyzers
    err = {"error": "unavailable"}
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err
    fib_result = fib_mod.analyze(candles, interval=interval, period=period) if fib_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err
    ema_result = ema_mod.analyze(candles, interval=interval, period=period) if ema_mod else err

    # --- Evaluate sub-signals ---
    signals = {}
    signed_score = 0.0  # range [-1, 1]

    # 1. EMA alignment (weight 0.25)
    alignment = trend_result.get("alignment") if "error" not in trend_result else None
    if alignment is None and "error" not in ema_result:
        alignment = ema_result.get("alignment")
    if alignment is None:
        ema_present = False
        ema_contribution = 0.0
    else:
        ema_present = alignment in ("FULL_BULL", "FULL_BEAR", "PARTIAL_BULL", "PARTIAL_BEAR")
        if alignment == "FULL_BULL":
            ema_contribution = 0.25
        elif alignment == "FULL_BEAR":
            ema_contribution = -0.25
        elif alignment == "PARTIAL_BULL":
            ema_contribution = 0.125
        elif alignment == "PARTIAL_BEAR":
            ema_contribution = -0.125
        else:
            ema_contribution = 0.0
    signals["ema_alignment"] = {"present": ema_present, "weight": 0.25}
    signed_score += ema_contribution

    # 2. HH/HL integrity (weight 0.25)
    hh = trend_result.get("higher_high") if "error" not in trend_result else None
    hl = trend_result.get("higher_low") if "error" not in trend_result else None
    hh_hl_present = False
    hh_hl_contribution = 0.0
    if hh is True and hl is True:
        hh_hl_present = True
        hh_hl_contribution = 0.25
    elif hh is False and hl is False:
        hh_hl_present = True
        hh_hl_contribution = -0.25
    signals["hh_hl_integrity"] = {"present": hh_hl_present, "weight": 0.25}
    signed_score += hh_hl_contribution

    # 3. Pullback depth (weight 0.20)
    pullback_present = False
    pullback_contribution = 0.0
    if "error" not in fib_result:
        fib_distance = fib_result.get("nearest_fib_distance_pct")
        if fib_distance is not None:
            pullback_present = True
            if fib_distance < 3:
                pullback_contribution = 0.20
            elif fib_distance > 8:
                pullback_contribution = -0.20
    signals["pullback_depth"] = {"present": pullback_present, "weight": 0.20}
    signed_score += pullback_contribution

    # 4. Impulse vs retrace ratio (weight 0.15) — measured directly from closes
    impulse_present = False
    impulse_contribution = 0.0
    if len(closes) >= 13:
        recent_return = closes[-1] / closes[-7] - 1
        prior_return = closes[-7] / closes[-13] - 1
        atr = compute_atr_from_candles(candles, period=14)
        atr_pct = (atr / closes[-1]) if atr is not None and closes[-1] else 0

        if recent_return > 0 and prior_return < 0 and abs(recent_return) >= 0.5 * atr_pct:
            impulse_present = True
            impulse_contribution = 0.15
        elif recent_return < 0 and prior_return > 0 and abs(recent_return) >= 0.5 * atr_pct:
            impulse_present = True
            impulse_contribution = -0.15
        elif recent_return > 0 and prior_return > 0 and abs(recent_return) < abs(prior_return):
            impulse_present = True
            impulse_contribution = -0.075
    signals["impulse_vs_retrace"] = {"present": impulse_present, "weight": 0.15}
    signed_score += impulse_contribution

    # 5. Volume confirmation (weight 0.15) — accept quiet accumulation, preserve distribution signal
    vol_present = False
    vol_contribution = 0.0
    if "error" not in vol_result:
        vr = vol_result.get("volume_ratio")
        obv_trend = vol_result.get("obv_trend")
        ema_50 = trend_result.get("ema_50") if "error" not in trend_result else None
        current_price = trend_result.get("current_price") if "error" not in trend_result else None
        if vr is not None:
            if vr > 1.5 and obv_trend == "rising":
                vol_present, vol_contribution = True, 0.15
            elif vr >= 1.0 and obv_trend == "falling":
                vol_present, vol_contribution = True, -0.15
            elif vr >= 0.7 and (obv_trend == "rising" or _price_above_ema50(current_price, ema_50)):
                vol_present, vol_contribution = True, 0.075
            elif vr < 0.5 and obv_trend == "falling":
                vol_present, vol_contribution = True, -0.15
    signals["volume_confirmation"] = {"present": vol_present, "weight": 0.15}
    signed_score += vol_contribution

    # weighted_sum is the absolute-weight sum of present sub-signals (range [0, 1]).
    # Mirrors the bug-scan Shape #1 trigger (``wsum > 0.30``) and the
    # market-liquidity-sweep L2 trigger (BUG-2026-06-24-01): use the count +
    # weight check, not ``signed_score`` — opposing signs can dilute
    # ``signed_score`` while the directional signal is still strong (see
    # BUG-2026-06-24-02, VVV 4h trend-quality: ema FULL_BEAR + hh_hl intact +
    # pullback shallow → signed_score 0.20 with wsum 0.70).
    weighted_sum = sum(s["weight"] for s in signals.values() if s.get("present"))

    # --- Classification (sole source of truth for present) ---
    trend_score = trend_result.get("score", 0) if "error" not in trend_result else 0
    alignment = trend_result.get("alignment", "TANGLED") if "error" not in trend_result else "TANGLED"

    hh_intact = hh is True and hl is True
    hh_broken = hh is False and hl is False
    hh_breaking = (hh is False) or (hl is False)

    ema_bullish = alignment in ("FULL_BULL", "PARTIAL_BULL")
    ema_bearish = alignment in ("FULL_BEAR", "PARTIAL_BEAR")
    ema_tangled = alignment in ("TANGLED", "UNKNOWN")

    # --- Determine if impulse is a genuine bullish reversal (recent bounce, not just deceleration) ---
    impulse_bullish_reversal = (
        impulse_present and impulse_contribution >= 0.1 and recent_return > 0 and prior_return < 0
    )

    classification = None
    # HEALTHY_PULLBACK_UPTREND: check BEFORE the strict HEALTHY_UPTREND
    if impulse_bullish_reversal and ema_bullish and (hh is True or (hh is not False and hl is not False)):
        has_price_context = trend_result.get("ema_50") is not None and trend_result.get("current_price") is not None
        price_above_ema50 = has_price_context and trend_result.get("current_price", 0) > trend_result.get("ema_50", 0)
        if price_above_ema50:
            classification = "HEALTHY_PULLBACK_UPTREND"
    if classification is None and trend_score >= 3 and hh_intact and ema_bullish:
        classification = "HEALTHY_UPTREND"
    elif classification is None and trend_score <= -3 and hh_broken and ema_bearish:
        classification = "HEALTHY_DOWNTREND"
    elif classification is None and (1 <= trend_score <= 2 or -2 <= trend_score <= -1):
        classification = "WEAKENING"
    elif classification is None and hh_breaking and ema_tangled:
        classification = "DEGRADING"
    elif classification is None and signed_score >= 0.75:
        # Sub-signal sum is strongly directional even though L1 trend_score didn't
        # reach the HEALTHY_UPTREND gate — classify so pattern.present isn't false.
        classification = "WEAKENING"
    elif classification is None and signed_score <= -0.75:
        classification = "WEAKENING"
    elif classification is None and _present_sub_signal_count(signals) >= 4:
        # 4+ present sub-signals with signed_score that may have dropped below 0.75
        # via one negative contribution (e.g. deep pullback → -0.20). Directional
        # signal is still strong enough to classify.
        classification = "WEAKENING"
    elif classification is None and _present_sub_signal_count(signals) == 3 and weighted_sum > 0.30:
        # 3 present sub-signals trigger WEAKENING absent a sign-dilution trap.
        # The pre-fix ``abs(signed_score) >= 0.50`` gate silently dropped cases
        # where one sub-signal contributes opposite to the others (e.g. ema
        # FULL_BEAR against hh_hl intact + pullback shallow), pushing
        # signed_score below 0.50 despite wsum being well above the bug-scan
        # Shape #1 trigger (0.30). Mirrors the ALGO 4h liquidity-sweep fix
        # (BUG-2026-06-24-01) which replaced a signed_score-style threshold
        # with a count + weight check.
        classification = "WEAKENING"

    # --- Compute pattern ---
    abs_score = abs(signed_score)
    present = classification is not None
    confidence = max(1, min(5, round(abs_score * 5))) if classification is not None else 1

    # --- Build input_scores ---
    input_scores = {}
    if "error" not in trend_result:
        input_scores["market-trend"] = trend_result
    if "error" not in fib_result:
        input_scores["market-fibonacci"] = fib_result
    if "error" not in vol_result:
        input_scores["market-volume"] = vol_result

    # --- Narrative ---
    narrative = _build_narrative(classification, trend_score, alignment, hh_intact)

    return {
        "pattern": {
            "present": present,
            "confidence": confidence,
            "max_confidence": 5,
            "classification": classification,
            "type": "TREND_QUALITY",
        },
        "signals": signals,
        "input_scores": input_scores,
        "narrative": narrative,
    }


def _build_narrative(classification, trend_score, alignment, hh_intact):
    if classification == "HEALTHY_PULLBACK_UPTREND":
        return "Healthy uptrend in pullback phase — waiting for bounce confirmation."
    if classification == "HEALTHY_UPTREND":
        return f"Healthy uptrend with score {trend_score}, EMA alignment {alignment}, and intact HH/HL structure."
    if classification == "HEALTHY_DOWNTREND":
        return f"Healthy downtrend with score {trend_score}, EMA alignment {alignment}, and broken HH/HL structure."
    if classification == "WEAKENING":
        return f"Trend weakening with score {trend_score}, showing conflicting signals between sub-components."
    if classification == "DEGRADING":
        return "Trend degrading as HH/HL structure breaks down and EMA alignment becomes tangled."
    return "No clear trend direction; EMA alignment and HH/HL structure are ambiguous."
