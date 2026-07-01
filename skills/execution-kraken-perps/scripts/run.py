#!/usr/bin/env python3
"""execution-kraken-perps — CLI for placing Kraken perps bracket orders.

Subcommands:
  submit     Place a perps bracket (open + stop + take-profit). Default if
             no subcommand given. Reads an Intent from ``--intent FILE``
             or from direct flags (``--pair`` / ``--side`` /
             ``--volume`` / ``--leverage`` / ``--stop-loss`` /
             ``--take-profit`` / ``--reference-entry`` / ``--position-value``).
             Validates via ``Intent`` schema + ``analysis.risk.vet`` (perps
             policies auto-selected by venue).
             ``--dry-run`` builds the Intent, runs risk vet, and prints the
             bracket summary without submitting. ``--yes`` skips the
             interactive confirm for LLM-driven runs where the user has
             pre-approved.
  balance    Show futures account balances (``kraken futures accounts``).
  positions  Show open perps positions (``kraken futures positions``).
  cancel     Cancel one open order by id (``kraken futures cancel``).

The CLI prompts for confirmation before live ``submit`` unless ``--yes``
is passed. There is no paper mode by design; ``--dry-run`` is the safe
pre-flight check that exercises the same code path without venue
side-effects.
"""

import argparse
import json
import os
import sys

from analysis.providers.execution import (
    kraken_perps as _execution_kraken_perps,  # noqa: F401 — side-effect: registers provider
)
from analysis.providers.execution.base import (
    Intent,
    get_execution_provider,
)
from analysis.skill_loader import load_lib_for_script

_lib = load_lib_for_script(__file__)


def _emit_json(payload: dict | list) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _confirm(prompt: str) -> bool:
    try:
        reply = input(prompt)
    except EOFError:
        return False
    return reply.strip().lower() in ("y", "yes")


def _vet_afk_gate(intent):
    """Wrap ``analysis.risk.afk.vet_afk`` for perps execution.

    Perps position sizing uses ``position_value`` (notional in quote
    ccy) instead of ``volume * limit_price``. The position_cap gate
    reads ``volume * limit_price`` and treats perps as
    ``volume * limit_price`` (best-effort); for AFK the notional comes
    from ``extras.position_value`` when supplied. We pass total_value=0
    so the gate degrades to fail-open on missing data, matching spot.
    """
    try:
        from analysis.risk.afk import AFKContext, CircuitBreakerState, vet_afk
    except ImportError:
        return {"gate": "passed", "status": "APPROVED", "reason": "afk module unavailable", "detail": {}}

    state = CircuitBreakerState.load()
    ctx = AFKContext(total_value=0.0, base_ccy="USD")
    # Perps intents: if extras.position_value is set, the cap reads it
    # as a synthetic "notional" by overriding intent['volume'] for the
    # AFK eval only — restore on the way out so the venue submit uses
    # the real volume.
    original_volume = intent.get("volume")
    original_limit_price = intent.get("limit_price")
    pv = (intent.get("extras") or {}).get("position_value")
    notional_volume = float(pv) if pv else original_volume
    synthetic_limit_price = 1.0
    intent["volume"] = notional_volume
    if original_limit_price is None:
        intent["limit_price"] = synthetic_limit_price
    try:
        return vet_afk(intent, ctx, state)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"warning: AFK gate evaluation failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {"gate": "passed", "status": "APPROVED", "reason": "afk evaluation error", "detail": {}}
    finally:
        intent["volume"] = original_volume
        if original_limit_price is None:
            intent.pop("limit_price", None)


def _resolve_portfolio_id(db_path: str, portfolio: str | None) -> int | None:
    """Resolve portfolio name/id to its DB row id."""
    if not portfolio:
        return None
    from portfolio.db import get_portfolio

    pf = get_portfolio(db_path, portfolio)
    if pf is None:
        print(f"error: portfolio '{portfolio}' not found in {db_path}", file=sys.stderr)
        sys.exit(2)
    return pf["id"]


