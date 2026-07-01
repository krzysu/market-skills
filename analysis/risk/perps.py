"""Perps execution risk policies.

Each policy takes an Intent + RiskContext and returns a RiskVerdictFragment.
Pure functions — no I/O, no network, no DB.

All perps policies short-circuit on spot intents via :func:`_is_perps_intent`
(returns APPROVED with reason "no objection"). The dispatcher
(``analysis.risk.vet``) only routes perps intents through this list, so
the short-circuit is defence-in-depth — it keeps the policies correct even
if a future caller passes a perps policy to a spot-only flow.
"""

from __future__ import annotations

from analysis.contracts import RiskVerdictFragment
from analysis.providers.execution.base import Intent
from analysis.providers.execution.kraken_perps import (
    DEFAULT_LEVERAGE_CAP,
    KRAKEN_FUTURES_MAP,
    LEVERAGE_CAPS,
    leverage_cap_for_pair,
    mm_rate_for_pair,
)

from ._common import RiskContext, _empty_fragment


def _is_perps_intent(intent: Intent) -> bool:
    """True when the intent targets a perps venue.

    Perps policies skip spot intents entirely (return APPROVED with "n/a").
    """
    return intent.get("venue", "").endswith("-perps") or intent.get("leverage") is not None


def leverage_cap_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """REJECT when ``intent.leverage`` exceeds the tier cap for the pair.

    Spot intents are ignored (return APPROVED with ``n/a``). The cap
    resolves in this order (later wins):

      1. ``ctx.leverage_caps[pair]`` — set by ``policies.yaml``'s
         ``perps.leverage_caps`` block.
      2. ``LEVERAGE_CAPS[pair]`` from
         ``analysis.providers.execution.kraken_perps`` (BTC/ETH/SOL=2x).
      3. ``ctx.default_leverage_cap`` — set by
         ``perps.default_leverage_cap`` in the YAML.
      4. ``DEFAULT_LEVERAGE_CAP = 5`` from the provider.
    """
    if not _is_perps_intent(intent):
        return _empty_fragment("leverage_cap")
    lev = intent.get("leverage")
    if lev is None:
        return RiskVerdictFragment(
            policy="leverage_cap",
            status="CONCERN",
            reason="perps intent without leverage — cannot validate",
        )
    pair = intent["pair"]
    cap = _resolve_leverage_cap(pair, ctx)
    if int(lev) > cap:
        return RiskVerdictFragment(
            policy="leverage_cap",
            status="REJECT",
            reason=(
                f"requested leverage {int(lev)}x exceeds {cap}x cap for "
                f"{pair} (per-pair override > code default in "
                f"analysis.providers.execution.kraken_perps.LEVERAGE_CAPS)"
            ),
            detail={
                "cap": cap,
                "requested": int(lev),
                "from_override": pair.upper() in ctx.leverage_caps,
                "default_table": dict(LEVERAGE_CAPS),
            },
        )
    return _empty_fragment("leverage_cap")


def _resolve_leverage_cap(pair: str, ctx: RiskContext) -> int:
    """Resolve the effective leverage cap for ``pair``.

    Order: ctx override > code LEVERAGE_CAPS > ctx default > code default.
    """
    pair_upper = pair.upper()
    if pair_upper in ctx.leverage_caps:
        return ctx.leverage_caps[pair_upper]
    code_cap = leverage_cap_for_pair(pair)
    # leverage_cap_for_pair already does the LEVERAGE_CAPS -> DEFAULT_LEVERAGE_CAP
    # fallback, so we only need to honor a ctx override on the default.
    if code_cap != DEFAULT_LEVERAGE_CAP or pair_upper in LEVERAGE_CAPS:
        return code_cap
    return ctx.default_leverage_cap if ctx.default_leverage_cap is not None else code_cap


def _resolve_mm_rate(pair: str, ctx: RiskContext) -> float | None:
    """Resolve the first-tier maintenance margin rate for ``pair``.

    Order: ``ctx.mm_rates[pair]`` (policies.yaml) > ``MM_RATES[pair]`` in
    the provider > ``ctx.maintenance_margin_rate`` (auto-fetched by
    risk-engine when --maintenance-margin-rate is set, which is the
    caller-supplied single-pair MM).

    Returns ``None`` when nothing is known — caller (the policy) treats
    that as "skip with CONCERN".
    """
    pair_upper = pair.upper()
    if pair_upper in ctx.mm_rates:
        return ctx.mm_rates[pair_upper]
    code_rate = mm_rate_for_pair(pair)
    if code_rate is not None:
        return code_rate
    return ctx.maintenance_margin_rate


