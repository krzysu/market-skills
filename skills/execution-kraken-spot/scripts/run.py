#!/usr/bin/env python3
"""execution-kraken-spot — CLI for placing Kraken spot orders.

Subcommands:

    submit   Place an order (default if no subcommand given).
             Reads an Intent from --intent FILE or from direct flags,
             optionally validates via ``kraken order --validate`` first,
             prompts for confirmation unless --yes, submits, polls for
             fill, and (on success) writes a row to portfolio-mgmt.
    balance  Show cash balances via ``kraken balance``.
    orders   Show open orders via ``kraken open-orders``.
    cancel   Cancel one open order by order id (``kraken order cancel``).

The script is the human-in-the-loop gate. Live orders always go to the
venue unless --dry-run. There is no paper mode by design (see SKILL.md).
"""

import argparse
import json
import os
import sys
import uuid

from analysis.providers.execution import (
    kraken_spot as _execution_kraken,  # noqa: F401 — side-effect: registers provider
)
from analysis.providers.execution._cli_common import (
    _confirm,
    _emit_json,
    _resolve_portfolio_id,
)
from analysis.providers.execution.base import (
    Intent,
    get_execution_provider,
)
from analysis.skill_loader import load_lib_for_script

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)

_lib = load_lib_for_script(__file__)


def _vet_afk_gate(intent, args):
    """Wrap ``analysis.risk.afk.vet_afk`` with execution-skill defaults.

    Returns the verdict (``APPROVED`` / ``CONCERN`` / ``REJECT``).
    Failures during gate evaluation degrade to APPROVED — the gate is
    advisory in the sense that a broken disk read shouldn't block
    trades; the rule that *intentional* REJECTs block the order still
    holds (the rule evaluation itself never returns APPROVED when the
    rule fired).
    """
    try:
        from analysis.risk.afk import AFKContext, CircuitBreakerState, vet_afk
    except ImportError:
        return {"gate": "passed", "status": "APPROVED", "reason": "afk module unavailable", "detail": {}}

    state = CircuitBreakerState.load()
    # ``total_value`` is unknown to the execution script — the AFK
    # layer treats ``total_value<=0`` as fail-open for the position cap
    # gate (matching risk-engine's behaviour on missing portfolio data).
    ctx = AFKContext(total_value=0.0, base_ccy="USD")
    try:
        return vet_afk(intent, ctx, state)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"warning: AFK gate evaluation failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {"gate": "passed", "status": "APPROVED", "reason": "afk evaluation error", "detail": {}}


