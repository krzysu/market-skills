"""Shared types and helpers for the risk layer.

RiskContext lives here (not in __init__.py) so both spot.py and perps.py
can import it without triggering a circular import back through the
package's re-exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from analysis.contracts import RiskVerdictFragment
from analysis.providers.execution.base import Intent


@dataclass
class RiskContext:
    """Snapshot of portfolio + watchlist + market state for policy evaluation.

    Populated by the risk-engine skill from portfolio-mgmt + market-watchlist.
    All monetary values are in the portfolio's base currency. Timestamps are
    ISO 8601 UTC. Empty/None values mean "policy should treat as no info".
    """

    portfolio_id: int | None = None
    portfolio_name: str = ""
    base_ccy: str = "USD"
    total_value: float = 0.0  # portfolio base-ccy value (positions + cash)
    cash_available: float = 0.0  # free quote-currency balance
    current_drawdown_pct: float = 0.0  # 0.0 to 100.0; positive = underwater
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Position snapshot keyed by asset notation (``kraken:BTCUSD``). Each entry:
        {qty, avg_price, current_price, market_value, tier}
    """
    tier_exposure: dict[str, float] = field(default_factory=dict)
    """Sum of market_value per tier (``{"tier1": 5000, "tier2": 1200, ...}``).
    Free-form keys; policies consult ``tier_limits`` for the actual cap."""
    tier_limits: dict[str, dict[str, float]] = field(default_factory=dict)
    """Per-tier caps in base_ccy:
        {"tier1": {"max_pct": 60, "max_total": 50000}, ...}
    Keys mirror ``tier_exposure``."""
    watchlist_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Bare-ticker -> {tier, source, ...} from market-watchlist. Used to look up
    which tier a pair belongs to when ``positions`` doesn't have it."""
    reference_prices: dict[str, float] = field(default_factory=dict)
    """Bare ticker (uppercase, separators stripped — ``PENDLEEUR``) -> last known
    base-ccy price for the intent's pair. Populated by the risk-engine skill so a
    market order with no ``limit_price`` can still be costed. Empty = no info."""
    recent_trades: list[dict[str, Any]] = field(default_factory=list)
    """Trades already executed today. Each entry at minimum:
        {pair, side, intent_id, timestamp, qty, price}
    """
    daily_trade_count: int = 0
    daily_trade_budget: int = field(
        default=10, metadata={"yaml_key": "daily_budget", "yaml_coerce": int}
    )  # max trades per day; tweakable
    pair_cooldown_hours: float = field(
        default=4.0, metadata={"yaml_key": "cooldown_hours", "yaml_coerce": float}
    )  # same pair+direction within N hours -> CONCERN
    max_position_pct: float = field(
        default=25.0, metadata={"yaml_key": "max_position_pct", "yaml_coerce": float}
    )  # max single-position size as % of portfolio
    max_drawdown_pct: float = field(
        default=20.0, metadata={"yaml_key": "max_drawdown_pct", "yaml_coerce": float}
    )  # portfolio drawdown above this -> REJECT

    # Perps-specific context (ignored by spot policies). Populated by the
    # caller (typically skills/execution-kraken-perps) when venue == "kraken-perps".
    open_perps_positions: list[dict[str, Any]] = field(default_factory=list)
    """Open perps positions from ``kraken futures positions``. Each entry:
        ``{symbol: str, size: float}`` (positive=long, negative=short).
        Used by :func:`duplicate_perps_position_policy`.
    """
    funding_rate_per_8h: float | None = None
    """Current perps funding rate (Kraken flexible futures charges every 8h).
    Sign convention is "this trade pays": longs pay when funding > 0,
    shorts pay when funding < 0. None means caller didn't fetch — the
    funding policy treats None as a non-blocking CONCERN, not REJECT.
    """
    maintenance_margin_rate: float | None = None
    """Per-instrument maintenance margin rate (e.g. 0.01 for tier 1 SOL).
    Used by :func:`liquidation_distance_policy`. None = skip the policy.
    """
    funding_warn_pct: float = field(
        default=1.0, metadata={"yaml_key": "funding_warn_pct", "yaml_coerce": float}
    )  # 3-day funding drag > this % of notional -> CONCERN (advisory)
    liq_min_distance_pct: float = field(
        default=30.0, metadata={"yaml_key": "liq_min_distance_pct", "yaml_coerce": float}
    )  # minimum liquidation distance as % of entry; below this -> REJECT
    stop_min_distance_pct: float = field(
        default=2.0, metadata={"yaml_key": "stop_min_distance_pct", "yaml_coerce": float}
    )  # minimum stop-to-entry distance for swing mode; below this -> REJECT
    stop_max_distance_pct: float = field(
        default=25.0, metadata={"yaml_key": "stop_max_distance_pct", "yaml_coerce": float}
    )  # maximum stop-to-entry distance for swing mode; above this -> REJECT
    funding_hold_days: int = field(
        default=3, metadata={"yaml_key": "funding_hold_days", "yaml_coerce": int}
    )  # hold horizon for funding drag projection

    # Per-pair risk policy data overrides. Empty = fall through to the
    # code-defined defaults in ``analysis.providers.execution.kraken_perps``
    # (``LEVERAGE_CAPS``, ``MM_RATES``, ``DEFAULT_LEVERAGE_CAP``). The
    # per-pair dicts let users override specific pairs via ``policies.yaml``
    # without forking the code; pairs not in the dict use the code default.
    leverage_caps: dict[str, int] = field(default_factory=dict)
    mm_rates: dict[str, float] = field(default_factory=dict)
    default_leverage_cap: int | None = None

    # Macro context for the regime_consistency policy. Populated by
    # ``skills/risk-engine/lib.build_context`` when --include-macro is
    # set (default on). The policy reads ``risk_appetite`` (``RISK_ON``,
    # ``NEUTRAL``, ``RISK_OFF``, ``CRISIS``, ``UNKNOWN`` — the last one
    # means the regime was degraded because one or more sources failed;
    # see ``analysis.macro.fetch_regime``). ``None`` means the caller
    # didn't fetch the regime; the policy treats None as a no-op (APPROVED
    # with reason naming the missing piece) so a caller without macro
    # context never gets a phantom CONCERN.
    macro_regime_risk_appetite: str | None = None
    """Headline macro axis consumed by ``regime_consistency_policy``.

    Reads the only MacroRegime field the policy needs (the rest —
    liquidity / sentiment — are narrate-only). Stored as a flat string
    on the dataclass so building a RiskContext doesn't depend on
    importing ``analysis.macro`` (which would create an import cycle —
    macro imports nothing from risk, but risk can stay agnostic)."""


