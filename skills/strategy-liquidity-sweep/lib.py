"""strategy-liquidity-sweep — L3 strategy: sweep + accumulation reversal."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l2_fired,
    l3_tp3_dead_zone_floor,
    validate_l3_tp_ladder_silent,
)
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    sweep_mod = load_skill("market-liquidity-sweep")
    accum_mod = load_skill("market-accumulation")
    vol_mod = load_skill("market-volume")

    err = {"error": "unavailable", "pattern": {"present": False}}
    sweep_result = sweep_mod.analyze(candles, interval=interval, period=period) if sweep_mod else err
    accum_result = accum_mod.analyze(candles, interval=interval, period=period) if accum_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err

    sweep_pattern = sweep_result.get("pattern", {})
    accum_pattern = accum_result.get("pattern", {})

    # l2_fired returns True only if both pattern.present and classification are
    # set; single source of truth for "did the L2 actually produce a verdict?".
    sweep_present = l2_fired(sweep_result)
    accum_present = l2_fired(accum_result)

    vol_ratio = vol_result.get("volume_ratio") if "error" not in vol_result else None
    obv_trend = vol_result.get("obv_trend") if "error" not in vol_result else None

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    volume_confirms = vol_ratio is not None and vol_ratio > 1.0 and obv_trend == "rising"

    if sweep_present and accum_present and volume_confirms:
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        conviction = min(5, sweep_pattern.get("confidence", 3) + accum_pattern.get("confidence", 3) // 2)
        # BUGS-2026-07-08-3: clamp TP3 at the 5% boundary so low-vol sweeps
        # still produce an idea.
        tp3 = max(entry + risk * 4, l3_tp3_dead_zone_floor(entry))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.3), round_price(entry + atr * 0.3)],
                "stop_loss": round_price(stop),
                # 3-TP ladder: 2R → 3R → 4R from entry (clamped at 5%).
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
                "reasoning": "Liquidity sweep with accumulation and volume confirmation — reversal setup.",
                "source_skills": ["market-liquidity-sweep", "market-accumulation", "market-volume"],
            }
        )
    elif sweep_present and not accum_present and volume_confirms:
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        # BUGS-2026-07-08-3: clamp TP3 at the 5% boundary.
        tp3 = max(entry + risk * 4, l3_tp3_dead_zone_floor(entry))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": 2,
                "version": conviction_version(2),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.3), round_price(entry + atr * 0.3)],
                "stop_loss": round_price(stop),
                # 3-TP ladder: 2R → 3R → 4R from entry (clamped at 5%).
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
                "reasoning": "Liquidity sweep with volume confirmation (no accumulation) — speculative reversal.",
                "source_skills": ["market-liquidity-sweep", "market-volume"],
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

    if ideas:
        narrative = f"Liquidity sweep setup: long. {sweep_result.get('narrative', '')}"
    elif tp_rejection is not None:
        narrative = tp_rejection
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No liquidity sweep setup — sweep, accumulation, or volume confirmation missing."

    return {"ideas": ideas, "narrative": narrative}
