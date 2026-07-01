"""market-exhaustion — L2 pattern detection: composes L1 indicators to detect exhaustion."""

from analysis.skill_loader import load_skill


def analyze(candles, interval="1d", period="1y"):
    """Compose L1 indicators to detect exhaustion pattern.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with pattern, signals, input_scores, narrative
    """
    if not candles or len(candles) < 35:
        cc = len(candles) if candles else 0
        return {
            "pattern": {
                "present": False,
                "confidence": 1,
                "max_confidence": 5,
                "classification": None,
                "type": "EXHAUSTION",
            },
            "signals": {},
            "input_scores": {},
            "narrative": f"insufficient data (need 35+ candles, got {cc})",
        }

    # Load L1 modules
    vol_mod = load_skill("market-volume")
    volty_mod = load_skill("market-volatility")
    macd_mod = load_skill("market-macd")
    rsi_mod = load_skill("market-rsi")

    # Run L1 analyzers
    err = {"error": "unavailable"}
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err
    volty_result = volty_mod.analyze(candles, interval=interval, period=period) if volty_mod else err
    macd_result = macd_mod.analyze(candles, interval=interval, period=period) if macd_mod else err
    rsi_result = rsi_mod.analyze(candles, interval=interval, period=period) if rsi_mod else err

    rsi = rsi_result.get("rsi_14") if "error" not in rsi_result else None

    # --- Evaluate sub-signals ---
    signals = {}
    total_weight = 0.0
    weighted_sum = 0.0

    # Weights sum to 1.0; confidence is the absolute |weighted_sum| on this scale.
    # Original ordering preserved: volume_climax > rsi_extreme > narrowing_range > momentum_divergence.

    # 1. Volume climax (weight 0.3333)
    vol_present = False
    if "error" not in vol_result:
        vr = vol_result.get("volume_ratio")
        regime = vol_result.get("regime")
        vol_present = (vr is not None and vr >= 2.5) or regime == "CLIMAX"
    signals["volume_climax"] = {"present": vol_present, "weight": 0.3333}
    weighted_sum += 0.3333 if vol_present else 0.0
    total_weight += 0.3333

    # 2. RSI extreme (weight 0.2778)
    rsi_extreme = rsi is not None and (rsi < 30 or rsi > 70)
    signals["rsi_extreme"] = {"present": rsi_extreme, "weight": 0.2778}
    weighted_sum += 0.2778 if rsi_extreme else 0.0
    total_weight += 0.2778

    # 3. Narrowing range (weight 0.2222)
    narrow_present = False
    if "error" not in volty_result:
        narrow_present = volty_result.get("regime") == "LOW"
    signals["narrowing_range"] = {"present": narrow_present, "weight": 0.2222}
    weighted_sum += 0.2222 if narrow_present else 0.0
    total_weight += 0.2222

    # 4. Momentum divergence (weight 0.1667)
    div_present = False
    if "error" not in macd_result:
        hf = macd_result.get("histogram_flip")
        div_present = hf in ("negative_to_positive", "positive_to_negative")
    signals["momentum_divergence"] = {"present": div_present, "weight": 0.1667}
    weighted_sum += 0.1667 if div_present else 0.0
    total_weight += 0.1667

    # --- Compute pattern ---
    # Trigger mirrors the bug-scan Shape #1 (absent-with-subs): require at
    # least 2 sub-signals present AND combined weight > 0.30. The pre-fix
    # ``ratio >= 0.5`` threshold silently dropped 2-sub cases at wsum in
    # (0.30, 0.50) — e.g. rsi_extreme + momentum_divergence (0.4445) and
    # narrowing_range + momentum_divergence (0.3889). Same pattern as ALGO 4h
    # (BUG-2026-06-24-01) and VVV 4h trend-quality (BUG-2026-06-24-02).
    n_present = sum(1 for sig in signals.values() if sig.get("present"))
    if total_weight > 0:
        confidence = max(1, min(5, round(weighted_sum * 5)))
        present = n_present >= 2 and weighted_sum > 0.30
    else:
        present = False
        confidence = 1

    # --- Classification ---
    classification = None
    if present:
        if rsi is not None and rsi < 30 and vol_present:
            classification = "CAPITULATION_BOTTOM"
        elif rsi is not None and rsi > 70 and vol_present:
            classification = "BLOWOFF_TOP"
        elif div_present:
            classification = "IMPULSE_EXHAUSTION"
        else:
            classification = "PULLBACK_EXHAUSTED"

    # --- Build input_scores ---
    input_scores = {}
    if "error" not in vol_result:
        input_scores["market-volume"] = vol_result
    if "error" not in volty_result:
        input_scores["market-volatility"] = volty_result
    if "error" not in macd_result:
        input_scores["market-macd"] = macd_result
    # --- Narrative ---
    narrative = _build_narrative(classification, present, rsi, vol_present, div_present)

    return {
        "pattern": {
            "present": present,
            "confidence": confidence,
            "max_confidence": 5,
            "classification": classification,
            "type": "EXHAUSTION",
        },
        "signals": signals,
        "input_scores": input_scores,
        "narrative": narrative,
    }


def _build_narrative(classification, present, rsi, vol_present, div_present):
    if not present:
        return "No exhaustion pattern detected; price move remains intact."
    if classification == "CAPITULATION_BOTTOM":
        return "Capitulation bottom detected: extreme volume climax with RSI oversold."
    if classification == "BLOWOFF_TOP":
        return "Blowoff top detected: extreme volume climax with RSI overbought."
    if classification == "IMPULSE_EXHAUSTION":
        parts = ["Impulse exhaustion detected: momentum divergence with histogram flip"]
        if vol_present:
            parts.append("confirmed by volume climax")
        return parts[0] + (" " + parts[1] if len(parts) > 1 else "") + "."
    if classification == "PULLBACK_EXHAUSTED":
        return "Pullback exhaustion detected: multiple indicators suggest the corrective move is losing steam."
    return "Exhaustion pattern detected with mixed signals."