_STATUS_RANK = {"APPROVED": 0, "CONCERN": 1, "SCALE": 2, "REJECT": 3}


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))


def _empty_fragment(policy: str) -> RiskVerdictFragment:
    return RiskVerdictFragment(policy=policy, status="APPROVED", reason="no objection")


def _bare_pair(pair: str) -> str:
    """``PENDLE-EUR`` / ``PENDLE/EUR`` / ``PENDLEEUR`` -> ``PENDLEEUR``."""
    return pair.replace("-", "").replace("/", "").upper()


def _positive_number(value: Any) -> float | None:
    """Coerce to float when numeric and strictly positive; ``None`` otherwise.

    Non-numeric or non-positive values are skipped, never raised on — a
    malformed extra must degrade to "no info", not crash the vet.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _resolve_price_chain(intent: Intent, ctx: RiskContext) -> tuple[float | None, float | None, str, str]:
    """Shared resolution chain behind the two public resolvers.

    Returns ``(unit_price, raw_notional, source, kind)``. ``kind`` is
    ``"price"`` for base-ccy unit prices (``raw_notional`` is ``None``) and
    ``"notional"`` for explicit quote-ccy notionals (``unit_price`` is the
    notional divided by volume). Both are ``None`` when nothing resolves.
    """
    volume = _positive_number(intent.get("volume"))
    extras = intent.get("extras")
    if not isinstance(extras, dict):
        extras = {}

    limit_price = _positive_number(intent.get("limit_price"))
    if limit_price is not None:
        return limit_price, None, "limit_price", "price"

    reference_price = _positive_number(extras.get("reference_price"))
    if reference_price is not None:
        return reference_price, None, "extras.reference_price", "price"

    est_notional = _positive_number(extras.get("est_notional"))
    if est_notional is not None and volume is not None:
        return est_notional / volume, est_notional, "extras.est_notional", "notional"

    position_value = _positive_number(extras.get("position_value"))
    if position_value is not None and volume is not None:
        return position_value / volume, position_value, "extras.position_value", "notional"

    bare = _bare_pair(intent["pair"])
    for asset_key, pos in ctx.positions.items():
        key_bare = asset_key.split(":", 1)[-1] if ":" in asset_key else asset_key
        if _bare_pair(key_bare) == bare:
            current_price = _positive_number(pos.get("current_price"))
            if current_price is not None:
                return current_price, None, "position.current_price", "price"

    ctx_price = _positive_number(ctx.reference_prices.get(bare))
    if ctx_price is not None:
        return ctx_price, None, "ctx.reference_prices", "price"

    return None, None, "", ""


def resolve_reference_price(intent: Intent, ctx: RiskContext) -> tuple[float | None, str]:
    """(price, source) — price is None when nothing can be resolved.

    source is one of: "limit_price" | "extras.reference_price" |
    "extras.est_notional" | "extras.position_value" |
    "position.current_price" | "ctx.reference_prices" | ""

    Resolution order (first usable, strictly positive value wins):
    ``intent.limit_price`` -> ``intent.extras.reference_price`` ->
    ``intent.extras.est_notional`` / ``extras.position_value`` (quote-ccy
    notional converted to a unit price via volume) -> the held position's
    ``current_price`` for the intent's pair -> ``ctx.reference_prices``.
    For est_notional / position_value the returned price is the derived
    unit price (notional / volume); the source string still names the
    original key so callers can narrate provenance.
    """
    price, _notional, source, _kind = _resolve_price_chain(intent, ctx)
    return price, source


def resolve_intent_notional(intent: Intent, ctx: RiskContext) -> tuple[float | None, str]:
    """(notional_in_quote_ccy, source) — the cost basis for spot policies.

    notional = volume * resolve_reference_price() on the price sources
    ("limit_price", "extras.reference_price", "position.current_price",
    "ctx.reference_prices"), or the explicit quote-ccy notional on the
    notional sources ("extras.est_notional", "extras.position_value").
    Returns ``(None, "")`` when no price source resolves or the volume is
    unusable — callers degrade to CONCERN, never a silent APPROVED.
    """
    price, raw_notional, source, kind = _resolve_price_chain(intent, ctx)
    if price is None:
        return None, ""
    if kind == "notional":
        return raw_notional, source
    volume = _positive_number(intent.get("volume"))
    if volume is None:
        return None, ""
    return volume * price, source
