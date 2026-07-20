"""strategy-funding-carry — L3 strategy: harvest perp funding rates for income."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l3_tp3_dead_zone_ceiling,
    l3_tp3_dead_zone_floor,
    validate_l3_tp_ladder_silent,
)
from analysis.conviction_thresholds import lookup_min_conviction
from analysis.data import fetch_funding_rate
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles

_STRATEGY_NAME = "strategy-funding-carry"


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    atr = compute_atr_from_candles(candles, period=14)
    if not atr or atr <= 0:
        return {
            "ideas": [],
            "narrative": "insufficient volatility to place carry stop (ATR is zero)",
        }

    funding = fetch_funding_rate(ticker)
    if not funding:
        return {"ideas": [], "narrative": f"Funding rate unavailable for {ticker}"}

    rate = funding.get("funding_rate")
    if rate is None:
        return {"ideas": [], "narrative": f"Funding rate unavailable for {ticker}"}

    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return {"ideas": [], "narrative": f"Funding rate unavailable for {ticker}"}

    abs_rate = abs(rate)
    if abs_rate >= 0.001:
        conviction = 4
    elif abs_rate >= 0.0005:
        conviction = 3
    elif abs_rate >= 0.0001:
        conviction = 2
    else:
        return {
            "ideas": [],
            "narrative": "No funding carry setup — funding rate not extreme enough to emit an idea.",
        }

    closes = [c[4] for c in candles]
    entry = closes[-1]
    direction = "long" if rate < 0 else "short"

    if direction == "long":
        stop = entry - atr * 2
        risk = entry - stop
        tp_ideal = [
            entry + risk * 1.5,
            entry + risk * 2.5,
            max(entry + risk * 4, l3_tp3_dead_zone_floor(entry)),
        ]
    else:
        stop = entry + atr * 2
        risk = stop - entry
        tp_ideal = [
            entry - risk * 1.5,
            entry - risk * 2.5,
            min(entry - risk * 4, l3_tp3_dead_zone_ceiling(entry)),
        ]

    if rate < 0:
        reasoning = (
            f"Negative perp funding ({rate * 100:.4f}% per 8h) — shorts pay longs. "
            f"Carry trade: enter long to harvest funding, exit when funding normalizes."
        )
    else:
        reasoning = (
            f"Positive perp funding ({rate * 100:.4f}% per 8h) — longs pay shorts. "
            f"Carry trade: enter short to harvest funding, exit when funding normalizes."
        )

    idea = {
        "pair": ticker,
        "direction": direction,
        "conviction": conviction,
        "version": conviction_version(conviction),
        "entry_type": "limit",
        "entry_price": round_price(entry),
        "entry_range": [round_price(entry - atr * 0.5), round_price(entry + atr * 0.5)],
        "stop_loss": round_price(stop),
        "take_profit": [round_price(tp) for tp in tp_ideal],
        "take_profit_ideal": tp_ideal,
        "reasoning": reasoning,
        "source_skills": ["market-basis"],
    }

    ideas = [idea]

    tp_rejection = None
    if ideas:
        validated = []
        for i in ideas:
            i["rr_to_tp"] = compute_rr_to_tp(i)
            err = validate_l3_tp_ladder_silent(i)
            if err is None:
                validated.append(i)
            elif tp_rejection is None:
                tp_rejection = err
        ideas = validated

    stop_2pct_rejection = None
    if ideas:
        filtered = []
        for i in ideas:
            ok, rej = enforce_min_stop_distance(i)
            if ok:
                filtered.append(i)
            elif stop_2pct_rejection is None:
                stop_2pct_rejection = rej
        ideas = filtered

    _min_conv = lookup_min_conviction(_STRATEGY_NAME, ticker, interval)
    if ideas and _min_conv > 1:
        ideas = [i for i in ideas if i.get("conviction", 0) >= _min_conv]

    if ideas:
        dirs = ", ".join(i["direction"] for i in ideas)
        narrative = f"Funding carry setup: {dirs}. {reasoning}"
    elif tp_rejection is not None:
        narrative = tp_rejection
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No funding carry setup — funding rate not extreme enough to emit an idea."

    return {"ideas": ideas, "narrative": narrative}
