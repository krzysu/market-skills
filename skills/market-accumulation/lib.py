"""market-accumulation — L2 pattern detection: composes L1 indicators to detect smart money accumulation."""

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
    """Compose L1 indicators to detect accumulation pattern.

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
                "type": "ACCUMULATION",
            },
            "signals": {},
            "input_scores": {},
            "narrative": f"insufficient data (need 35+ candles, got {cc})",
        }

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]

    # Load L1 modules
    sr_mod = _load_l1_skill("market-s-r")
    vol_mod = _load_l1_skill("market-volume")
    volty_mod = _load_l1_skill("market-volatility")
    trend_mod = _load_l1_skill("market-trend")

    # Run L1 analyzers
    err = {"error": "unavailable"}
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err
    volty_result = volty_mod.analyze(candles, interval=interval, period=period) if volty_mod else err
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err

    # --- Evaluate sub-signals ---
    signals = {}
    total_weight = 0.0
    weighted_sum = 0.0

    # 1. Spring/shakeout (weight 0.30)
    spring_present = False
    if "error" not in sr_result and "error" not in vol_result:
        nearest_support = sr_result.get("nearest_support")
        sits_on_level = sr_result.get("sits_on_level", False)
        if nearest_support is not None and sits_on_level:
            recent_lows = lows[-5:]
            low_below_support = any(low < nearest_support for low in recent_lows)
            reclaimed = current_price > nearest_support
            spring_present = low_below_support and reclaimed
    signals["spring_shakeout"] = {"present": spring_present, "weight": 0.30}
    weighted_sum += 0.30 if spring_present else 0.0
    total_weight += 0.30

    # 2. Absorption (weight 0.20)
    absorption_present = False
    if "error" not in vol_result and "error" not in volty_result:
        vol_ratio = vol_result.get("volume_ratio")
        vol_regime = volty_result.get("regime")
        absorption_present = vol_ratio is not None and vol_ratio > 1.5 and vol_regime == "LOW"
    signals["absorption"] = {"present": absorption_present, "weight": 0.20}
    weighted_sum += 0.20 if absorption_present else 0.0
    total_weight += 0.20

    # 3. Sign of strength (weight 0.20)
    sos_present = False
    if "error" not in vol_result and "error" not in trend_result:
        vol_ratio = vol_result.get("volume_ratio")
        trend_score = trend_result.get("score")
        sos_present = vol_ratio is not None and vol_ratio > 1.5 and trend_score is not None and trend_score > 0
    signals["sign_of_strength"] = {"present": sos_present, "weight": 0.20}
    weighted_sum += 0.20 if sos_present else 0.0
    total_weight += 0.20

    # 4. Reaccumulation (weight 0.15)
    reaccum_present = False
    if "error" not in trend_result:
        alignment = trend_result.get("alignment")
        above_emas = trend_result.get("price_above_emas")
        reaccum_present = alignment == "PARTIAL_BULL" and above_emas is not None and above_emas >= 2
    signals["reaccumulation"] = {"present": reaccum_present, "weight": 0.15}
    weighted_sum += 0.15 if reaccum_present else 0.0
    total_weight += 0.15

    # 5. Low volatility after distribution (weight 0.15)
    low_vol_present = False
    if "error" not in volty_result:
        regime = volty_result.get("regime")
        vol_trend = volty_result.get("trend")
        low_vol_present = regime == "LOW" and vol_trend == "compressing"
    signals["low_vol_after_distribution"] = {"present": low_vol_present, "weight": 0.15}
    weighted_sum += 0.15 if low_vol_present else 0.0
    total_weight += 0.15

    # --- Compute pattern ---
    if total_weight > 0:
        ratio = weighted_sum / total_weight
        present = ratio >= 0.5
        confidence = max(1, min(5, round(ratio * 5)))
    else:
        ratio = 0.0
        present = False
        confidence = 1

    # --- Classification ---
    classification = None
    if present:
        if spring_present and absorption_present:
            classification = "SPRING"
        elif reaccum_present and sos_present:
            classification = "REACCUMULATION"
        elif low_vol_present:
            classification = "UTAD"
        else:
            classification = "SPRING"

    # --- Build input_scores ---
    input_scores = {}
    if "error" not in sr_result:
        input_scores["market-s-r"] = sr_result
    if "error" not in vol_result:
        input_scores["market-volume"] = vol_result
    if "error" not in volty_result:
        input_scores["market-volatility"] = volty_result
    if "error" not in trend_result:
        input_scores["market-trend"] = trend_result

    # --- Narrative ---
    narrative = _build_narrative(
        classification,
        present,
        spring_present,
        absorption_present,
        sos_present,
        reaccum_present,
        low_vol_present,
    )

    return {
        "pattern": {
            "present": present,
            "confidence": confidence,
            "max_confidence": 5,
            "classification": classification,
            "type": "ACCUMULATION",
        },
        "signals": signals,
        "input_scores": input_scores,
        "narrative": narrative,
    }


def _build_narrative(
    classification,
    present,
    spring_present,
    absorption_present,
    sos_present,
    reaccum_present,
    low_vol_present,
):
    if not present:
        return "No accumulation pattern detected; smart money is not actively positioning."
    if classification == "SPRING":
        return (
            "Spring pattern detected: price dipped below support and reclaimed"
            " with absorption, suggesting smart money accumulation."
        )
    if classification == "REACCUMULATION":
        return (
            "Reaccumulation detected: pullback within uptrend with signs of"
            " strength, suggesting institutional buying after initial markup."
        )
    if classification == "UTAD":
        return (
            "Upthrust after distribution detected: low volatility following"
            " prior distribution activity, suggesting a potential top."
        )
    return "Accumulation pattern detected with mixed signals."
