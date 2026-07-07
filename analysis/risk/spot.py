"""Spot execution risk policies.

Each policy takes an Intent + RiskContext and returns a RiskVerdictFragment.
Pure functions — no I/O, no network, no DB.

Spot policies short-circuit on perps intents? No — they don't need to. The
dispatcher (``analysis.risk.vet``) only routes perps intents through the
perps policy list, so spot policies never see a perps intent in practice.
The default fragment for non-firing policies is APPROVED with reason
"no objection".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analysis.contracts import RiskVerdictFragment
from analysis.providers.execution.base import Intent

from ._common import RiskContext, _empty_fragment


def position_size_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """SCALE / REJECT when intent cost exceeds ``max_position_pct`` of portfolio.

    Approximation: ``cost = intent.volume * intent.limit_price`` for limit
    orders; market orders use the most-recent close (caller should populate
    ``ctx`` with a price hint). For ``buy`` intents, the cost is the
    notional; for ``sell``, we size against the position's current value
    (proposed exit size as % of held).
    """
    if ctx.total_value <= 0:
        return _empty_fragment("position_size")

    notional = 0.0
    if intent["side"] == "buy":
        lp = intent.get("limit_price")
        if lp is not None:
            notional = intent["volume"] * lp
        else:
            # No price reference — can't size without it.
            return RiskVerdictFragment(
                policy="position_size",
                status="CONCERN",
                reason="no limit_price on buy intent — cannot size-check",
            )
    else:  # sell
        # Look up held qty for the asset.
        asset = f"kraken:{intent['pair'].replace('-', '').replace('/', '').upper()}"
        pos = ctx.positions.get(asset) or {}
        held = float(pos.get("qty") or 0)
        if held <= 0:
            return RiskVerdictFragment(
                policy="position_size",
                status="CONCERN",
                reason=f"sell intent on {asset} but no open position in portfolio",
            )
        lp = intent.get("limit_price")
        pos_price = pos.get("current_price")
        if lp is None and (pos_price is None or float(pos_price) <= 0):
            # No price reference — can't size without it. Mirrors the buy
            # branch so a missing price cache doesn't silently APPROVE.
            return RiskVerdictFragment(
                policy="position_size",
                status="CONCERN",
                reason=(
                    f"sell intent on {asset} has no limit_price and held "
                    f"position has no current_price — cannot size-check"
                ),
            )
        notional = intent["volume"] * float(lp if lp is not None else pos_price)

    pct = (notional / ctx.total_value) * 100
    if pct <= ctx.max_position_pct:
        return _empty_fragment("position_size")

    # SCALE: shrink to max_position_pct. REJECT only if even scaled size
    # rounds to zero or the user is trying to bypass by 10x+.
    suggested_volume = (ctx.total_value * ctx.max_position_pct / 100) / (notional / intent["volume"])

    if pct > ctx.max_position_pct * 3:
        return RiskVerdictFragment(
            policy="position_size",
            status="REJECT",
            reason=(
                f"position size {pct:.1f}% of portfolio is more than 3x the "
                f"{ctx.max_position_pct:.0f}% target — recommend splitting into smaller orders"
            ),
            detail={"notional": notional, "pct": pct, "max_pct": ctx.max_position_pct},
        )

    # CONCERN if just over the cap (within 10%); SCALE when meaningfully over.
    over_pct = ((pct - ctx.max_position_pct) / ctx.max_position_pct) * 100
    if over_pct <= 10:
        return RiskVerdictFragment(
            policy="position_size",
            status="CONCERN",
            reason=(
                f"position size {pct:.1f}% of portfolio is just over the "
                f"{ctx.max_position_pct:.0f}% target — review before submitting"
            ),
            detail={"notional": notional, "pct": pct, "max_pct": ctx.max_position_pct},
        )

    return RiskVerdictFragment(
        policy="position_size",
        status="SCALE",
        reason=(
            f"position size {pct:.1f}% of portfolio exceeds target "
            f"{ctx.max_position_pct:.0f}% — scale to ~{suggested_volume:.6f}"
        ),
        detail={"notional": notional, "pct": pct, "max_pct": ctx.max_position_pct},
        suggested_volume=suggested_volume,
    )


def portfolio_drawdown_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """REJECT when portfolio drawdown exceeds ``max_drawdown_pct``.

    Defensive posture: a portfolio that's already down >20% should not be
    adding new risk without an explicit user explanation. The LLM narrates
    the rejection but execution-kraken-spot/-perps still respects --yes if user
    overrides.
    """
    if ctx.current_drawdown_pct >= ctx.max_drawdown_pct:
        return RiskVerdictFragment(
            policy="portfolio_drawdown",
            status="REJECT",
            reason=(
                f"portfolio drawdown {ctx.current_drawdown_pct:.1f}% exceeds "
                f"max {ctx.max_drawdown_pct:.0f}% — defensive posture; user "
                f"should justify new exposure"
            ),
            detail={"drawdown_pct": ctx.current_drawdown_pct, "max_pct": ctx.max_drawdown_pct},
        )
    if ctx.current_drawdown_pct >= ctx.max_drawdown_pct * 0.75:
        return RiskVerdictFragment(
            policy="portfolio_drawdown",
            status="CONCERN",
            reason=(
                f"portfolio drawdown {ctx.current_drawdown_pct:.1f}% is within "
                f"75% of the {ctx.max_drawdown_pct:.0f}% max — proceed with caution"
            ),
            detail={"drawdown_pct": ctx.current_drawdown_pct, "max_pct": ctx.max_drawdown_pct},
        )
    return _empty_fragment("portfolio_drawdown")


def per_tier_exposure_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """SCALE / REJECT when adding to a tier pushes exposure above its cap.

    Tier is resolved from ``ctx.watchlist_metadata[bare_ticker].tier`` (falls
    back to CONCERN if the pair isn't registered). The notional is the same
    computation as in ``position_size_policy``.
    """
    if not ctx.tier_limits:
        return _empty_fragment("per_tier_exposure")

    bare = intent["pair"].replace("-", "").replace("/", "").upper()
    tier = (ctx.watchlist_metadata.get(bare) or {}).get("tier")
    if tier is None:
        return RiskVerdictFragment(
            policy="per_tier_exposure",
            status="CONCERN",
            reason=f"pair {bare} has no tier metadata in watchlist — cannot tier-check",
        )
    limit = ctx.tier_limits.get(str(tier))
    if not limit:
        return _empty_fragment("per_tier_exposure")
    max_total = limit.get("max_total")
    max_pct = limit.get("max_pct")
    if max_total is None and max_pct is None:
        return _empty_fragment("per_tier_exposure")

    current = ctx.tier_exposure.get(str(tier), 0.0)
    notional = 0.0
    if intent["side"] == "buy" and intent.get("limit_price") is not None:
        notional = intent["volume"] * intent["limit_price"]
    elif intent["side"] == "sell":
        # Selling reduces exposure — no need to check.
        return _empty_fragment("per_tier_exposure")

    projected = current + notional
    over_total = max_total is not None and projected > max_total
    over_pct = max_pct is not None and ctx.total_value > 0 and (projected / ctx.total_value) * 100 > max_pct

    if over_total or over_pct:
        # Compute the headroom left at the most binding cap.
        binding_capacity = None
        if max_total is not None:
            binding_capacity = max_total - current
        if max_pct is not None and ctx.total_value > 0:
            pct_capacity = (ctx.total_value * max_pct / 100) - current
            binding_capacity = min(binding_capacity, pct_capacity) if binding_capacity is not None else pct_capacity

        common_detail = {
            "tier": tier,
            "current": current,
            "added": notional,
            "projected": projected,
            "max_total": max_total,
            "max_pct": max_pct,
        }

        # No headroom left at any cap — already over. SCALE can't help, and
        # the suggested volume would round to 0 (nonsensical). REJECT so the
        # LLM doesn't silently suggest "trade 0 units".
        if binding_capacity is not None and binding_capacity <= 0:
            return RiskVerdictFragment(
                policy="per_tier_exposure",
                status="REJECT",
                reason=(
                    f"tier {tier} exposure {current:.0f} {ctx.base_ccy} is already at "
                    f"or above its cap — adding {notional:.0f} cannot be scaled into compliance"
                ),
                detail=common_detail,
            )

        lp = intent.get("limit_price")
        if lp is None or lp <= 0:
            # Defensive: this branch is currently unreachable for buys that
            # triggered over_total/over_pct (notional=0 implies current
            # already exceeded the cap -> REJECT above). Kept as a safety net
            # for any future notional source that might let a no-price intent
            # into this branch.
            return RiskVerdictFragment(
                policy="per_tier_exposure",
                status="CONCERN",
                reason=(
                    f"tier {tier} projected exposure {projected:.0f} {ctx.base_ccy} "
                    f"would exceed its cap but intent has no usable limit_price — "
                    f"cannot compute a scaling volume"
                ),
                detail=common_detail,
            )

        suggested_volume = max(0.0, binding_capacity / lp)
        return RiskVerdictFragment(
            policy="per_tier_exposure",
            status="SCALE",
            reason=(
                f"tier {tier} projected exposure {projected:.0f} {ctx.base_ccy} "
                f"exceeds limit (current={current:.0f}, +{notional:.0f}, "
                f"max_total={max_total}, max_pct={max_pct})"
            ),
            detail=common_detail,
            suggested_volume=suggested_volume,
        )
    return _empty_fragment("per_tier_exposure")


def daily_budget_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """SCALE / REJECT when intent would exceed the daily trade budget.

    Counts the current day only. ``ctx.daily_trade_count`` is the count
    before this intent; this intent adds 1.
    """
    if ctx.daily_trade_count + 1 > ctx.daily_trade_budget:
        return RiskVerdictFragment(
            policy="daily_budget",
            status="REJECT",
            reason=(
                f"daily trade budget would be exceeded "
                f"({ctx.daily_trade_count + 1}/{ctx.daily_trade_budget}) — "
                f"consider waiting until tomorrow or raising the budget"
            ),
            detail={
                "current": ctx.daily_trade_count,
                "after": ctx.daily_trade_count + 1,
                "budget": ctx.daily_trade_budget,
            },
        )
    if ctx.daily_trade_count + 1 >= ctx.daily_trade_budget:
        return RiskVerdictFragment(
            policy="daily_budget",
            status="CONCERN",
            reason=(f"daily trade budget will be saturated ({ctx.daily_trade_count + 1}/{ctx.daily_trade_budget})"),
            detail={
                "current": ctx.daily_trade_count,
                "after": ctx.daily_trade_count + 1,
                "budget": ctx.daily_trade_budget,
            },
        )
    return _empty_fragment("daily_budget")


def insufficient_funds_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """REJECT a buy when cash < cost; CONCERN on a sell when held < sell qty.

    If the portfolio context is unloaded (``ctx.total_value == 0`` and
    ``ctx.cash_available == 0``) we cannot distinguish "zero cash" from
    "no info" — treat as CONCERN rather than REJECT. The LLM narrates
    and the user decides.
    """
    context_loaded = ctx.total_value > 0 or ctx.cash_available > 0
    if intent["side"] == "buy":
        cost = 0.0
        if intent.get("limit_price") is not None:
            cost = intent["volume"] * intent["limit_price"]
        else:
            return _empty_fragment("insufficient_funds")
        if cost > ctx.cash_available:
            if not context_loaded:
                return RiskVerdictFragment(
                    policy="insufficient_funds",
                    status="CONCERN",
                    reason=(
                        f"portfolio context not loaded — cannot verify cash "
                        f"({cost:.2f} {ctx.base_ccy} required); user should confirm"
                    ),
                    detail={"required": cost, "available": None},
                )
            return RiskVerdictFragment(
                policy="insufficient_funds",
                status="REJECT",
                reason=(f"insufficient cash: need {cost:.2f} {ctx.base_ccy}, have {ctx.cash_available:.2f}"),
                detail={"required": cost, "available": ctx.cash_available},
            )
        if cost > ctx.cash_available * 0.5:
            return RiskVerdictFragment(
                policy="insufficient_funds",
                status="CONCERN",
                reason=(
                    f"cash usage would be {cost / ctx.cash_available * 100:.0f}% of "
                    f"available balance ({ctx.cash_available:.2f} {ctx.base_ccy})"
                ),
                detail={"required": cost, "available": ctx.cash_available},
            )
        return _empty_fragment("insufficient_funds")
    # sell side
    asset = f"kraken:{intent['pair'].replace('-', '').replace('/', '').upper()}"
    pos = ctx.positions.get(asset) or {}
    held = float(pos.get("qty") or 0)
    if intent["volume"] > held:
        return RiskVerdictFragment(
            policy="insufficient_funds",
            status="REJECT",
            reason=(f"insufficient {asset}: want to sell {intent['volume']}, only hold {held}"),
            detail={"required": intent["volume"], "available": held, "asset": asset},
        )
    return _empty_fragment("insufficient_funds")


def per_pair_cooldown_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """CONCERN when same pair+side was traded within the cooldown window.

    Pure advisory — frequent trading isn't always wrong (e.g. DCA plans).
    The LLM narrates the cooldown so the user can confirm intent.
    """
    if ctx.pair_cooldown_hours <= 0 or not ctx.recent_trades:
        return _empty_fragment("per_pair_cooldown")

    bare = intent["pair"].replace("-", "").replace("/", "").upper()
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=ctx.pair_cooldown_hours)

    for t in ctx.recent_trades:
        ts_str = t.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if t.get("pair", "").replace("-", "").replace("/", "").upper() == bare and t.get("side") == intent["side"]:
            return RiskVerdictFragment(
                policy="per_pair_cooldown",
                status="CONCERN",
                reason=(
                    f"already traded {bare} {intent['side']} within "
                    f"{ctx.pair_cooldown_hours:.0f}h cooldown "
                    f"(last at {ts.isoformat()})"
                ),
                detail={"last_trade": t, "cooldown_hours": ctx.pair_cooldown_hours},
            )
    return _empty_fragment("per_pair_cooldown")


def regime_consistency_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """WARN when the intent direction conflicts with the macro regime.

    The macro ``risk_appetite`` axis is read off
    ``ctx.macro_regime_risk_appetite`` (populated by ``risk-engine`` from
    ``analysis.macro.fetch_regime()``).

    Direction inference follows the asymmetric rule the execution layer
    uses elsewhere:

      - ``intent.side == "buy"`` is treated as *bullish* (long open).
      - ``intent.side == "sell"`` is bearish *unless* the intent targets
        a pair with a held position: a sell against a held long is a
        risk-reducing exit, and a buy against a held short is a
        risk-reducing cover. Both are skipped entirely. Without a
        position, a sell is a short open.

    Counter-macro is **CONCERN, never REJECT** — risk-engine stays
    advisory; the execution skill's interactive confirm is the real
    safety gate (matches ``per_pair_cooldown``'s pattern). UNKNOWN is
    treated as adverse so a degraded regime (one or more sources
    failed — see ``analysis.macro.fetch_regime``) can never pass
    silently through the consistency check.
    """
    if ctx.macro_regime_risk_appetite is None:
        return _empty_fragment("regime_consistency")

    bare = intent["pair"].replace("-", "").replace("/", "").upper()
    asset = f"kraken:{bare}"
    held_qty = float((ctx.positions.get(asset) or {}).get("qty") or 0)

    # Reducing a held position in either direction (selling against a
    # long, buying against a short) is risk-reducing; the macro shouldn't
    # gate exits. Skip the policy on those paths.
    if intent["side"] == "sell" and held_qty > 0:
        return _empty_fragment("regime_consistency")
    if intent["side"] == "buy" and held_qty < 0:
        return _empty_fragment("regime_consistency")

    bullish = intent["side"] == "buy"
    macro = ctx.macro_regime_risk_appetite

    # Conflict matrix — anything outside the macro's directional bias is
    # counter-macro and fires CONCERN. UNKNOWN is treated as adverse so a
    # degraded regime can never pass silently (see analysis.macro.fetch_regime
    # for the downgrade path).
    #
    #   macro \ intent | long (buy) | short (sell on no position)
    #   ---------------+------------+---------------------------
    #   RISK_ON        | ok         | counter-macro (CONCERN)
    #   NEUTRAL        | ok         | counter-macro (CONCERN)
    #                   # NEUTRAL SHORT is the canonical counter-macro shape
    #                   # the consistency policy guards against silent pass-through.
    #   RISK_OFF       | counter    | ok
    #   CRISIS         | counter    | ok
    #   UNKNOWN        | counter    | counter
    #
    short_ok_macros = {"RISK_OFF", "CRISIS"}
    long_ok_macros = {"RISK_ON", "NEUTRAL"}
    conflicting = (bullish and macro not in long_ok_macros) or (not bullish and macro not in short_ok_macros)

    if not conflicting:
        return _empty_fragment("regime_consistency")

    direction = "long" if bullish else "short"
    unknown_note = " (regime degraded; one or more sources failed)" if macro == "UNKNOWN" else ""
    return RiskVerdictFragment(
        policy="regime_consistency",
        status="CONCERN",
        reason=(
            f"macro risk_appetite={macro}{unknown_note} conflicts with "
            f"{direction} intent on {bare} — counter-macro setup; user "
            f"should justify or wait for macro agreement"
        ),
        detail={"macro_risk_appetite": macro, "direction": direction, "pair": bare},
    )


SPOT_POLICIES = [
    position_size_policy,
    portfolio_drawdown_policy,
    per_tier_exposure_policy,
    daily_budget_policy,
    insufficient_funds_policy,
    per_pair_cooldown_policy,
    regime_consistency_policy,
]