def liquidation_distance_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """REJECT when the liquidation price is too close to entry.

    Linear approximation: ``move_to_liq = 1/leverage + mm_rate``; liq
    is ``entry * (1 ± move_to_liq)`` depending on direction. The venue's
    tiered MM schedule means the real liq differs by a few percent at
    higher notional — the policy uses the first-tier (smallest notional)
    rate from ``ctx.maintenance_margin_rate`` for the most conservative
    case. Pair-level overrides (notional-tier-aware MM) belong in the
    caller; this policy is intentionally a flat lower bound.

    Spot intents are ignored. Perps without ``maintenance_margin_rate``
    are skipped (caller didn't fetch the instrument spec) — the LLM
    narrates this as a CONCERN so the user can refresh the spec.
    """
    if not _is_perps_intent(intent):
        return _empty_fragment("liquidation_distance")
    bracket = intent.get("bracket") or {}
    stop_loss = bracket.get("stop_loss")
    lev = intent.get("leverage")
    mm_rate = _resolve_mm_rate(intent["pair"], ctx)
    if stop_loss is None or lev is None:
        return RiskVerdictFragment(
            policy="liquidation_distance",
            status="CONCERN",
            reason="perps intent missing bracket.stop_loss or leverage — cannot compute liq distance",
        )
    if mm_rate is None:
        return RiskVerdictFragment(
            policy="liquidation_distance",
            status="CONCERN",
            reason="maintenance_margin_rate not loaded — caller should populate from instrument spec",
        )

    # Estimate entry from the stop side: long entry > stop, short entry < stop.
    # We use the bracket's stop_loss + a 0% buffer (caller knows the actual
    # entry; the conservative case treats stop as the reference price).
    # In practice the caller passes an explicit entry via intent.extras
    # ``reference_entry`` — fall back to the stop side otherwise.
    entry = (intent.get("extras") or {}).get("reference_entry")
    if entry is None:
        return RiskVerdictFragment(
            policy="liquidation_distance",
            status="CONCERN",
            reason="reference entry not provided — pass entry via intent.extras.reference_entry",
        )
    entry = float(entry)

    move_to_liq = 1.0 / float(lev) + float(mm_rate)
    if intent["side"] == "buy":  # long
        liq_price = entry * (1.0 - move_to_liq)
        liq_distance_pct = (entry - liq_price) / entry * 100
    else:  # short
        liq_price = entry * (1.0 + move_to_liq)
        liq_distance_pct = (liq_price - entry) / entry * 100

    if liq_distance_pct < ctx.liq_min_distance_pct:
        # Suggested leverage: largest integer that keeps the floor.
        denom = (ctx.liq_min_distance_pct / 100) + float(mm_rate)
        suggested = max(1, int(1.0 / denom)) if denom > 0 else 1
        return RiskVerdictFragment(
            policy="liquidation_distance",
            status="REJECT",
            reason=(
                f"liquidation {liq_distance_pct:.2f}% from entry below the "
                f"{ctx.liq_min_distance_pct:.0f}% floor — reduce leverage to "
                f"{suggested}x or less (or add the protective stop farther from entry)"
            ),
            detail={
                "entry": entry,
                "liq_price": round(liq_price, 4),
                "liq_distance_pct": round(liq_distance_pct, 4),
                "min_distance_pct": ctx.liq_min_distance_pct,
                "suggested_leverage": suggested,
            },
        )
    return _empty_fragment("liquidation_distance")


def stop_distance_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """REJECT when the perps bracket stop is outside the swing bucket.

    Swing mode (position-trade tier) treats stops tighter than 2% as
    noise-risk and stops wider than 25% as oversized for the tier. This
    applies to the bracket's ``stop_loss`` relative to the reference
    entry supplied via ``intent.extras.reference_entry``.

    Spot intents are ignored. Perps without ``reference_entry`` skip
    with a CONCERN.
    """
    if not _is_perps_intent(intent):
        return _empty_fragment("stop_distance")
    bracket = intent.get("bracket") or {}
    stop_loss = bracket.get("stop_loss")
    entry = (intent.get("extras") or {}).get("reference_entry")
    if stop_loss is None or entry is None:
        return RiskVerdictFragment(
            policy="stop_distance",
            status="CONCERN",
            reason="perps intent missing bracket.stop_loss or extras.reference_entry — cannot measure distance",
        )
    entry = float(entry)
    distance_pct = abs(entry - float(stop_loss)) / entry * 100
    if distance_pct < ctx.stop_min_distance_pct:
        return RiskVerdictFragment(
            policy="stop_distance",
            status="REJECT",
            reason=(
                f"stop {distance_pct:.2f}% from entry below the "
                f"{ctx.stop_min_distance_pct:.0f}% swing minimum — noise risk in swing mode"
            ),
            detail={
                "distance_pct": round(distance_pct, 4),
                "min_pct": ctx.stop_min_distance_pct,
            },
        )
    if distance_pct > ctx.stop_max_distance_pct:
        return RiskVerdictFragment(
            policy="stop_distance",
            status="REJECT",
            reason=(
                f"stop {distance_pct:.2f}% from entry above the "
                f"{ctx.stop_max_distance_pct:.0f}% swing maximum — too wide for swing mode"
            ),
            detail={
                "distance_pct": round(distance_pct, 4),
                "max_pct": ctx.stop_max_distance_pct,
            },
        )
    return _empty_fragment("stop_distance")


