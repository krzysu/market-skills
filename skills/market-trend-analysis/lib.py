"""market-trend-analysis — composite trend verdict from L1 skills."""

from analysis.skill_loader import load_skill


def analyze(candles, interval="1d", period="1y"):
    if not candles or len(candles) < 60:
        cc = len(candles) if candles else 0
        return {
            "pattern": {
                "present": False,
                "confidence": 1,
                "max_confidence": 5,
                "classification": None,
                "type": "TREND_ANALYSIS",
            },
            "signals": {},
            "input_scores": {},
            "narrative": f"insufficient data (need 60+ candles, got {cc})",
        }

    trend_mod = load_skill("market-trend")
    rsi_mod = load_skill("market-rsi")
    sqz_mod = load_skill("market-squeeze")
    vol_mod = load_skill("market-volume")

    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else {}
    rsi_result = rsi_mod.analyze(candles, interval=interval, period=period) if rsi_mod else {}
    sqz_result = sqz_mod.analyze(candles, interval=interval, period=period) if sqz_mod else {}
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else {}

    trend_score = trend_result.get("score") or 0
    rsi_score = rsi_result.get("score") or 0

    sqz_signal = sqz_result.get("signal")
    sqz_map = {"BULLISH": 2, "BULLISH FADING": 1, "BEARISH": -2, "BEARISH FADING": -1}
    sqz_val = sqz_map.get(sqz_signal, 0)

    obv_trend = vol_result.get("obv_trend")
    vol_val = 1 if obv_trend == "rising" else (-1 if obv_trend == "falling" else 0)

    raw = trend_score * 35 + rsi_score * 25 + sqz_val * 25 + vol_val * 15
    max_raw = 255
    min_raw = -255
    unified = ((raw - min_raw) / (max_raw - min_raw)) * 100

    if unified >= 75:
        classification = "BULLISH_HIGH"
    elif unified >= 55:
        classification = "BULLISH_MEDIUM"
    elif unified >= 35:
        classification = "BULLISH_LOW"
    elif unified > 25:
        classification = "NEUTRAL"
    elif unified > 15:
        classification = "BEARISH_LOW"
    elif unified > 5:
        classification = "BEARISH_MEDIUM"
    else:
        classification = "BEARISH_HIGH"

    present = classification in ("BULLISH_HIGH", "BULLISH_MEDIUM", "BEARISH_HIGH", "BEARISH_MEDIUM")
    confidence = max(1, min(5, round(abs(unified - 50) / 50 * 5)))

    signals = {
        "trend_momentum": {"present": trend_score != 0, "weight": 0.35},
        "rsi_extreme": {"present": rsi_score != 0, "weight": 0.25},
        "squeeze_signal": {"present": sqz_val != 0, "weight": 0.25},
        "volume_confirmation": {"present": vol_val != 0, "weight": 0.15},
    }

    parts = []
    trend_signal = trend_result.get("signal")
    if trend_signal:
        parts.append(f"trend {trend_signal.lower()}")
    rsi_val = rsi_result.get("rsi_14")
    if rsi_val is not None:
        parts.append(f"RSI {rsi_val:.0f}")
    if sqz_signal and sqz_signal not in ("FLAT", "UNKNOWN"):
        parts.append(f"squeeze {sqz_signal.lower()}")
    if obv_trend:
        parts.append(f"OBV {obv_trend}")
    narrative = "; ".join(parts) if parts else "mixed signals"

    return {
        "pattern": {
            "present": present,
            "confidence": confidence,
            "max_confidence": 5,
            "classification": classification,
            "type": "TREND_ANALYSIS",
        },
        "signals": signals,
        "input_scores": {
            k: v
            for k, v in {
                "market-trend": trend_result,
                "market-rsi": rsi_result,
                "market-squeeze": sqz_result,
                "market-volume": vol_result,
            }.items()
            if "error" not in v
        },
        "narrative": narrative,
    }