def _resolve_intent(args: argparse.Namespace) -> Intent:
    """Build an Intent from --intent FILE or direct flags."""
    if args.intent:
        intent = _lib.load_intent_file(args.intent)
    else:
        intent_id = args.intent_id or f"cli-{uuid.uuid4()}"
        direct = {
            "pair": args.pair,
            "side": args.side,
            "order_type": args.order_type,
            "volume": args.volume,
            "limit_price": args.limit_price,
            "stop_price": args.stop_price,
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


# ───────────────────────────────────────────────────────────────────── submit


def cmd_submit(args: argparse.Namespace) -> int:
    try:
        intent = _resolve_intent(args)
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
    afk_verdict = _vet_afk_gate(intent, args)
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

    provider = get_execution_provider("kraken")

    if args.dry_run:
        try:
            import subprocess

            cmd = ["kraken", "order", intent["side"]]
            cmd += [
                intent["pair"].replace("-", "").replace("/", "").upper(),
                str(intent["volume"]),
                "--type",
                intent["order_type"],
            ]
            if intent["order_type"] != "market" and intent.get("limit_price") is not None:
                cmd += ["--price", str(intent["limit_price"])]
            if intent.get("stop_price") is not None:
                cmd += ["--price2", str(intent["stop_price"])]
            if intent.get("time_in_force"):
                cmd += ["--timeinforce", intent["time_in_force"]]
            if intent.get("intent_id"):
                cmd += ["--cl-ord-id", intent["intent_id"]]
            cmd += ["--validate", "-o", "json"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            validate_resp = None
            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    validate_resp = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    validate_resp = {"_parse_error": True, "_raw": proc.stdout[:500]}
            else:
                validate_resp = {"error": (proc.stderr or "kraken --validate failed").strip()}
        except FileNotFoundError:
            print("error: kraken CLI not found in PATH", file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print("error: kraken --validate timed out", file=sys.stderr)
            return 1

        if args.json:
            _emit_json(
                {
                    "mode": "dry-run",
                    "intent": intent,
                    "kraken_validate": validate_resp,
                }
            )
            return 0
        print(_lib.render_dry_run_result(intent, validate_resp))
        return 0

    # Live path. Show the order and (unless --yes) prompt for confirmation.
    if not args.yes:
        print(_lib.render_intent_summary(intent))
        if not _confirm("\nSubmit this order to Kraken? (y/N) "):
            print("Aborted.")
            return 0

    confirmation = provider.place_order(
        intent,
        wait=not args.no_wait,
        timeout_s=args.wait_timeout,
    )

    # Write to portfolio-mgmt on positive fills when --portfolio is set.
    tx_id: int | None = None
    if args.portfolio and confirmation.get("status") in ("filled", "partial"):
        try:
            pid = _resolve_portfolio_id(args.db, args.portfolio)
        except SystemExit:
            return 2
        if pid is None:
            print(
                f"error: --portfolio is required to record the fill. Pass --portfolio <name|id> "
                f"or omit to skip portfolio-mgmt wiring (status={confirmation.get('status')})",
                file=sys.stderr,
            )
            return 2
        try:
            tx_id = _lib.write_fill_to_portfolio(confirmation, portfolio_id=pid, db_path=args.db, intent=intent)
        except Exception as e:
            print(f"warning: order placed but portfolio write failed: {e}", file=sys.stderr)

    if args.json:
        payload: dict = {
            "mode": "live",
            "intent": intent,
            "confirmation": confirmation,
        }
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


# ───────────────────────────────────────────────────────────────────── balance / orders / cancel


def cmd_balance(args: argparse.Namespace) -> int:
    provider = get_execution_provider("kraken")
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
    print(f"{'Asset':<{label_w}}  Balance")
    print("-" * (label_w + 12))
    for code, amount in sorted(balances.items()):
        print(f"{code:<{label_w}}  {amount:,.8f}")
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    provider = get_execution_provider("kraken")
    try:
        orders = provider.get_open_orders()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(orders)
        return 0
    if not orders:
        print("(no open orders)")
        return 0
    for o in orders:
        print(
            f"  {o.get('order_id', '?'):<14} {o.get('pair', '?'):<10} "
            f"{o.get('side', '?'):<5} {o.get('order_type', '?'):<14} "
            f"vol={o.get('volume', 0):.8f} price={o.get('limit_price') or '—'}"
        )
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    provider = get_execution_provider("kraken")
    ok = provider.cancel_order(args.order_id)
    if args.json:
        _emit_json({"order_id": args.order_id, "cancelled": ok})
    else:
        print(f"cancel {args.order_id}: {'ok' if ok else 'failed'}")
    return 0 if ok else 1


# ───────────────────────────────────────────────────────────────────── CLI


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="execution-kraken-spot",
        description=(
            "Place Kraken spot orders via the `kraken` CLI. Subcommands: submit (default), balance, orders, cancel."
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
        help="Place an order (default if no subcommand given).",
    )
    sub_submit.add_argument(
        "--intent",
        help="Path to a JSON file containing an Intent. Mutually exclusive with direct flags.",
    )
    sub_submit.add_argument("--pair", help="Trading pair, e.g. BTCUSD")
    sub_submit.add_argument("--side", choices=["buy", "sell"])
    sub_submit.add_argument(
        "--order-type",
        choices=["market", "limit", "stop-loss", "take-profit", "stop-loss-limit", "take-profit-limit"],
    )
    sub_submit.add_argument("--volume", type=float, help="Base-asset volume (e.g. 0.01 BTC)")
    sub_submit.add_argument("--limit-price", type=float, help="Limit / trigger price (required for non-market)")
    sub_submit.add_argument("--stop-price", type=float, help="Secondary trigger price for *-limit order variants")
    sub_submit.add_argument("--time-in-force", choices=["GTC", "IOC", "GTD"], help="Time in force")
    sub_submit.add_argument("--deadline", help="RFC3339 deadline for matching-engine arrival")
    sub_submit.add_argument(
        "--intent-id",
        help="Idempotency key. Default: cli-<uuid>. Passed as --cl-ord-id to Kraken.",
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
        help="Call `kraken order --validate` instead of submitting. No venue side-effects.",
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

    sub_balance = sub.add_parser("balance", help="Show Kraken cash balances.")
    sub_balance.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_balance.set_defaults(func=cmd_balance)

    sub_orders = sub.add_parser("orders", help="List Kraken open orders.")
    sub_orders.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_orders.set_defaults(func=cmd_orders)

    sub_cancel = sub.add_parser("cancel", help="Cancel a Kraken open order by id.")
    sub_cancel.add_argument("order_id", help="Kraken order id (txid)")
    sub_cancel.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub_cancel.set_defaults(func=cmd_cancel)

    return p


def main() -> int:
    parser = _build_parser()
    # `submit` is the default when no subcommand is given — make argparse
    # accept the flags directly under that assumption.
    argv = sys.argv[1:]
    if argv and argv[0] not in ("submit", "balance", "orders", "cancel", "-h", "--help"):
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
