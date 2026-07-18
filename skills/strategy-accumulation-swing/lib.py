"""strategy-accumulation-swing — L3 strategy: Wyckoff accumulation in healthy trends."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l2_classification,
    l3_tp3_dead_zone_floor,
    validate_l3_tp_ladder_silent,
)
from analysis.conviction_thresholds import lookup_min_conviction
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill

_STRATEGY_NAME = "strategy-accumulation-swing"


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    accum_mod = load_skill("market-accumulation")
    tq_mod = load_skill("market-trend-quality")

    err = {"error": "unavailable", "pattern": {"present": False}}
    accum_result = accum_mod.analyze(candles, interval=interval, period=period) if accum_mod else err
    tq_result = tq_mod.analyze(candles, interval=interval, period=period) if tq_mod else err

    # l2_classification returns None if pattern didn't actually fire (invariant:
    # present=True AND classification is not None).
    accum_classification = l2_classification(accum_result)
    tq_classification = l2_classification(tq_result)
    accum_pattern = accum_result.get("pattern", {})

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    valid_accum = accum_classification in ("SPRING", "REACCUMULATION")
    valid_trend = tq_classification in ("HEALTHY_UPTREND", "WEAKENING")
    improving = tq_classification == "WEAKENING"

    if valid_accum and valid_trend:
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        conviction = min(5, accum_pattern.get("confidence", 3) + (2 if tq_classification == "HEALTHY_UPTREND" else 0))
        # BUGS-2026-07-08-3: clamp TP3 at the 5% dead-zone boundary so the
        # ladder clears the validator on low-vol assets (PAXGUSD regression).
        tp3 = max(entry + risk * 5, l3_tp3_dead_zone_floor(entry))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.5), round_price(entry + atr * 0.3)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry + risk * 2),
                    round_price(entry + risk * 3),
                    round_price(tp3),
                ],
                "take_profit_ideal": [
                    entry + risk * 2,
                    entry + risk * 3,
                    tp3,
                ],
                "reasoning": (
                    f"Accumulation ({accum_classification}) in {'improving' if improving else 'healthy'} trend."
                ),
                "source_skills": ["market-accumulation", "market-trend-quality"],
            }
        )

    tp_rejection = None
    if ideas:
        validated = []
        for idea in ideas:
            idea["rr_to_tp"] = compute_rr_to_tp(idea)
            err = validate_l3_tp_ladder_silent(idea)
            if err is None:
                validated.append(idea)
            elif tp_rejection is None:
                tp_rejection = err
        ideas = validated

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

    # Tighten entry gate (bead market-skills-6th, market-skills-oin):
    # drop low-conviction noise. Threshold comes from the per-(ticker,
    # interval) table in `analysis.conviction_thresholds`; ``1`` is the
    # no-op floor and any ``>= 2`` value drops low-conviction ideas.
    _min_conv = lookup_min_conviction(_STRATEGY_NAME, ticker, interval)
    if ideas and _min_conv > 1:
        ideas = [i for i in ideas if i.get("conviction", 0) >= _min_conv]

    if ideas:
        narrative = f"Accumulation swing setup: long. {accum_result.get('narrative', '')}"
    elif tp_rejection is not None:
        narrative = tp_rejection
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No accumulation swing setup — missing accumulation pattern or healthy trend."

    return {"ideas": ideas, "narrative": narrative}
