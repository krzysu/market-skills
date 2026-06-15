"""strategy-accumulation-swing — L3 strategy: Wyckoff accumulation in healthy trends."""

import functools
import importlib.util
import os

from lib.indicators import compute_atr_from_candles


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

    accum_mod = _load_l2_skill("market-accumulation")
    tq_mod = _load_l2_skill("market-trend-quality")

    err = {"error": "unavailable", "pattern": {"present": False}}
    accum_result = accum_mod.analyze(candles, interval=interval, period=period) if accum_mod else err
    tq_result = tq_mod.analyze(candles, interval=interval, period=period) if tq_mod else err

    accum_pattern = accum_result.get("pattern", {})
    accum_present = accum_pattern.get("present", False)
    accum_classification = accum_pattern.get("classification")

    tq_pattern = tq_result.get("pattern", {})
    tq_present = tq_pattern.get("present", False)
    tq_classification = tq_pattern.get("classification")

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    valid_accum = accum_present and accum_classification in ("SPRING", "REACCUMULATION", "UTAD")
    valid_trend = tq_present and tq_classification in ("HEALTHY_UPTREND", "WEAKENING")
    improving = tq_classification == "WEAKENING"

    if valid_accum and valid_trend:
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        conviction = min(5, accum_pattern.get("confidence", 3) + (2 if tq_classification == "HEALTHY_UPTREND" else 0))
        ideas.append(
            {
                "pair": "...",
                "direction": "long",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry - atr * 0.5, 2), round(entry + atr * 0.3, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry + risk * 2, 2), round(entry + risk * 3, 2)],
                "reasoning": (
                    f"Accumulation ({accum_classification}) in {'improving' if improving else 'healthy'} trend."
                ),
                "source_skills": ["market-accumulation", "market-trend-quality"],
            }
        )

    if ideas:
        narrative = f"Accumulation swing setup: long. {accum_result.get('narrative', '')}"
    else:
        narrative = "No accumulation swing setup — missing accumulation pattern or healthy trend."

    return {"ideas": ideas, "narrative": narrative}
