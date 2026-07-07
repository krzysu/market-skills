"""strategy-mean-reversion — L3 strategy: fade extremes at S/R levels."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    validate_l3_tp_ladder,
)
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill


def _apply_cape_valuation_tag(idea: dict, valuation: dict | None) -> None:
    """Attach a soft ``veto_reasons`` tag when SP500 CAPE valuation disagrees with the trade.

    Following ADR-0002: the strategy never hard-vetoes. The tag is
    informational — the LLM agent brain reads the z-score and decides.
    Conviction is *not* auto-downgraded here; that's the LLM's call.

    Rules:
      - long  + regime=OVEREXTENDED (z >= 2.0) → "sp500_cape_overextended_z{X.XX}"
      - short + regime=OVERSOLD     (z <= -2.0) → "sp500_cape_oversold_z{X.XX}"
      - any other regime (FAIR / ELEVATED / DEPRESSED / UNKNOWN) → no tag
    """
    if not valuation or not isinstance(valuation, dict):
        return
    regime = (valuation.get("regime") or {}).get("regime")
    zscore = (valuation.get("regime") or {}).get("cape_zscore")
    if regime is None or zscore is None:
        return
    direction = idea.get("direction")
    if direction == "long" and regime == "OVEREXTENDED":
        tag = f"sp500_cape_overextended_z{zscore:.2f}"
    elif direction == "short" and regime == "OVERSOLD":
        tag = f"sp500_cape_oversold_z{zscore:.2f}"
    else:
        return
    idea.setdefault("veto_reasons", []).append(tag)
    existing = idea.get("reasoning", "")
    if tag not in existing:
        idea["reasoning"] = f"{existing} [valuation: {regime.lower()} z={zscore:+.2f}]"


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    rsi_mod = load_skill("market-rsi")
    sr_mod = load_skill("market-s-r")
    volty_mod = load_skill("market-volatility")
    val_mod = load_skill("market-valuation")

    err = {"error": "unavailable"}
    rsi_result = rsi_mod.analyze(candles, interval=interval, period=period) if rsi_mod else err
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    volty_result = volty_mod.analyze(candles, interval=interval, period=period) if volty_mod else err
    valuation = val_mod.analyze() if val_mod else None

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    rsi_oversold = False
    rsi_overbought = False
    if "error" not in rsi_result:
        rsi = rsi_result.get("rsi_14")
        if rsi is not None:
            rsi_oversold = rsi <= 30
            rsi_overbought = rsi >= 70

    low_vol = "error" not in volty_result and volty_result.get("regime") == "LOW"
    support = sr_result.get("nearest_support") if "error" not in sr_result else None
    resistance = sr_result.get("nearest_resistance") if "error" not in sr_result else None

    if rsi_oversold and support is not None and price <= support * 1.02:
        entry = price
        stop = support - atr * 1
        risk = entry - stop
        # Audit 2026-06-21 #5: TP3 must be ≥ entry × 1.05. If resistance is too close
        # to entry, fall back to a 3R target instead of letting TP3 degenerate to entry.
        far_target = resistance if (resistance is not None and resistance >= entry * 1.05) else entry + risk * 3
        conviction = 3 if low_vol else 2
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(support), round_price(entry * 1.01)],
                "stop_loss": round_price(stop),
                # 3-TP ladder (ascending): 1R → 2R → full reversion to resistance.
                "take_profit": [
                    round_price(entry + risk * 1),
                    round_price(entry + risk * 2),
                    round_price(far_target),
                ],
                "take_profit_ideal": [
                    entry + risk * 1,
                    entry + risk * 2,
                    far_target,
                ],
                "reasoning": "Oversold at support with mean-reversion setup.",
                "source_skills": ["market-rsi", "market-s-r", "market-volatility"],
            }
        )

    if rsi_overbought and resistance is not None and price >= resistance * 0.98:
        entry = price
        stop = resistance + atr * 1
        risk = stop - entry
        # Audit 2026-06-21 #5: TP3 must be ≤ entry × 0.95. If support is too close,
        # fall back to 3R target.
        far_target = support if (support is not None and support <= entry * 0.95) else entry - risk * 3
        conviction = 3 if low_vol else 2
        ideas.append(
            {
                "pair": ticker,
                "direction": "short",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry * 0.99), round_price(resistance)],
                "stop_loss": round_price(stop),
                # 3-TP ladder (descending): 1R → 2R → full reversion to support.
                "take_profit": [
                    round_price(entry - risk * 1),
                    round_price(entry - risk * 2),
                    round_price(far_target),
                ],
                "take_profit_ideal": [
                    entry - risk * 1,
                    entry - risk * 2,
                    far_target,
                ],
                "reasoning": "Overbought at resistance with mean-reversion setup.",
                "source_skills": ["market-rsi", "market-s-r", "market-volatility"],
            }
        )

    for idea in ideas:
        _apply_cape_valuation_tag(idea, valuation)
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
        narrative = f"Mean-reversion setup: {', '.join(i['direction'] for i in ideas)}."
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No mean-reversion setup — RSI not at extreme or price not at key level."

    return {"ideas": ideas, "narrative": narrative}
