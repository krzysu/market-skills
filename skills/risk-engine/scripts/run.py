#!/usr/bin/env python3
"""risk-engine — vet an Intent against portfolio + watchlist state.

Reads portfolio-mgmt + market-watchlist (read-only), builds a RiskContext,
runs analysis.risk.vet(), emits a RiskVerdict. Advisory, not a hard gate —
the LLM narrates the verdict, then asks the user to confirm before calling
execution-kraken.

Usage:
  # Vet an Intent file (machine input, --json for LLM consumption)
  uv run skills/risk-engine/scripts/run.py --intent intent.json --portfolio spot --json

  # Vet direct flags (interactive)
  uv run skills/risk-engine/scripts/run.py \
    --pair HYPEUSD --side buy --order-type limit --volume 1.5 --limit-price 60.15 \
    --portfolio spot

  # Without portfolio context (policies degrade to no-info CONCERN)
  uv run skills/risk-engine/scripts/run.py --intent intent.json

Exit codes:
  0 — verdict emitted (even if status=REJECT; REJECT is advisory)
  1 — fatal: bad input, missing portfolio DB
  2 — bad input
"""

import argparse
import json
import os
import sys
import uuid

from analysis.providers.execution.base import Intent, validate_intent
from analysis.risk import (
    apply_global_overrides,
    apply_pair_overrides,
    apply_portfolio_overrides,
    load_policy_overrides,
    vet,
)
from analysis.skill_loader import load_lib_for_script

ENV_POLICIES = "MARKET_SKILLS_RISK_POLICIES_PATH"

_lib = load_lib_for_script(__file__)


def _emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _build_intent_from_args(args: argparse.Namespace) -> Intent:
    if args.intent:
        if not os.path.exists(args.intent):
            print(f"error: intent file not found: {args.intent}", file=sys.stderr)
            sys.exit(2)
        with open(args.intent) as f:
            raw = json.load(f)
        return validate_intent(raw)
    intent_id = args.intent_id or f"risk-{uuid.uuid4()}"
    direct = {
        "intent_id": intent_id,
        "venue": args.venue or "kraken",
        "pair": args.pair,
        "side": args.side,
        "order_type": args.order_type,
        "volume": args.volume,
        "limit_price": args.limit_price,
        "stop_price": args.stop_price,
    }
    return validate_intent(direct)


