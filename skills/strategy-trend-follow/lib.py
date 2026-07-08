"""strategy-trend-follow — L3 strategy: trends with pullback/breakout entries."""

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    enforce_min_stop_distance,
    l2_classification,
    l3_tp3_dead_zone_ceiling,
    l3_tp3_dead_zone_floor,
    validate_l3_tp_ladder_silent,
)
from analysis.formatting import round_price
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill

# Asset-class multipliers for Pattern S maturity thresholds.
# Each multiplier scales the default 30% (mature-move) / 50% (late-move) floors.
# Default (unlisted or None) = 1.0 → current behaviour for blue-chip majors.
_ASSET_CLASS_MULTIPLIERS: dict[str, float] = {
    "perp_dex": 6.0,  # 30→180%, 50→300%
    "low_float": 6.0,  # same regime as perp_dex
    "ai_infra": 2.0,  # 30→60%, 50→100%
}


def _detect_intact_bullish_structure(tq_result):
    """Check if WEAKENING still has intact bullish macro structure."""
    trend = tq_result.get("input_scores", {}).get("market-trend", {})
    if "error" in trend:
        return False
    hh = trend.get("higher_high")
    hl = trend.get("higher_low")
    alignment = trend.get("alignment")
    return hh is True and hl is True and alignment in ("FULL_BULL", "PARTIAL_BULL")


def _compute_maturity_metrics(closes: list[float], entry: float) -> tuple[float | None, float | None]:
    """Compute move maturity and entry-window validity for the current close.

    Returns ``(move_maturity_pct, entry_window_validity_pct)`` — both
    ``None`` when the lookback is too short to be meaningful.

    ``move_maturity_pct`` = ``(close - rolling_low) / rolling_low * 100``
    over the full candle window. >30 = mature move, >50 = late/chase-risk.
    A swing low reset within the window pulls this number back toward 0;
    the field is observational, not gating.

    ``entry_window_validity_pct`` = ``abs(close - entry) / entry * 100``.
    >3 = current price is meaningfully outside the planned entry band
    (chase risk; the original entry may no longer be available).
    """
    if not closes or entry <= 0:
        return None, None
    rolling_low = min(closes)
    if rolling_low <= 0:
        return None, None
    move_maturity_pct = (closes[-1] - rolling_low) / rolling_low * 100
    entry_window_validity_pct = abs(closes[-1] - entry) / entry * 100
    return round(move_maturity_pct, 4), round(entry_window_validity_pct, 4)


def _apply_pattern_s(idea: dict) -> None:
    """Apply soft-veto tags that downgrade an idea based on chase risk and move maturity.

    Inlines the swing-scan cron's down-or-info rules into the L3 emit path so
    consumers running ``strategy-trend-follow`` standalone see the same
    protective downgrades. Tag the idea with ``veto_reasons`` and adjust
    conviction accordingly.

    S1. Entry-window check:
      - chase-risk:  current price > entry + 2% (long) or < entry - 2% (short) → -1 conv
      - entry-edge:  current price > entry + 0.5% < 2%                 → info only
      - pullback-not-yet: long: close < entry - 5%; short: close > entry + 5% → -1 conv

    S2. Move-maturity check (thresholds scaled by asset_class multiplier):
      - mature-move:  move_maturity_pct > 30 × mult               → -1 conv
      - late-move:    move_maturity_pct > 50 × mult               → -2 conv
      - asset-class-scaled: info tag when multiplier > 1.0 (zero conviction delta)

    Default multiplier is 1.0 (blue-chip majors). Perp-DEX/low-float = 6×.
    Ai_infra = 2×. See ``_ASSET_CLASS_MULTIPLIERS`` for the full map.

    Conviction is clamped to [1, 5]. Ideas with conviction still >= 1 are kept
    (downgrade-not-veto); ideas that would drop to 0 are kept at 1 so the L3
    output isn't silently empty.
    """
    entry = idea.get("entry_price")
    direction = idea.get("direction")
    move_maturity = idea.get("move_maturity_pct")
    entry_window = idea.get("entry_window_validity_pct")
    closes_val = idea.get("_close")

    reasons = []
    conv_delta = 0

    if entry_window is not None and entry_window > 2.0:
        reasons.append("chase-risk")
        conv_delta -= 1
    elif entry_window is not None and entry_window > 0.5:
        reasons.append("entry-edge")

    if closes_val is not None and entry is not None and entry > 0:
        if direction == "long" and closes_val < entry * 0.95:
            reasons.append("pullback-not-yet")
            conv_delta -= 1
        elif direction == "short" and closes_val > entry * 1.05:
            reasons.append("pullback-not-yet")
            conv_delta -= 1

    multiplier = _ASSET_CLASS_MULTIPLIERS.get(idea.get("asset_class", ""), 1.0)
    mature_threshold = 30.0 * multiplier
    late_threshold = 50.0 * multiplier

    if multiplier > 1.0:
        reasons.append("asset-class-scaled")

    if move_maturity is not None and move_maturity > late_threshold:
        reasons.append("late-move")
        conv_delta -= 2
    elif move_maturity is not None and move_maturity > mature_threshold:
        reasons.append("mature-move")
        conv_delta -= 1

    if reasons:
        idea["veto_reasons"] = reasons
        idea["conviction"] = max(1, min(5, idea["conviction"] + conv_delta))
        idea["version"] = conviction_version(idea["conviction"])
        if "veto_reasons" not in idea.get("reasoning", "") and reasons:
            idea["reasoning"] = f"{idea['reasoning']} Pattern S: {', '.join(reasons)}."


