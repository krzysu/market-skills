"""risk-engine — pure helpers around analysis.risk.vet.

Same role as execution-kraken-spot/lib.py: testable layer that the CLI wrapper
imports. The skill's job is to:

  1. Build a RiskContext from the user's portfolio-mgmt DB +
     market-watchlist registry + today's trade log.
  2. Validate the input Intent.
  3. Call ``analysis.risk.vet()``.
  4. Render the verdict as JSON (for LLM tool-use) or formatted text.

No I/O for execution. No side effects on the DB. The CLI is a pure
read-and-recommend tool — the LLM narrates the verdict, then asks the
user to confirm before calling execution-kraken.
"""

import argparse
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.risk import RiskContext  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)


def _load_lib():
    lib_path = os.path.join(SKILL_DIR, "lib.py")
    spec = importlib.util.spec_from_file_location("risk_engine_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _strip_prefix(asset: str) -> str:
    """Strip provider prefix from an asset key (``kraken:PAXGEUR`` -> ``PAXGEUR``).

    portfolio-mgmt stores assets in ``provider:ticker`` notation (e.g.
    ``kraken:EUR`` for cash, ``kraken:HYPEUSD`` for held positions). Many
    downstream lookups need the bare ticker.
    """
    return asset.split(":", 1)[-1] if ":" in asset else asset


def _get_position_prices(
    db_path: str, assets: list[str], *, refresh: bool = False
) -> tuple[dict[str, float], dict[str, str]]:
    """Resolve live prices for the given held assets.

    Reads from portfolio-mgmt's ``price_cache`` table first (populated by
    ``portfolio.db.refresh_prices()`` as a cron job — same data the rest of
    the portfolio UI consumes). Falls back to ``fetch_spot_price`` only for
    assets missing from the cache, so a cold cache doesn't regress a hot one.

    Args:
        db_path: Path to portfolio-mgmt SQLite DB.
        assets:  Held assets in provider:ticker notation (e.g. ``kraken:HYPEUSD``).
        refresh: If True, call ``refresh_prices()`` first to repopulate the
                 cache from the data layer. Off by default — caller usually
                 trusts the cron-refreshed cache.

    Returns:
        (prices, sources) tuple. ``prices`` maps asset -> float price.
        ``sources`` maps asset -> ``"cache"`` or ``"spot"`` so the LLM can
        narrate "prices last refreshed 4 minutes ago" or "live fetched".
    """
    if not assets:
        return {}, {}

    from portfolio.db import get_cached_prices, refresh_prices

    if refresh:
        try:
            refresh_prices(db_path)
        except (OSError, ValueError, KeyError) as e:
            print(f"warning: refresh_prices failed: {e}", file=sys.stderr)

    prices: dict[str, float] = {}
    sources: dict[str, str] = {}

    try:
        cached = get_cached_prices(db_path)
    except (OSError, ValueError) as e:
        print(f"warning: price_cache read failed: {e}", file=sys.stderr)
        cached = {}

    for asset in assets:
        if asset in cached and cached[asset] is not None:
            prices[asset] = float(cached[asset])
            sources[asset] = "cache"
            continue

    missing = [a for a in assets if a not in prices]
    if not missing:
        return prices, sources

    try:
        from analysis.data import fetch_spot_price
    except ImportError as e:
        print(f"warning: analysis.data not importable: {e}", file=sys.stderr)
        return prices, sources

    for asset in missing:
        try:
            spot = fetch_spot_price(asset)
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"warning: live spot price fetch failed for {asset}: {e}", file=sys.stderr)
            continue
        if not spot:
            continue
        price = spot.get("price")
        if price is None:
            continue
        try:
            prices[asset] = float(price)
            sources[asset] = "spot"
        except (TypeError, ValueError):
            continue
    return prices, sources