def main() -> int:
    p = argparse.ArgumentParser(
        prog="risk-engine",
        description="Vet an Intent against portfolio + watchlist + recent trades. Advisory, not a hard gate.",
    )
    p.add_argument("--intent", help="Path to a JSON file containing an Intent")
    p.add_argument("--pair", help="Trading pair (direct mode)")
    p.add_argument("--side", choices=["buy", "sell"], help="Order side (direct mode)")
    p.add_argument("--venue", help="Venue (direct mode); defaults to 'kraken' (spot). Use 'kraken-perps' for perps.")
    p.add_argument("--order-type", help="Order type (direct mode)")
    p.add_argument("--volume", type=float, help="Base-asset volume (direct mode)")
    p.add_argument("--limit-price", type=float, help="Limit/trigger price (direct mode)")
    p.add_argument("--stop-price", type=float, help="Stop price (direct mode)")
    p.add_argument("--intent-id", help="Idempotency key (direct mode)")

    p.add_argument(
        "--portfolio",
        help="Portfolio name or id (risk-engine reads portfolio-mgmt for context)",
    )
    p.add_argument(
        "--db",
        default=None,
        help="Path to portfolio-mgmt SQLite DB (default: $MARKET_SKILLS_PORTFOLIO_DB)",
    )
    p.add_argument(
        "--watchlist",
        default=os.environ.get("MARKET_SKILLS_WATCHLIST_PATH"),
        help="Path to market-watchlist JSON (for tier metadata)",
    )
    p.add_argument(
        "--drawdown-pct",
        type=float,
        help="Override current portfolio drawdown percentage (otherwise 0.0)",
    )
    p.add_argument(
        "--refresh-prices",
        action="store_true",
        help="Force-refresh portfolio-mgmt price cache before vetting (otherwise reads the cron-refreshed cache)",
    )
    p.add_argument(
        "--perps-account",
        help=(
            "Auto-fetch perps state (open positions, current funding rate) for the "
            "kraken-perps flow. Triggers `kraken futures positions` (auth required) "
            "and `kraken futures historical-funding-rates <symbol>` for the intent's "
            "pair. Skip on systems without kraken futures auth — the perps policies "
            "degrade to no-info (APPROVED/CONCERN, never REJECT) when state is missing."
        ),
    )
    p.add_argument(
        "--funding-rate-per-8h",
        type=float,
        help=(
            "Override `ctx.funding_rate_per_8h` directly. Sign convention: "
            "positive = this trade pays funding. Wins over auto-fetch from "
            "--perps-account. Use for tests and for callers that have the rate "
            "from another source (e.g. market-basis)."
        ),
    )
    p.add_argument(
        "--maintenance-margin-rate",
        type=float,
        help=(
            "Override `ctx.maintenance_margin_rate` directly. Wins over the "
            "static MM_RATES table in analysis/providers/execution/kraken_perps.py. "
            "Use for tests or when the caller knows the per-notional-tier rate."
        ),
    )
    p.add_argument(
        "--open-perps-positions",
        help=(
            "Override `ctx.open_perps_positions` directly as a JSON list of "
            "{symbol, size} entries (positive=long, negative=short). Wins "
            "over auto-fetch from --perps-account. Example: "
            '\'[{"symbol":"PF_SOLUSD","size":-10.0}]\'.'
        ),
    )
    p.add_argument(
        "--config",
        default=os.environ.get(ENV_POLICIES),
        help=(
            f"Path to policies.yaml (default: ${{{ENV_POLICIES}}} or "
            f"skills/risk-engine/data/policies.yaml). See POLICIES_CONFIG.md."
        ),
    )
    p.add_argument(
        "--include-macro",
        action="store_true",
        default=True,
        help=(
            "Fetch macro regime and populate ctx.macro_regime_risk_appetite "
            "so the regime_consistency policy can warn on counter-macro "
            "intents. On by default; network-call budget is small (one "
            "CoinGecko + one Alternative.me + four yfinance tickers)."
        ),
    )
    p.add_argument(
        "--no-macro",
        action="store_true",
        help=(
            "Skip the macro fetch entirely; regime_consistency policy stays a "
            "no-op (APPROVED). Use for micro-tests or when you want "
            "deterministic verdicts without the cross-asset dependency."
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout (for LLM tool-use)")
    args = p.parse_args()

    if args.db is None:
        args.db = os.environ["MARKET_SKILLS_PORTFOLIO_DB"]

    try:
        intent = _build_intent_from_args(args)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    ctx = _lib.build_context(args)
    try:
        overrides = load_policy_overrides(args.config)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    apply_global_overrides(ctx, overrides)
    apply_portfolio_overrides(ctx, overrides, args.portfolio)
    apply_pair_overrides(ctx, overrides, intent["pair"])
    verdict = vet(intent, ctx)

    if args.json:
        _emit_json({"context": _ctx_to_dict(ctx), "verdict": verdict})
        return 0

    print(_lib.render_verdict(verdict))
    print("\nReminder: this verdict is advisory. the execution skill confirm is the actual safety layer.")
    return 0


def _ctx_to_dict(ctx) -> dict:
    """RiskContext -> dict for JSON serialization."""
    return {
        "portfolio_id": ctx.portfolio_id,
        "portfolio_name": ctx.portfolio_name,
        "base_ccy": ctx.base_ccy,
        "total_value": ctx.total_value,
        "cash_available": ctx.cash_available,
        "current_drawdown_pct": ctx.current_drawdown_pct,
        "tier_exposure": ctx.tier_exposure,
        "tier_limits": ctx.tier_limits,
        "daily_trade_count": ctx.daily_trade_count,
        "daily_trade_budget": ctx.daily_trade_budget,
        "pair_cooldown_hours": ctx.pair_cooldown_hours,
        "max_position_pct": ctx.max_position_pct,
        "max_drawdown_pct": ctx.max_drawdown_pct,
        "positions": {
            k: {"qty": v.get("qty"), "market_value": v.get("market_value")} for k, v in ctx.positions.items()
        },
        "perps": {
            "open_positions": ctx.open_perps_positions,
            "funding_rate_per_8h": ctx.funding_rate_per_8h,
            "maintenance_margin_rate": ctx.maintenance_margin_rate,
        },
        "macro": {
            "risk_appetite": ctx.macro_regime_risk_appetite,
        },
    }


if __name__ == "__main__":
    sys.exit(main())
