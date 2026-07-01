"""strategy-exhaustion-fade — L3 strategy: fade exhaustion at extremes."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l2_classification,
    validate_l3_tp_ladder,
)
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    exh_mod = load_skill("market-exhaustion")
    sr_mod = load_skill("market-s-r")
    trend_mod = load_skill("market-trend")

    err = {"error": "unavailable", "pattern": {"present": False}}
    exh_result = exh_mod.analyze(candles, interval=interval, period=period) if exh_mod else err
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err

    exh_pattern = exh_result.get("pattern", {})
    # l2_classification returns None if pattern didn't actually fire (invariant:
    # present=True AND classification is not None).
    exh_classification = l2_classification(exh_result)

    resistance = sr_result.get("nearest_resistance") if "error" not in sr_result else None
    support = sr_result.get("nearest_support") if "error" not in sr_result else None

    trend_score = trend_result.get("score") if "error" not in trend_result else 0

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    blowoff = "BLOWOFF" in str(exh_classification).upper()
    capitulation = "CAPITULATION" in str(exh_classification).upper()

    if exh_classification and blowoff and resistance is not None and price >= resistance * 0.98 and trend_score > 0:
        entry = price
        stop = price + atr * 1.5
        risk = stop - entry
        conviction = min(5, exh_pattern.get("confidence", 3))
        ideas.append(
            {
                "pair": ticker,
                "direction": "short",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry * 0.98), round_price(entry)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry - risk * 1),
                    round_price(entry - risk * 2),
                    round_price(entry - risk * 3),
                ],
                "take_profit_ideal": [
                    entry - risk * 1,
                    entry - risk * 2,
                    entry - risk * 3,
                ],
                "reasoning": f"Blowoff exhaustion at resistance ({exh_classification}).",
                "source_skills": ["market-exhaustion", "market-s-r", "market-trend"],
            }
        )

    if exh_classification and capitulation and support is not None and price <= support * 1.02 and trend_score < 0:
        entry = price
        stop = price - atr * 1.5
        risk = entry - stop
        conviction = min(5, exh_pattern.get("confidence", 3))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry), round_price(entry * 1.02)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry + risk * 1),
                    round_price(entry + risk * 2),
                    round_price(entry + risk * 3),
                ],
                "take_profit_ideal": [
                    entry + risk * 1,
                    entry + risk * 2,
                    entry + risk * 3,
                ],
                "reasoning": f"Capitulation exhaustion at support ({exh_classification}).",
                "source_skills": ["market-exhaustion", "market-s-r", "market-trend"],
            }
        )

    for idea in ideas:
        idea["rr_to_tp"] = compute_rr_to_tp(idea)
        validate_l3_tp_ladder(idea)

    # Drop sub-2% stops (noise risk in swing mode).
    stop_2pct_rejection = None
    if ideas:
        filtered = []
        for idea in ideas:
            ok, rej = enforce_min_stop_distance(idea)
            if ok:
                filtered.append(idea)
            elif stop_2pct_rejection is None:
                stop_2pct_rejection = rej
        ideas = filtered

    if ideas:
        narrative = f"Exhaustion fade setup: {', '.join(i['direction'] for i in ideas)}."
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No exhaustion fade setup — missing exhaustion pattern or S/R alignment."

    return {"ideas": ideas, "narrative": narrative}