def build_context(args: argparse.Namespace) -> RiskContext:
    """Build a RiskContext from portfolio-mgmt + watchlist + trade log.

    Reads only — never writes. Empty/None values are returned when data
    isn't available; policies treat that as "no info" and degrade to
    CONCERN (never REJECT on missing data).
    """
    from portfolio.db import (
        compute_positions,
        get_portfolio,
        list_transactions,
    )

    ctx = RiskContext()
    # Perps context populates even without a portfolio — the perps guardrails
    # don't need spot state, and the LLM might want to vet a perps intent
    # without loading the spot portfolio. The auto-fetch branch is skipped
    # when ``--perps-account`` isn't set; CLI overrides on ``args`` always win.
    _populate_perps_context(ctx, args)
    _populate_macro_context(ctx, args)
    if not args.portfolio:
        return ctx
    pf = get_portfolio(args.db, args.portfolio)
    if pf is None:
        print(f"error: portfolio '{args.portfolio}' not found in {args.db}", file=sys.stderr)
        sys.exit(2)
    ctx.portfolio_id = pf["id"]
    ctx.portfolio_name = pf["name"]
    ctx.base_ccy = pf.get("base_ccy", "USD")

    # First pass: positions without prices, just to learn the held-asset list.
    raw_positions = compute_positions(args.db, portfolio_id=pf["id"])

    # Resolve prices from portfolio-mgmt's price cache (refreshed by cron via
    # `portfolio.db.refresh_prices()`). Falls back to a live fetch for assets
    # missing from the cache. ``args.refresh_prices`` forces a re-refresh.
    # The cash-ccy position (e.g. ``kraken:EUR``) is excluded — it's already
    # denominated in base_ccy, so no price lookup is needed.
    cash_asset_key = ctx.base_ccy.upper()
    held_assets = [p["asset"] for p in raw_positions if _strip_prefix(p["asset"]).upper() != cash_asset_key]
    current_prices, _price_sources = _get_position_prices(
        args.db, held_assets, refresh=getattr(args, "refresh_prices", False)
    )

    # Second pass: positions enriched with live prices. compute_positions is
    # deterministic over (lots, current_prices) so this is the canonical view.
    positions = compute_positions(args.db, portfolio_id=pf["id"], current_prices=current_prices)

    # Asset keys are already in provider:ticker notation from portfolio-mgmt
    # (e.g. ``kraken:PAXGEUR``). Do NOT re-prefix. Translate compute_positions'
    # field names (``avg_cost``, ``current_value``) into RiskContext's
    # documented names (``avg_price``, ``market_value``).
    #
    # ``market_value`` falls back to ``cost_basis`` when ``current_value`` is
    # None (no live spot price in the cache). Without this, a cold price
    # cache zeroes out total_value, which makes position_size / per_tier /
    # insufficient_funds policies treat the portfolio as €0.00 — same shape
    # as a true zero-holdings portfolio. Cost basis is the most conservative
    # proxy: it never overstates holdings, and a fresh cron refresh of the
    # price cache will immediately replace it with the real market value.
    #
    # The cash-ccy row (e.g. ``kraken:EUR``) is excluded from the
    # market_value fallback — its qty is already extracted to
    # ``cash_available`` below, and summing it again would double-count
    # the cash leg of the portfolio.
    cash_asset_key = ctx.base_ccy.upper()
    ctx.positions = {}
    for p in positions:
        asset = p["asset"]
        is_cash = _strip_prefix(asset).upper() == cash_asset_key
        if is_cash:
            mv = 0.0
        else:
            cv = p.get("current_value")
            mv = float(cv) if cv is not None else float(p.get("cost_basis", 0) or 0)
        ctx.positions[asset] = {
            "qty": float(p.get("qty", 0)),
            "avg_price": float(p.get("avg_cost", 0) or 0),
            "current_price": float(p.get("current_price", 0) or 0),
            "market_value": mv,
            "tier": None,
        }

    # Cash available: look up the base_ccy position. Asset may be prefixed
    # (e.g. ``kraken:EUR``) — strip before comparing.
    cash_asset = ctx.base_ccy.upper()
    for p in positions:
        if _strip_prefix(p.get("asset", "")).upper() == cash_asset:
            ctx.cash_available = float(p.get("qty", 0) or 0)
            break

    # Per-tier exposure. Tiers come from the watchlist metadata if available.
    if args.watchlist:
        try:
            from analysis.watchlist import metadata_for

            for asset, pos in ctx.positions.items():
                bare = _strip_prefix(asset)
                meta = metadata_for(bare, path=args.watchlist) or {}
                pos["tier"] = meta.get("tier")
                tier_key = str(meta.get("tier")) if meta.get("tier") is not None else None
                if tier_key:
                    ctx.tier_exposure[tier_key] = ctx.tier_exposure.get(tier_key, 0.0) + pos["market_value"]
        except (ImportError, OSError) as e:
            print(f"warning: watchlist load failed: {e}", file=sys.stderr)

    # Total value = sum of position market values + cash.
    ctx.total_value = sum(p["market_value"] for p in ctx.positions.values()) + ctx.cash_available

    # Drawdown: ``--drawdown-pct`` (CLI override) wins over the auto-computed
    # value from portfolio-mgmt's peak tracking. Auto-compute uses the same
    # price cache the position sizing uses; cash rows (base_ccy) contribute
    # their full qty. If portfolio-mgmt fails (DB missing, no peak yet), we
    # fall back to 0.0 so the policy is informational rather than rejecting.
    if args.drawdown_pct is not None:
        ctx.current_drawdown_pct = float(args.drawdown_pct)
    else:
        try:
            from portfolio.db import compute_portfolio_drawdown

            ctx.current_drawdown_pct = float(compute_portfolio_drawdown(args.db, pf["id"], current_prices))
        except (OSError, ValueError, KeyError) as e:
            print(f"warning: portfolio drawdown unavailable: {e}", file=sys.stderr)

    # Daily trade count.
    today = datetime.now(UTC).date().isoformat()
    rows = list_transactions(args.db, portfolio_id=pf["id"], since=f"{today}T00:00:00+00:00")
    ctx.daily_trade_count = len(rows)
    ctx.recent_trades = [
        {
            "pair": _strip_prefix(r.get("asset", "")),
            "side": r.get("side", "").lower(),
            "intent_id": r.get("ref"),
            "timestamp": r.get("ts"),
            "qty": float(r.get("qty", 0) or 0),
            "price": float(r.get("price", 0) or 0),
        }
        for r in rows
    ]

    # Perps context was populated at the top of build_context (before the
    # portfolio early-return) so the perps branch runs even when no
    # portfolio is loaded.

    return ctx