def _resolve_intent(args: argparse.Namespace, *, intent_id: str) -> Intent:
    """Build an Intent from ``--intent`` JSON or direct flags."""
    if args.intent:
        intent = _lib.load_intent_file(args.intent)
    else:
        direct = {
            "pair": args.pair,
            "side": args.side,
            "order_type": args.order_type,
            "volume": args.volume,
            "limit_price": args.limit_price,
            "leverage": args.leverage,
            "stop_loss": args.stop_loss,
            "take_profit": args.take_profit,
            "position_value": args.position_value,
            "reference_entry": args.reference_entry,
            "time_in_force": args.time_in_force,
            "deadline": args.deadline,
            "thesis": args.thesis,
            "strategy": args.strategy,
            "conviction": args.conviction,
        }
        if args.source_skills:
            direct["source_skills"] = [s.strip() for s in args.source_skills.split(",") if s.strip()]
        intent = _lib.intent_from_direct_args(direct, intent_id=intent_id)

    decoration = _parse_decoration(args.decision_decoration, args.override_from_suggestion)
    if decoration:
        intent["decision_decoration"] = decoration
    return intent


def _parse_decoration(raw: str | None, override_from_suggestion: bool) -> dict | None:
    """Parse the --decision-decoration JSON blob + the --override-from-suggestion
    flag into a single ``decision_decoration`` dict. Returns ``None`` when
    neither source supplied anything.

    Schema (all keys optional, see ``analysis.decision.DecisionContext``):

      regime_label            str
      regime_fng              float
      regime_btc_dominance    float
      regime_divergence       str
      macro_signals           list[str]
      risk_status             str  (APPROVED|CONCERN|SCALE|REJECT|UNKNOWN)
      risk_position_size_pct  float
      risk_concerns           list[str]
      override_from_suggestion bool
      override_field          str
      override_reason         str
    """
    out: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"--decision-decoration must be valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError(f"--decision-decoration must be a JSON object, got {type(parsed).__name__}")
        out.update(parsed)
    if override_from_suggestion:
        out["override_from_suggestion"] = True
    return out or None


# ──────────────────────────────────────────────────────────────────── submit