def funding_drag_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """CONCERN when projected funding drag exceeds the threshold.

    Kraken flexible futures charge every 8h. A ``funding_hold_days`` hold
    pays ``3 * hold_days`` charges. The policy expects
    ``ctx.funding_rate_per_8h`` in the "this trade pays" sign convention:
    positive when the trade pays funding, negative when it receives.

    Status taxonomy:
      - ``CONCERN`` (advisory) when drag exceeds ``funding_warn_pct``.
      - ``APPROVED`` (``reason=""``) below the threshold.
      - Skipped with ``reason="n/a"`` when funding rate isn't loaded or
        the intent isn't a perps intent.

    Never REJECTs — funding is informational; the LLM narrates and the
    user decides whether to wait for a less expensive rate or proceed.
    """
    if not _is_perps_intent(intent):
        return _empty_fragment("funding_drag")
    rate = ctx.funding_rate_per_8h
    if rate is None:
        return RiskVerdictFragment(
            policy="funding_drag",
            status="CONCERN",
            reason="funding rate not loaded — caller should populate ctx.funding_rate_per_8h",
        )
    charges = 3 * int(ctx.funding_hold_days)
    drag_pct = float(rate) * 100 * charges
    # Notional best-effort: position_value (in quote ccy) if supplied,
    # otherwise fall back to volume * reference_entry for size context.
    extras = intent.get("extras") or {}
    notional = extras.get("position_value") or extras.get("reference_entry", 0) * intent.get("volume", 0)
    drag_quote = (drag_pct / 100) * float(notional) if notional else 0.0

    detail = {
        "funding_rate_per_8h_pct": round(float(rate) * 100, 4),
        "hold_days": int(ctx.funding_hold_days),
        "charges": charges,
        "drag_pct_of_notional": round(drag_pct, 4),
        "drag_quote": round(drag_quote, 4),
    }
    if abs(drag_pct) >= float(ctx.funding_warn_pct):
        return RiskVerdictFragment(
            policy="funding_drag",
            status="CONCERN",
            reason=(
                f"funding drag {drag_pct:+.2f}% of notional over "
                f"{int(ctx.funding_hold_days)}d ({charges} charges) "
                f"exceeds {float(ctx.funding_warn_pct):.0f}% threshold"
            ),
            detail=detail,
        )
    return _empty_fragment("funding_drag")


def duplicate_perps_position_policy(intent: Intent, ctx: RiskContext) -> RiskVerdictFragment:
    """REJECT when an open perps position matches the intent's pair+side.

    Pyramiding into the same thesis without explicit confirmation is a
    known blow-up pattern. Spot intents are ignored. The mapping from
    futures symbol (``PF_SOLUSD``) to spot pair (``SOLUSD``) is shared
    with the perps provider (``KRAKEN_FUTURES_MAP``).
    """
    if not _is_perps_intent(intent):
        return _empty_fragment("duplicate_perps_position")
    if not ctx.open_perps_positions:
        return _empty_fragment("duplicate_perps_position")

    target_pair = intent["pair"].upper()
    target_side = intent["side"]
    duplicates = []
    for pos in ctx.open_perps_positions:
        sym = pos.get("symbol", "")
        size = float(pos.get("size", 0) or 0)
        if not sym or size == 0:
            continue
        spot = next((p for p, f in KRAKEN_FUTURES_MAP.items() if f == sym), "")
        if spot.upper() != target_pair:
            continue
        pos_side = "buy" if size > 0 else "sell"
        if pos_side == target_side:
            duplicates.append({"symbol": sym, "size": size, "side": pos_side})

    if duplicates:
        return RiskVerdictFragment(
            policy="duplicate_perps_position",
            status="REJECT",
            reason=(
                f"{len(duplicates)} open {target_side} position(s) already on "
                f"{target_pair} — pyramiding without explicit confirmation"
            ),
            detail={"duplicates": duplicates},
        )
    return _empty_fragment("duplicate_perps_position")


PERPS_POLICIES = [
    leverage_cap_policy,
    liquidation_distance_policy,
    stop_distance_policy,
    funding_drag_policy,
    duplicate_perps_position_policy,
]
