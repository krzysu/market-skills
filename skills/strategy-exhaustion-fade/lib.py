"""strategy-exhaustion-fade — L3 strategy: fade exhaustion at extremes."""

import functools
import importlib.util
import os

from analysis.indicators import compute_atr_from_candles


@functools.cache
def _load_l2_skill(name):
    lib_path = os.path.join(os.path.dirname(__file__), "..", name, "lib.py")
    if not os.path.exists(lib_path):
        return None
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def analyze(candles, interval="1d", period="1y"):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    exh_mod = _load_l2_skill("market-exhaustion")
    sr_mod = _load_l2_skill("market-s-r")
    trend_mod = _load_l2_skill("market-trend")

    err = {"error": "unavailable", "pattern": {"present": False}}
    exh_result = exh_mod.analyze(candles, interval=interval, period=period) if exh_mod else err
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err

    exh_pattern = exh_result.get("pattern", {})
    exh_present = exh_pattern.get("present", False)
    exh_classification = exh_pattern.get("classification", "")

    resistance = sr_result.get("nearest_resistance") if "error" not in sr_result else None
    support = sr_result.get("nearest_support") if "error" not in sr_result else None

    trend_score = trend_result.get("score") if "error" not in trend_result else 0

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    blowoff = "BLOWOFF" in str(exh_classification).upper()
    capitulation = "CAPITULATION" in str(exh_classification).upper()

    if exh_present and blowoff and resistance is not None and price >= resistance * 0.98 and trend_score > 0:
        entry = price
        stop = price + atr * 1.5
        risk = stop - entry
        conviction = min(5, exh_pattern.get("confidence", 3))
        ideas.append(
            {
                "pair": "...",
                "direction": "short",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry * 0.98, 2), round(entry, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry - risk, 2), round(entry - risk * 2, 2)],
                "reasoning": f"Blowoff exhaustion at resistance ({exh_classification}).",
                "source_skills": ["market-exhaustion", "market-s-r", "market-trend"],
            }
        )

    if exh_present and capitulation and support is not None and price <= support * 1.02 and trend_score < 0:
        entry = price
        stop = price - atr * 1.5
        risk = entry - stop
        conviction = min(5, exh_pattern.get("confidence", 3))
        ideas.append(
            {
                "pair": "...",
                "direction": "long",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry, 2), round(entry * 1.02, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry + risk, 2), round(entry + risk * 2, 2)],
                "reasoning": f"Capitulation exhaustion at support ({exh_classification}).",
                "source_skills": ["market-exhaustion", "market-s-r", "market-trend"],
            }
        )

    if ideas:
        narrative = f"Exhaustion fade setup: {', '.join(i['direction'] for i in ideas)}."
    else:
        narrative = "No exhaustion fade setup — missing exhaustion pattern or S/R alignment."

    return {"ideas": ideas, "narrative": narrative}