def cmd_submit(args: argparse.Namespace) -> int:
    intent_id = args.intent_id or f"perps-{os.urandom(8).hex()}"
    try:
        intent = _resolve_intent(args, intent_id=intent_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if intent.get("status") == "REJECT":
        msg = f"refusing to execute REJECTED intent (reason: {intent.get('reject_reason', 'n/a')})"
        if args.json:
            _emit_json({"intent": intent, "result": {"status": "rejected", "reason": msg}})
        else:
            print(msg, file=sys.stderr)
        return 2
    if intent.get("status") == "SCALED" and intent.get("scaled_volume") is not None:
        intent["volume"] = intent["scaled_volume"]

    # AFK gate: block on position cap / sleep window / circuit breaker
    # BEFORE any network call. The gate is independent of risk-engine —
    # an AFK REJECT aborts even when risk-engine would APPROVE.
    afk_verdict = _vet_afk_gate(intent)
    if afk_verdict["status"] == "REJECT":
        msg = f"AFK gate REJECT ({afk_verdict['gate']}): {afk_verdict['reason']}"
        if args.json:
            _emit_json(
                {
                    "intent": intent,
                    "result": {
                        "status": "rejected",
                        "reason": msg,
                        "afk": afk_verdict,
                    },
                }
            )
        else:
            print(msg, file=sys.stderr)
        return 2

    # Local validation against the provider's published leverage cap.
    from analysis.providers.execution.kraken_perps import (
        leverage_cap_for_pair,
        resolve_futures_symbol,
    )

    # Validate the symbol map even on --dry-run so unknown pairs surface
    # before the operator invests in the bracket summary.
    if not ((intent.get("extras") or {}).get("futures_symbol")):
        try:
            resolve_futures_symbol(intent["pair"])
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    cap = leverage_cap_for_pair(intent["pair"])
    if intent.get("leverage") is not None and int(intent["leverage"]) > cap:
        print(
            f"error: requested leverage {int(intent['leverage'])}x exceeds tier cap {cap}x for {intent['pair']}",
            file=sys.stderr,
        )
        return 2

    provider = get_execution_provider("kraken-perps")

    if args.dry_run:
        if args.json:
            _emit_json({"mode": "dry-run", "intent": intent})
        else:
            print(_lib.render_intent_summary(intent))
        return 0

    if not args.yes:
        print(_lib.render_intent_summary(intent))
        if not _confirm("\nSubmit this perps bracket to Kraken? (y/N) "):
            print("Aborted.")
            return 0

    confirmation = provider.place_order(
        intent,
        wait=not args.no_wait,
        timeout_s=args.wait_timeout,
    )

    tx_id: int | None = None
    if args.portfolio and confirmation.get("status") in ("filled", "partial"):
        try:
            pid = _resolve_portfolio_id(args.db, args.portfolio)
        except SystemExit:
            return 2
        if pid is not None and confirmation.get("filled_volume", 0) > 0:
            try:
                tx_id = _lib.write_fill_to_portfolio(
                    confirmation,
                    portfolio_id=pid,
                    db_path=args.db,
                    intent=intent,
                )
            except Exception as e:
                print(f"warning: order placed but portfolio write failed: {e}", file=sys.stderr)

    if args.json:
        payload: dict = {"mode": "live", "intent": intent, "confirmation": confirmation}
        if tx_id is not None:
            payload["portfolio_tx_id"] = tx_id
        _emit_json(payload)
        return 0

    print(_lib.render_confirmation(confirmation))
    if tx_id is not None:
        print(f"\nRecorded in portfolio as transaction #{tx_id}.")
    if confirmation.get("status") == "error":
        return 1
    return 0


# ──────────────────────────────────────────────────── balance / positions / cancel


def cmd_balance(args: argparse.Namespace) -> int:
    provider = get_execution_provider("kraken-perps")
    try:
        balances = provider.get_balance()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(balances)
        return 0
    if not balances:
        print("(no balances)")
        return 0
    label_w = max(len(k) for k in balances)
    print(f"{'Currency':<{label_w}}  Balance")
    print("-" * (label_w + 12))
    for code, amount in sorted(balances.items()):
        print(f"{code:<{label_w}}  {amount:,.8f}")
    return 0


def cmd_positions(args: argparse.Namespace) -> int:
    provider = get_execution_provider("kraken-perps")
    positions = provider.get_positions()
    if args.json:
        _emit_json(positions)
        return 0
    if not positions:
        print("(no open positions)")
        return 0
    for p in positions:
        print(f"  {p.get('symbol', '?'):<14} {p.get('side', '?'):<5} size={p.get('size', 0):+.4f}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    provider = get_execution_provider("kraken-perps")
    ok = provider.cancel_order(args.order_id)
    if args.json:
        _emit_json({"order_id": args.order_id, "cancelled": ok})
    else:
        print(f"cancel {args.order_id}: {'ok' if ok else 'failed'}")
    return 0 if ok else 1


# ────────────────────────────────────────────────────────────────────── CLI


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="execution-kraken-perps",
        description=(
            "Place Kraken perps bracket orders (open + stop + take-profit) via the `kraken` CLI. "
            "Subcommands: submit (default), balance, positions, cancel."
        ),
    )
    sub = p.add_subparsers(dest="command")

    common_db = argparse.ArgumentParser(add_help=False)
    common_db.add_argument(
        "--db",
        default=None,
        help="Path to portfolio-mgmt SQLite DB (default: $MARKET_SKILLS_PORTFOLIO_DB)",
    )

    sub_submit = sub.add_parser(
        "submit",
        parents=[common_db],
        help="Place a perps bracket (default if no subcommand given).",
    )
    sub_submit.add_argument(
        "--intent",
        help="Path to a JSON file containing an Intent. Mutually exclusive with direct flags.",
    )
    sub_submit.add_argument("--pair", help="Trading pair, e.g. SOLUSD")
    sub_submit.add_argument("--side", choices=["buy", "sell"])
    sub_submit.add_argument(
        "--order-type",
        choices=["market", "limit"],
        default="market",
        help="Open-leg order type (default: market)",
    )
    sub_submit.add_argument("--volume", type=float, help="Base-asset volume (e.g. 11.5 SOL)")
    sub_submit.add_argument("--limit-price", type=float, help="Limit price (for --order-type=limit)")
    sub_submit.add_argument("--leverage", type=int, help="Leverage multiplier (1x-50x; tier-capped per pair)")
    sub_submit.add_argument("--stop-loss", type=float, help="Stop-loss trigger price")
    sub_submit.add_argument("--take-profit", type=float, help="Take-profit trigger price")
    sub_submit.add_argument(
        "--position-value", type=float, help="Position notional in quote ccy (for funding drag calc)"
    )
    sub_submit.add_argument(
        "--reference-entry",
        type=float,
        help="Reference entry price for risk policies (liq distance, stop distance). Defaults to limit_price when present.",
    )
    sub_submit.add_argument("--time-in-force", choices=["GTC", "IOC", "GTD"], help="Time in force")
    sub_submit.add_argument("--deadline", help="RFC3339 deadline for matching-engine arrival")
    sub_submit.add_argument(
        "--intent-id",
        help="Idempotency key. Default: perps-<hex>. Passed as --client-order-id to Kraken.",
    )
    sub_submit.add_argument("--thesis", help="Free-text thesis (persisted in portfolio-mgmt notes)")
    sub_submit.add_argument("--strategy", help="Strategy name (persisted in portfolio-mgmt notes)")
    sub_submit.add_argument("--conviction", type=int, help="Conviction 1-5 (persisted in portfolio-mgmt notes)")
    sub_submit.add_argument(
        "--source-skills",
        help="Comma-separated skill names (persisted in portfolio-mgmt notes)",
    )
    sub_submit.add_argument(
        "--decision-decoration",
        help=(
            "JSON object merged into the auto-built decision_context (regime, "
            "macro signals, risk verdict, override fields). Keys: regime_label, "
            "regime_fng, regime_btc_dominance, regime_divergence, macro_signals, "
            "risk_status, risk_position_size_pct, risk_concerns, override_field, "
            "override_reason. Persisted to the decisions table."
        ),
    )
    sub_submit.add_argument(
        "--override-from-suggestion",
        action="store_true",
        help=(
            "Set decision_context.override.from_suggestion=true (the user accepted "
            "but modified the suggested stop/tp/volume). Persisted to the decisions table."
        ),
    )
    sub_submit.add_argument("--portfolio", help="Portfolio name or id to record the fill in (portfolio-mgmt)")
    sub_submit.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + render the Intent + run risk vet; no venue side-effect.",
    )
    sub_submit.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt (for LLM-driven runs where the user has explicitly pre-approved)",
    )
    sub_submit.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit and return immediately (status='submitted'). Skips fill polling.",
    )
    sub_submit.add_argument(
        "--wait-timeout",
        type=float,
        default=5.0,
        help="Max seconds to wait for a terminal fill status (default: 5.0). Ignored if --no-wait.",
    )
    sub_submit.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_submit.set_defaults(func=cmd_submit)

    sub_balance = sub.add_parser("balance", help="Show Kraken futures account balances.")
    sub_balance.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_balance.set_defaults(func=cmd_balance)

    sub_positions = sub.add_parser("positions", help="List open Kraken perps positions.")
    sub_positions.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_positions.set_defaults(func=cmd_positions)

    sub_cancel = sub.add_parser("cancel", help="Cancel an open futures order by id.")
    sub_cancel.add_argument("order_id", help="Kraken futures order id")
    sub_cancel.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_cancel.set_defaults(func=cmd_cancel)

    return p


def main() -> int:
    parser = _build_parser()
    argv = sys.argv[1:]
    if argv and argv[0] not in ("submit", "balance", "positions", "cancel", "-h", "--help"):
        argv = ["submit", *argv]
    args = parser.parse_args(argv)
    if getattr(args, "db", None) is None:
        args.db = os.environ["MARKET_SKILLS_PORTFOLIO_DB"]
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