def _populate_perps_context(ctx: RiskContext, args: argparse.Namespace) -> None:
    """Populate the perps-only ``RiskContext`` fields from args + auto-fetch.

    Resolution order per field (later wins):
      1. Default (``None`` / empty).
      2. Auto-fetch from ``kraken`` CLI when ``args.perps_account`` is set
         AND the intent targets a perps venue.
      3. CLI override on ``args`` (testing / explicit sourcing).

    The intent is read off ``args.intent`` (file path) or ``args.venue``
    (direct mode) to determine the venue and pair. Tests that build
    ``Namespace`` manually won't have either attribute — the perps
    branch is skipped.
    """
    from analysis.perp_state import get_funding_rate, get_mm_rate, get_open_positions

    venue, pair_from_file, side_from_file = _resolve_intent_meta(args)
    is_perps = bool(venue) and venue.endswith("-perps")
    pair = getattr(args, "pair", None) or pair_from_file
    side = getattr(args, "side", None) or side_from_file or "buy"

    # --- funding_rate_per_8h ---
    if getattr(args, "funding_rate_per_8h", None) is not None:
        ctx.funding_rate_per_8h = float(args.funding_rate_per_8h)
    elif is_perps and getattr(args, "perps_account", None) and pair:
        try:
            ctx.funding_rate_per_8h = get_funding_rate(pair, side)
        except (RuntimeError, ValueError) as e:
            print(f"warning: perps funding rate fetch failed: {e}", file=sys.stderr)

    # --- maintenance_margin_rate ---
    if getattr(args, "maintenance_margin_rate", None) is not None:
        ctx.maintenance_margin_rate = float(args.maintenance_margin_rate)
    elif is_perps and pair:
        # No auto-fetch for MM — the static MM_RATES dict in
        # analysis/providers/execution/kraken_perps.py is the source of
        # truth. Per-notional-tier overrides belong to a future caller.
        ctx.maintenance_margin_rate = get_mm_rate(pair)

    # --- open_perps_positions ---
    open_pos_arg = getattr(args, "open_perps_positions", None)
    if open_pos_arg is not None:
        try:
            raw = json.loads(open_pos_arg)
            if not isinstance(raw, list):
                raise ValueError("expected JSON list")
            ctx.open_perps_positions = [
                {"symbol": str(p["symbol"]), "size": float(p["size"])}
                for p in raw
                if isinstance(p, dict) and "symbol" in p and "size" in p
            ]
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            print(f"warning: --open-perps-positions parse failed: {e}", file=sys.stderr)
    elif is_perps and getattr(args, "perps_account", None):
        positions = get_open_positions()
        if positions is not None:
            ctx.open_perps_positions = positions


