"""market-exhaustion — L2 pattern detection: composes L1 indicators to detect exhaustion."""

import functools
import importlib.util
import os


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
    vol_mod = _load_l1_skill("market-volume")
    volty_mod = _load_l1_skill("market-volatility")
    macd_mod = _load_l1_skill("market-macd")
    rsi_mod = _load_l1_skill("market-rsi")

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

    # 1. Volume climax (weight 0.30)
    vol_present = False
    if "error" not in vol_result:
        vr = vol_result.get("volume_ratio")
        regime = vol_result.get("regime")
        vol_present = (vr is not None and vr >= 2.5) or regime == "CLIMAX"
    signals["volume_climax"] = {"present": vol_present, "weight": 0.30}
    weighted_sum += 0.30 if vol_present else 0.0
    total_weight += 0.30

    # 2. RSI extreme (weight 0.25) — computed directly
    rsi_extreme = rsi is not None and (rsi < 30 or rsi > 70)
    signals["rsi_extreme"] = {"present": rsi_extreme, "weight": 0.25}
    weighted_sum += 0.25 if rsi_extreme else 0.0
    total_weight += 0.25

    # 3. Narrowing range (weight 0.20)
    narrow_present = False
    if "error" not in volty_result:
        narrow_present = volty_result.get("regime") == "LOW"
    signals["narrowing_range"] = {"present": narrow_present, "weight": 0.20}
    weighted_sum += 0.20 if narrow_present else 0.0
    total_weight += 0.20

    # 4. Momentum divergence (weight 0.15)
    div_present = False
    if "error" not in macd_result:
        hf = macd_result.get("histogram_flip")
        div_present = hf in ("negative_to_positive", "positive_to_negative")
    signals["momentum_divergence"] = {"present": div_present, "weight": 0.15}
    weighted_sum += 0.15 if div_present else 0.0
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