def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
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

    # l2_classification returns None if pattern didn't actually fire (invariant:
    # present=True AND classification is not None). Defends against L2 verdicts
    # that leak a classification without a coherent present flag.
    classification = l2_classification(tq_result)
    bo_classification = l2_classification(bo_result)
    tq_pattern = tq_result["pattern"]

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    # Healthy uptrend — standard breakout/pullback entry
    if classification in ("HEALTHY_UPTREND", "HEALTHY_PULLBACK_UPTREND"):
        entry = price
        stop = entry - atr * 2
        risk = entry - stop
        boost = 1 if bo_classification and "BREAKOUT" in str(bo_classification) else 0
        is_pullback = classification == "HEALTHY_PULLBACK_UPTREND"
        conviction = max(1, min(5, tq_pattern["confidence"] + boost - (1 if is_pullback else 0)))

        if is_pullback:
            reasoning = (
                f"Healthy uptrend in pullback phase ({classification}), "
                f"bounce entry at current price. "
                f"Stop below EMA50 or recent higher low."
            )
        else:
            reasoning = f"Healthy uptrend ({classification}), pullback or breakout entry."

        move_maturity_pct, entry_window_validity_pct = _compute_maturity_metrics(closes, entry)
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.5), round_price(entry + atr * 0.5)],
                "stop_loss": round_price(stop),
                # BUGS-2026-07-08-3: clamp TP3 at the 5% boundary. risk × 4
                # is normally well above 5%, but on tight ATR with a high
                # entry price (e.g. large-cap stocks), the clamp catches
                # any underflow before the validator rejects.
                "take_profit": [
                    round_price(entry + risk * 1.5),
                    round_price(entry + risk * 2.5),
                    round_price(max(entry + risk * 4, l3_tp3_dead_zone_floor(entry))),
                ],
                "take_profit_ideal": [
                    entry + risk * 1.5,
                    entry + risk * 2.5,
                    max(entry + risk * 4, l3_tp3_dead_zone_floor(entry)),
                ],
                "reasoning": reasoning,
                "source_skills": ["market-trend-quality", "market-breakout"],
                "move_maturity_pct": move_maturity_pct,
                "entry_window_validity_pct": entry_window_validity_pct,
                "asset_class": asset_class,
                "_close": closes[-1],
            }
        )

    # WEAKENING with intact bullish structure — partial bull case
    if classification == "WEAKENING" and _detect_intact_bullish_structure(tq_result):
        entry = price
        stop = entry - atr * 2
        risk = entry - stop
        conviction = max(1, min(5, tq_pattern["confidence"] - 1))
        move_maturity_pct, entry_window_validity_pct = _compute_maturity_metrics(closes, entry)
        # BUGS-2026-07-08-3: 5% clamp on TP3.
        tp3 = max(entry + risk * 4, l3_tp3_dead_zone_floor(entry))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.5), round_price(entry + atr * 0.5)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry + risk * 1.5),
                    round_price(entry + risk * 2.5),
                    round_price(tp3),
                ],
                "take_profit_ideal": [
                    entry + risk * 1.5,
                    entry + risk * 2.5,
                    tp3,
                ],
                "reasoning": (
                    "WEAKENING classification but macro structure intact "
                    "(HH/HL majority bullish, EMA alignment bullish). "
                    "Partial bull case — re-entry at current price."
                ),
                "source_skills": ["market-trend-quality", "market-breakout"],
                "move_maturity_pct": move_maturity_pct,
                "entry_window_validity_pct": entry_window_validity_pct,
                "asset_class": asset_class,
                "_close": closes[-1],
            }
        )

    if classification == "HEALTHY_DOWNTREND":
        entry = price
        stop = entry + atr * 2
        risk = stop - entry
        boost = 1 if bo_classification and "BREAKOUT" in str(bo_classification) else 0
        conviction = min(5, tq_pattern["confidence"] + boost)
        move_maturity_pct, entry_window_validity_pct = _compute_maturity_metrics(closes, entry)
        # BUGS-2026-07-08-3: 5% ceiling on TP3 for shorts.
        tp3 = min(entry - risk * 4, l3_tp3_dead_zone_ceiling(entry))
        ideas.append(
            {
                "pair": ticker,
                "direction": "short",
                "conviction": conviction,
                "version": conviction_version(conviction),
                "entry_type": "limit",
                "entry_price": round_price(entry),
                "entry_range": [round_price(entry - atr * 0.5), round_price(entry + atr * 0.5)],
                "stop_loss": round_price(stop),
                "take_profit": [
                    round_price(entry - risk * 1.5),
                    round_price(entry - risk * 2.5),
                    round_price(tp3),
                ],
                "take_profit_ideal": [
                    entry - risk * 1.5,
                    entry - risk * 2.5,
                    tp3,
                ],
                "reasoning": f"Healthy downtrend ({classification}), breakdown entry.",
                "source_skills": ["market-trend-quality", "market-breakout"],
                "move_maturity_pct": move_maturity_pct,
                "entry_window_validity_pct": entry_window_validity_pct,
                "asset_class": asset_class,
                "_close": closes[-1],
            }
        )

    tp_rejection = None
    if ideas:
        validated = []
        for idea in ideas:
            _apply_pattern_s(idea)
            idea.pop("_close", None)
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
        dirs = ", ".join(i["direction"] for i in ideas)
        narrative = f"Trend-follow setup: {dirs}. {tq_result.get('narrative', '')}"
    elif tp_rejection is not None:
        narrative = tp_rejection
    elif stop_2pct_rejection is not None:
        narrative = stop_2pct_rejection
    else:
        narrative = tq_result.get("narrative", "No trend-follow setup detected.")

    return {"ideas": ideas, "narrative": narrative}
