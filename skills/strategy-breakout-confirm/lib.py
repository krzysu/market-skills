"""strategy-breakout-confirm — L3 strategy: confirmed breakouts with volume + squeeze."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l2_classification,
    validate_l3_tp_ladder,
)
from analysis.conviction_thresholds import lookup_min_conviction
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill

_STRATEGY_NAME = "strategy-breakout-confirm"


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    bo_mod = load_skill("market-breakout")
    sqz_mod = load_skill("market-squeeze")
    vol_mod = load_skill("market-volume")

    err = {"error": "unavailable", "pattern": {"present": False}}
    bo_result = bo_mod.analyze(candles, interval=interval, period=period) if bo_mod else err
    sqz_result = sqz_mod.analyze(candles, interval=interval, period=period) if sqz_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err

    bo_pattern = bo_result.get("pattern", {})
    # l2_classification returns None if pattern didn't actually fire (invariant:
    # present=True AND classification is not None).
    bo_classification = l2_classification(bo_result)
    # Direction (`bull`/`bear`/`None`) was added so consumers don't have to
    # substring-match against `classification` (which is a status, not a direction).
    bo_direction = bo_pattern.get("direction")

    sqz_signal = sqz_result.get("signal") if "error" not in sqz_result else None

    vol_ratio = vol_result.get("volume_ratio") if "error" not in vol_result else None
    obv_trend = vol_result.get("obv_trend") if "error" not in vol_result else None

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    volume_ok = vol_ratio is not None and vol_ratio > 1.2
    squeeze_long = sqz_signal in ("BULLISH", "BULLISH FADING")
    squeeze_short = sqz_signal in ("BEARISH", "BEARISH FADING")
    obv_rising = obv_trend == "rising"
    obv_falling = obv_trend == "falling"

    if bo_direction == "bull" and volume_ok and (squeeze_long or obv_rising):
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        conviction = min(5, bo_pattern.get("confidence", 3) + (1 if squeeze_long else 0))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "market",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry), round_price(entry + atr * 0.3)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry + risk * 1.5),
                    round_price(entry + risk * 2.5),
                    round_price(entry + risk * 4),
                ],
                "take_profit_ideal": [
                    entry + risk * 1.5,
                    entry + risk * 2.5,
                    entry + risk * 4,
                ],
                "reasoning": f"Bullish breakout confirmed: {bo_classification}, volume {vol_ratio:.1f}x.",
                "source_skills": ["market-breakout", "market-volume", "market-squeeze"],
            }
        )

    if bo_direction == "bear" and volume_ok and (squeeze_short or obv_falling):
        entry = price
        stop = entry + atr * 1.5
        risk = stop - entry
        conviction = min(5, bo_pattern.get("confidence", 3) + (1 if squeeze_short else 0))
        ideas.append(
            {
                "pair": ticker,
                "direction": "short",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "market",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.3), round_price(entry)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry - risk * 1.5),
                    round_price(entry - risk * 2.5),
                    round_price(entry - risk * 4),
                ],
                "take_profit_ideal": [
                    entry - risk * 1.5,
                    entry - risk * 2.5,
                    entry - risk * 4,
                ],
                "reasoning": f"Bearish breakdown confirmed: {bo_classification}, volume {vol_ratio:.1f}x.",
                "source_skills": ["market-breakout", "market-volume", "market-squeeze"],
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

    # Tighten entry gate (bead market-skills-6th, market-skills-oin):
    # drop low-conviction noise. Threshold comes from the per-(ticker,
    # interval) table in `analysis.conviction_thresholds`; ``1`` is the
    # no-op floor and any ``>= 2`` value drops low-conviction ideas.
    _min_conv = lookup_min_conviction(_STRATEGY_NAME, ticker, interval)
    if ideas and _min_conv > 1:
        ideas = [i for i in ideas if i.get("conviction", 0) >= _min_conv]

    if ideas:
        narrative = f"Breakout momentum setup: {', '.join(i['direction'] for i in ideas)}."
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No confirmed breakout — volume or squeeze confirmation missing."

    return {"ideas": ideas, "narrative": narrative}
