"""strategy-trend-follow — L3 strategy: trends with pullback/breakout entries."""

from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill


def analyze(candles, *, ticker, interval="1d", period="1y"):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    tq_mod = load_skill("market-trend-quality")
    bo_mod = load_skill("market-breakout")

    err = {"error": "unavailable", "pattern": {"present": False}}
    tq_result = tq_mod.analyze(candles, interval=interval, period=period) if tq_mod else err
    bo_result = bo_mod.analyze(candles, interval=interval, period=period) if bo_mod else err

    if "error" in tq_result.get("pattern", {}):
        return {"ideas": [], "narrative": tq_result.get("narrative", "trend-quality unavailable")}

    tq_pattern = tq_result["pattern"]
    classification = tq_pattern.get("classification")
    bo_classification = bo_result.get("pattern", {}).get("classification")

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    if classification == "HEALTHY_UPTREND" and tq_pattern["present"]:
        entry = price
        stop = entry - atr * 2
        risk = entry - stop
        boost = 1 if bo_classification and "BREAKOUT" in str(bo_classification) else 0
        conviction = min(5, tq_pattern["confidence"] + boost)
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry - atr * 0.5, 2), round(entry + atr * 0.5, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry + risk * 1.5, 2), round(entry + risk * 2.5, 2), round(entry + risk * 4, 2)],
                "reasoning": f"Healthy uptrend ({classification}), pullback or breakout entry.",
                "source_skills": ["market-trend-quality", "market-breakout"],
            }
        )

    if classification == "HEALTHY_DOWNTREND" and tq_pattern["present"]:
        entry = price
        stop = entry + atr * 2
        risk = stop - entry
        boost = 1 if bo_classification and "BREAKOUT" in str(bo_classification) else 0
        conviction = min(5, tq_pattern["confidence"] + boost)
        ideas.append(
            {
                "pair": ticker,
                "direction": "short",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry - atr * 0.5, 2), round(entry + atr * 0.5, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry - risk * 1.5, 2), round(entry - risk * 2.5, 2), round(entry - risk * 4, 2)],
                "reasoning": f"Healthy downtrend ({classification}), breakdown entry.",
                "source_skills": ["market-trend-quality", "market-breakout"],
            }
        )

    if ideas:
        dirs = ", ".join(i["direction"] for i in ideas)
        narrative = f"Trend-follow setup: {dirs}. {tq_result.get('narrative', '')}"
    else:
        narrative = tq_result.get("narrative", "No trend-follow setup detected.")

    return {"ideas": ideas, "narrative": narrative}
