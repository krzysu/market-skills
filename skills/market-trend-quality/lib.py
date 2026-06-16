"""market-trend-quality — L2 pattern detection: assesses trend health by composing L1 indicators."""

import functools
import importlib.util
import os

from analysis.indicators import extract_ohlcv


@functools.cache
def _load_l1_skill(name):
    """Load an L1 skill lib.py dynamically (handles hyphens in path)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", name, "lib.py")
    if not os.path.exists(lib_path):
        return None
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    trend_mod = _load_l1_skill("market-trend")
    fib_mod = _load_l1_skill("market-fibonacci")
    vol_mod = _load_l1_skill("market-volume")
    ema_mod = _load_l1_skill("market-ema")

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

    # 4. Impulse vs retrace ratio (weight 0.15)
    impulse_present = False
    impulse_contribution = 0.0
    if "error" not in trend_result:
        trend_score = trend_result.get("score", 0)
        if abs(trend_score) >= 3:
            impulse_present = True
            if trend_score >= 3:
                impulse_contribution = 0.15
            else:
                impulse_contribution = -0.15
    signals["impulse_vs_retrace"] = {"present": impulse_present, "weight": 0.15}
    signed_score += impulse_contribution

    # 5. Volume confirmation (weight 0.15)
    vol_present = False
    vol_contribution = 0.0
    if "error" not in vol_result:
        vr = vol_result.get("volume_ratio")
        obv_trend = vol_result.get("obv_trend")
        if vr is not None and vr > 1.0:
            if obv_trend == "rising":
                vol_present = True
                vol_contribution = 0.15
            elif obv_trend == "falling":
                vol_present = True
                vol_contribution = -0.15
    signals["volume_confirmation"] = {"present": vol_present, "weight": 0.15}
    signed_score += vol_contribution

    # --- Classification (sole source of truth for present) ---
    trend_score = trend_result.get("score", 0) if "error" not in trend_result else 0
    alignment = trend_result.get("alignment", "TANGLED") if "error" not in trend_result else "TANGLED"

    hh_intact = hh is True and hl is True
    hh_broken = hh is False and hl is False
    hh_breaking = (hh is False) or (hl is False)

    ema_bullish = alignment in ("FULL_BULL", "PARTIAL_BULL")
    ema_bearish = alignment in ("FULL_BEAR", "PARTIAL_BEAR")
    ema_tangled = alignment in ("TANGLED", "UNKNOWN")

    classification = None
    if trend_score >= 3 and hh_intact and ema_bullish:
        classification = "HEALTHY_UPTREND"
    elif trend_score <= -3 and hh_broken and ema_bearish:
        classification = "HEALTHY_DOWNTREND"
    elif 1 <= trend_score <= 2 or -2 <= trend_score <= -1:
        classification = "WEAKENING"
    elif hh_breaking and ema_tangled:
        classification = "DEGRADING"

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
    if classification == "HEALTHY_UPTREND":
        return f"Healthy uptrend with score {trend_score}, EMA alignment {alignment}, and intact HH/HL structure."
    if classification == "HEALTHY_DOWNTREND":
        return f"Healthy downtrend with score {trend_score}, EMA alignment {alignment}, and broken HH/HL structure."
    if classification == "WEAKENING":
        return f"Trend weakening with score {trend_score}, showing conflicting signals between sub-components."
    if classification == "DEGRADING":
        return "Trend degrading as HH/HL structure breaks down and EMA alignment becomes tangled."
    return "No clear trend direction; EMA alignment and HH/HL structure are ambiguous."
