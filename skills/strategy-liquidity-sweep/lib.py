"""strategy-liquidity-sweep — L3 strategy: sweep + accumulation reversal."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l2_fired,
    l3_tp3_dead_zone_floor,
    validate_l3_tp_ladder_silent,
)
from analysis.conviction_thresholds import lookup_min_conviction
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill

_STRATEGY_NAME = "strategy-liquidity-sweep"


def conviction_from_confidences(sweep_conf: int, accum_conf: int, *, mode: str = "current") -> int:
    """Compute liq-sweep conviction from the two L2 confidences under ``mode``.

    Pure, pluggable so the calibration grid search (bead market-skills-7eq) can
    score alternative formulas without editing this module's hot path. ``mode``
    variants (``"current"`` is capped at 5 and matches the shipped inline
    formula exactly; the others are capped at 5):

      * ``"current"``      — ``min(5, sweep + accum // 2)`` (shipped default).
      * ``"add"``          — ``min(5, sweep + accum)``.
      * ``"add_minus_one"``— ``min(5, sweep + accum - 1)``.
      * ``"max_plus_one"`` — ``min(5, max(sweep, accum) + 1)``.

    The grid search reports conviction distributions per mode; the actual
    constant change is deferred until validated against a strategy-filtered
    journal. A previous attempt to flip the default to ``max_plus_one`` based
    on aggregated (cross-strategy) journal data was reverted in this commit;
    see 7eq notes for the corrected blocker.
    """
    if mode == "current":
        raw = sweep_conf + accum_conf // 2
    elif mode == "add":
        raw = sweep_conf + accum_conf
    elif mode == "add_minus_one":
        raw = sweep_conf + accum_conf - 1
    elif mode == "max_plus_one":
        raw = max(sweep_conf, accum_conf) + 1
    else:
        raise ValueError(f"unknown conviction mode: {mode!r}")
    return min(5, raw)


# Entry-gate tightening (bead market-skills-96y, market-skills-oin): the L3
# used to emit every sweep-classified idea regardless of conviction. Bead
# ``oin`` moved the per-strategy ``MIN_CONVICTION_TO_EMIT`` integer default
# into ``analysis.conviction_thresholds`` so per-(ticker, interval) overrides
# can be shipped in one place without editing this file. Default 1 (no
# filter) is preserved as ``GLOBAL_MIN_CONVICTION_TO_EMIT`` in the table; the
# formula's natural floor for ``current`` mode on integer L2 confidences is
# 1, so this is the legacy no-op. Raising the floor to ``>= 2`` drops
# low-conviction ideas. Per-band backtest evidence (see ``oin`` description
# and ``96y`` notes) suggests some perp_dex / ai_infra tokens benefit from
# tightening while others are harmed, so overrides are ticker-aware via the
# central table.


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None, conviction_mode: str | None = None):
    """Run the liq-sweep L3 emit pipeline.

    Optional ``conviction_mode`` forwards to
    :func:`conviction_from_confidences` (one of ``"current"``,
    ``"add"``, ``"add_minus_one"``, ``"max_plus_one"``). When ``None`` (the
    default) ``"current"`` is used — the formula's own default. The kwarg
    is the lever for backtest-engine formula A/B comparison (bead
    market-skills-7eq): run ``analyze`` with each mode in turn and
    compare Sharpe through ``FillSimulator``.
    """
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
        conviction = conviction_from_confidences(
            sweep_pattern.get("confidence", 3),
            accum_pattern.get("confidence", 3),
            mode=conviction_mode if conviction_mode is not None else "current",
        )
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

    # Tighten entry gate (bead market-skills-96y, market-skills-oin):
    # drop low-conviction noise. Threshold comes from the per-(ticker,
    # interval) table in `analysis.conviction_thresholds`; ``1`` is the
    # no-op floor and any ``>= 2`` value drops low-conviction ideas.
    _min_conv = lookup_min_conviction(_STRATEGY_NAME, ticker, interval)
    if ideas and _min_conv > 1:
        ideas = [i for i in ideas if i.get("conviction", 0) >= _min_conv]

    if ideas:
        narrative = f"Liquidity sweep setup: long. {sweep_result.get('narrative', '')}"
    elif tp_rejection is not None:
        narrative = tp_rejection
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = "No liquidity sweep setup — sweep, accumulation, or volume confirmation missing."

    return {"ideas": ideas, "narrative": narrative}