def _resolve_intent_meta(args: argparse.Namespace) -> tuple[str, str | None, str | None]:
    """Best-effort (venue, pair, side) lookup for the perps-context branch.

    The risk-engine CLI validates the Intent before ``build_context`` runs,
    so we have ``args.intent`` (file path) or direct-mode flags.
    Returns ``("", None, None)`` when nothing's set — caller treats that
    as a non-perps intent and skips the perps branch.
    """
    intent_path = getattr(args, "intent", None)
    if intent_path:
        try:
            with open(intent_path) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return (
                    str(raw.get("venue") or ""),
                    str(raw.get("pair") or "") or None,
                    str(raw.get("side") or "") or None,
                )
        except (OSError, json.JSONDecodeError):
            return "", None, None
    return str(getattr(args, "venue", None) or ""), None, None


def _populate_macro_context(ctx: RiskContext, args: argparse.Namespace) -> None:
    """Fetch the macro regime and assign ``ctx.macro_regime_risk_appetite``.

    Opt-in via ``--include-macro`` (default on). When a micro-test or ad-hoc
    vet wants deterministic no-macro semantics, ``--no-macro`` keeps the
    policy as a no-op (it returns APPROVED when ``macro_regime_risk_appetite``
    is ``None``). Fetch errors degrade to ``None`` for the same reason —
    network failure shouldn't propagate as a REJECT.
    """
    if getattr(args, "no_macro", False):
        return
    if not getattr(args, "include_macro", True):
        return
    try:
        from analysis.macro import fetch_regime
    except ImportError:
        return
    try:
        signal = fetch_regime()
    except Exception as e:  # noqa: BLE001 — macro fetch must not abort the vet
        print(f"warning: macro fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return
    regime = signal.get("regime") or {}
    risk_appetite = regime.get("risk_appetite")
    if isinstance(risk_appetite, str):
        ctx.macro_regime_risk_appetite = risk_appetite


def render_verdict(verdict: dict) -> str:
    """Format a RiskVerdict for human display."""
    rows = [
        ("Intent", f"{verdict['side']} {verdict['pair']}"),
        ("Status", verdict["status"]),
    ]
    if verdict.get("suggested_volume") is not None:
        rows.append(("Suggested volume", f"{verdict['suggested_volume']:.6f}"))
    rows.append(("Concerns", str(len(verdict.get("concerns", [])))))

    label_w = max(len(r[0]) for r in rows)
    lines = ["┌─ Risk Verdict (advisory) ──────────────────────────────"]
    for label, value in rows:
        lines.append(f"│ {label:<{label_w}}  {value}")
    lines.append("├─ Fragments ───────────────────────────────────────────")
    for f in verdict.get("fragments", []):
        lines.append(f"│  [{f['policy']:<22}] {f['status']:<8}  {f['reason']}")
    if verdict.get("narrative_hint"):
        lines.append("├─ Narrative hint ──────────────────────────────────────")
        lines.append(f"│  {verdict['narrative_hint']}")
    lines.append("└───────────────────────────────────────────────────────")
    return "\n".join(lines)


__all__ = ["build_context", "render_verdict", "_strip_prefix", "_get_position_prices"]
