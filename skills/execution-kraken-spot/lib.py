"""execution-kraken-spot — pure helpers around the KrakenExecutionProvider.

This module is the testable layer. It contains:

  - Intent loading from JSON files and from a direct-args namespace
  - Human-readable order summary (confirm + dry-run table)
  - Wire-fill-into-portfolio helpers (no network or CLI; calls
    ``portfolio.db.add_transaction`` directly)

The CLI wrapper ``scripts/run.py`` is responsible for argparse, the
interactive confirm prompt, and dispatching to the provider. Keeping that
glue out of here means every helper below can be unit-tested without a
mocked stdin or a populated ``sys.argv``.
"""

import json
import os
from datetime import UTC, datetime
from typing import Any

from analysis.providers.execution.base import (
    FillConfirmation,
    Intent,
    validate_intent,
)

# ───────────────────────────────────────────────────────────────────── Intent loading


def load_intent_file(path: str) -> Intent:
    """Load and validate an Intent from a JSON file.

    Raises ``ValueError`` for malformed JSON or schema violations.
    """
    if not os.path.exists(path):
        raise ValueError(f"intent file not found: {path}")
    try:
        with open(path) as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"intent file {path} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"intent file {path} must contain a JSON object, got {type(raw).__name__}")
    return validate_intent(raw)


def intent_from_direct_args(args: dict[str, Any], *, intent_id: str) -> Intent:
    """Build an Intent from a flat args dict (parsed by argparse).

    Required keys: ``pair``, ``side``, ``order_type``, ``volume``.
    Optional: ``limit_price``, ``stop_price``, ``time_in_force``, ``deadline``,
    ``thesis``, ``source_skills``, ``conviction``, ``strategy``.

    Raises ``ValueError`` if required keys are missing or invalid.
    """
    required = ("pair", "side", "order_type", "volume")
    missing = [k for k in required if k not in args or args[k] is None]
    if missing:
        raise ValueError(f"missing required args for direct intent: {missing}")

    intent: dict[str, Any] = {
        "intent_id": intent_id,
        "venue": "kraken",
        "pair": args["pair"],
        "side": args["side"],
        "order_type": args["order_type"],
        "volume": float(args["volume"]),
        "limit_price": args.get("limit_price"),
        "stop_price": args.get("stop_price"),
        "time_in_force": args.get("time_in_force"),
        "deadline": args.get("deadline"),
    }
    for k in ("thesis", "source_skills", "conviction", "strategy"):
        v = args.get(k)
        if v is not None:
            intent[k] = v

    return validate_intent(intent)


# ───────────────────────────────────────────────────────────────────── Display


def _fmt_price(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1:
        return f"{v:,.4f}"
    if v >= 0.01:
        return f"{v:.6f}"
    return f"{v:.8f}"


def _fmt_volume(v: float) -> str:
    if v >= 1:
        return f"{v:.8f}".rstrip("0").rstrip(".") or "0"
    return f"{v:.8f}"


def render_intent_summary(intent: Intent) -> str:
    """Format an Intent as a multi-line table for human confirmation.

    Two-column "label: value" layout with box-drawing characters. Pure
    function — no I/O. Used by both the live confirm prompt and the
    dry-run path so the operator sees the same shape either way.
    """
    rows: list[tuple[str, str]] = [
        ("Intent ID", intent["intent_id"]),
        ("Venue", intent["venue"]),
        ("Pair", intent["pair"]),
        ("Side", intent["side"].upper()),
        ("Order type", intent["order_type"]),
        ("Volume", _fmt_volume(intent["volume"])),
    ]
    if intent.get("limit_price") is not None:
        rows.append(("Limit price", _fmt_price(intent["limit_price"])))
    if intent.get("stop_price") is not None:
        rows.append(("Stop/trigger price", _fmt_price(intent["stop_price"])))
    if intent.get("time_in_force"):
        rows.append(("Time in force", intent["time_in_force"]))
    if intent.get("deadline"):
        rows.append(("Deadline", intent["deadline"]))
    if intent.get("thesis"):
        rows.append(("Thesis", intent["thesis"]))
    if intent.get("strategy"):
        rows.append(("Strategy", intent["strategy"]))
    if intent.get("conviction") is not None:
        rows.append(("Conviction", f"{intent['conviction']}/5"))
    if intent.get("source_skills"):
        rows.append(("Source skills", ", ".join(intent["source_skills"])))
    if intent.get("status") and intent["status"] != "APPROVED":
        rows.append(("Risk status", intent["status"]))
        if intent.get("reject_reason"):
            rows.append(("Reject reason", intent["reject_reason"]))

    label_w = max(len(r[0]) for r in rows)
    lines = ["┌─ Order Intent ────────────────────────────────────────"]
    for label, value in rows:
        lines.append(f"│ {label:<{label_w}}  {value}")
    lines.append("└───────────────────────────────────────────────────────")
    return "\n".join(lines)


def render_dry_run_result(intent: Intent, validate_resp: dict | None) -> str:
    """Format a `kraken --validate` response under the intent summary.

    The Kraken CLI returns a response shape similar to a live submit on
    success (``{"descr": {...}, "txid": ["..."]}``) with the difference
    that no order actually hit the book. We surface the descr so the
    operator sees what the venue thinks will happen.
    """
    parts = [render_intent_summary(intent), ""]
    parts.append("─ Dry-run (kraken --validate, no submit) ─")
    if not validate_resp:
        parts.append("(no validate response)")
        return "\n".join(parts)
    if "error" in validate_resp:
        parts.append(f"  Kraken validation error: {validate_resp['error']}")
        return "\n".join(parts)
    descr = validate_resp.get("descr") or {}
    if isinstance(descr, dict):
        order_descr = descr.get("order", "")
        close_descr = descr.get("close", "")
        if order_descr:
            parts.append(f"  Order description : {order_descr}")
        if close_descr:
            parts.append(f"  Close description : {close_descr}")
    if validate_resp.get("txid"):
        parts.append(f"  Would-be txid     : {', '.join(validate_resp['txid'])}")
    return "\n".join(parts)


def render_confirmation(confirmation: FillConfirmation) -> str:
    """Format a FillConfirmation for human display.

    Used after a successful (or terminal-failed) placement. Pure function.
    """
    rows: list[tuple[str, str]] = [
        ("Order ID", confirmation.get("order_id") or "—"),
        ("Pair", confirmation["pair"]),
        ("Side", confirmation["side"].upper()),
        ("Order type", confirmation["order_type"]),
        ("Status", confirmation["status"].upper()),
    ]
    req = confirmation.get("requested_volume", 0)
    filled = confirmation.get("filled_volume", 0)
    rows.append(("Requested", _fmt_volume(req)))
    rows.append(("Filled", _fmt_volume(filled)))
    if confirmation.get("fill_price") is not None:
        rows.append(("Fill price", _fmt_price(confirmation["fill_price"])))
    if confirmation.get("cost_quote") is not None:
        rows.append(("Cost", _fmt_price(confirmation["cost_quote"])))
    if confirmation.get("fee"):
        rows.append(("Fee", f"{_fmt_price(confirmation['fee'])} {confirmation.get('fee_currency', '')}".strip()))
    if confirmation.get("reason"):
        rows.append(("Reason", confirmation["reason"]))
    rows.append(("Timestamp (UTC)", confirmation["timestamp"]))

    label_w = max(len(r[0]) for r in rows)
    lines = ["┌─ Fill Confirmation ───────────────────────────────────"]
    for label, value in rows:
        lines.append(f"│ {label:<{label_w}}  {value}")
    lines.append("└───────────────────────────────────────────────────────")
    return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────── Portfolio wiring


def portfolio_asset_symbol(pair: str, side: str) -> str:
    """Build the portfolio-mgmt ``asset`` field for a Kraken pair.

    Portfolio-mgmt's asset notation uses ``kraken:<PAIR>`` for routing.
    For sell sides we use the same notation — the ``side`` argument
    disambiguates buy vs sell without changing the asset symbol.
    """
    return f"kraken:{pair.replace('-', '').replace('/', '').upper()}"


def _quote_currency(pair: str) -> str:
    """Infer the quote currency (USD/EUR/GBP/etc.) from a Kraken pair suffix."""
    for suffix in ("USDT", "USDC", "USD", "EUR", "GBP", "AUD", "CAD", "JPY", "CHF"):
        if pair.upper().endswith(suffix) and len(pair) > len(suffix):
            return suffix
    return ""


def write_fill_to_portfolio(
    confirmation: FillConfirmation,
    *,
    portfolio_id: int,
    db_path: str,
    intent: Intent | None = None,
) -> int:
    """Append a fill row to portfolio-mgmt's SQLite DB.

    Returns the new transaction's row id. Skips the write if the fill is
    in a non-terminal-positive state (``status not in filled/partial``).

    Side is computed from the intent (when provided) — ``confirmation.side``
    is already correct for a successful order, but the caller may want to
    pass the intent for source-skill / thesis tagging.
    """
    status = confirmation.get("status")
    if status not in ("filled", "partial"):
        raise ValueError(f"refusing to write non-positive fill to portfolio (status={status!r})")

    filled = confirmation.get("filled_volume", 0)
    if filled <= 0:
        raise ValueError("refusing to write zero-volume fill to portfolio")

    fill_price = confirmation.get("fill_price")
    cost_quote = confirmation.get("cost_quote")
    fee = confirmation.get("fee", 0)
    fee_currency = confirmation.get("fee_currency", "")
    side_raw = confirmation.get("side", "buy")
    side = side_raw.upper() if isinstance(side_raw, str) else "BUY"
    pair = confirmation.get("pair", "")

    if fill_price is None and cost_quote is not None:
        fill_price = cost_quote / filled

    asset = portfolio_asset_symbol(pair, side_raw)

    notes_blob: dict[str, Any] = {
        "order_id": confirmation.get("order_id"),
        "cl_ord_id": confirmation.get("cl_ord_id"),
        "venue": confirmation.get("venue", "kraken"),
        "fill_price": fill_price,
        "fee": fee,
        "fee_currency": fee_currency,
        "source": "execution-kraken-spot",
    }
    if intent:
        for k in ("strategy", "source_skills", "thesis", "intent_id"):
            v = intent.get(k)
            if v is not None:
                notes_blob[k] = v

    # Record the decision trace in the decisions table.
    if intent:
        from analysis.decision import build_decision_context_from_idea, direction_from_side

        take_profit = []
        if intent.get("bracket"):
            bracket = intent["bracket"]
            tp = bracket.get("take_profit")
            if tp is not None:
                take_profit = [tp]
        elif intent.get("tp1"):
            take_profit = [intent[k] for k in ("tp1", "tp2", "tp3") if intent.get(k) is not None]

        decoration = intent.get("decision_decoration") or {}
        dc = build_decision_context_from_idea(
            intent_id=intent.get("intent_id", ""),
            source_skill=intent.get("strategy", "execution-kraken-spot"),
            idea={
                "direction": direction_from_side(side_raw),
                "conviction": intent.get("conviction"),
                "summary": intent.get("thesis"),
                "entry_price": fill_price,
                "stop_loss": intent.get("bracket", {}).get("stop_loss") if intent.get("bracket") else None,
                "take_profit": take_profit,
            },
            regime_label=decoration.get("regime_label"),
            regime_fng=decoration.get("regime_fng"),
            regime_btc_dominance=decoration.get("regime_btc_dominance"),
            regime_divergence=decoration.get("regime_divergence"),
            macro_signals=decoration.get("macro_signals"),
            risk_status=decoration.get("risk_status"),
            risk_position_size_pct=decoration.get("risk_position_size_pct"),
            risk_concerns=decoration.get("risk_concerns"),
            override_from_suggestion=bool(decoration.get("override_from_suggestion", False)),
            override_field=decoration.get("override_field"),
            override_reason=decoration.get("override_reason"),
        )
        notes_blob["decision_context"] = dc
        from portfolio.db import add_decision as _add_decision

        _add_decision(
            db_path,
            intent_id=intent.get("intent_id", ""),
            pair=pair,
            decision_context_json=json.dumps(dc),
            portfolio_id=portfolio_id,
            captured_at=dc["captured_at"],
        )

    # Late import to keep this module import-cheap for unit tests.
    from portfolio.db import add_transaction

    return add_transaction(
        db_path,
        portfolio_id,
        ts=datetime.now(UTC).isoformat(),
        side=side,
        asset=asset,
        qty=filled,
        price=fill_price,
        cost_quote=cost_quote,
        fee=fee or 0,
        tx_hash=confirmation.get("order_id"),
        source="execution-kraken-spot",
        ref=intent.get("intent_id") if intent else None,
        notes=json.dumps(notes_blob),
    )


__all__ = [
    "intent_from_direct_args",
    "load_intent_file",
    "portfolio_asset_symbol",
    "render_confirmation",
    "render_dry_run_result",
    "render_intent_summary",
    "write_fill_to_portfolio",
]
