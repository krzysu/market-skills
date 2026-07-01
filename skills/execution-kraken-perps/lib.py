"""execution-kraken-perps — pure helpers for the perps adapter CLI.

Mirrors the role of ``skills/execution-kraken-spot/lib.py``: pure,
testable layer that builds the testable building blocks for the CLI.

Public surface:
  load_intent_file              Load + validate Intent from JSON
  intent_from_direct_args       Build Intent from flat CLI args
  render_intent_summary         Human-readable bracket summary
  render_confirmation           Human-readable fill summary
  write_fill_to_portfolio       Append a perps fill to portfolio-mgmt

Portfolio notes distinguish perps fills (``source="execution-kraken-perps"``,
``side`` mirrors the direction: BUY for long, SELL for short).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from analysis.providers.execution.base import (
    FillConfirmation,
    Intent,
    validate_intent,
)


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
    """Build a perps Intent from a flat CLI args dict.

    Required keys: ``pair``, ``side``, ``volume``, ``leverage``,
    ``stop_loss``, ``take_profit``. Optional: ``limit_price``, ``order_type``
    (default ``"market"``), ``time_in_force``, ``deadline``, ``thesis``,
    ``strategy``, ``conviction``, ``position_value``, ``reference_entry``.

    Raises ``ValueError`` if required keys are missing or invalid.
    """
    required = ("pair", "side", "volume", "leverage", "stop_loss", "take_profit")
    missing = [k for k in required if args.get(k) is None]
    if missing:
        raise ValueError(f"missing required args for perps intent: {missing}")

    side = str(args["side"]).lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    intent: dict[str, Any] = {
        "intent_id": intent_id,
        "venue": "kraken-perps",
        "pair": str(args["pair"]),
        "side": side,
        "order_type": str(args.get("order_type") or "market"),
        "volume": float(args["volume"]),
        "leverage": int(args["leverage"]),
        "bracket": {
            "stop_loss": float(args["stop_loss"]),
            "take_profit": float(args["take_profit"]),
        },
    }
    for k in ("limit_price", "time_in_force", "deadline", "thesis", "strategy", "conviction"):
        v = args.get(k)
        if v is not None:
            intent[k] = v

    # Extras: position_value (notional in quote ccy), reference_entry (the
    # price the risk policies should anchor liq-distance to).
    extras: dict[str, Any] = {}
    if args.get("position_value") is not None:
        extras["position_value"] = float(args["position_value"])
    if args.get("reference_entry") is not None:
        extras["reference_entry"] = float(args["reference_entry"])
    if extras:
        intent["extras"] = extras

    return validate_intent(intent)


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
    """Format a perps Intent as a multi-line bracket summary."""
    rows: list[tuple[str, str]] = [
        ("Intent ID", intent["intent_id"]),
        ("Venue", intent["venue"]),
        ("Pair", intent["pair"]),
        ("Side", intent["side"].upper()),
        ("Volume", _fmt_volume(intent["volume"])),
    ]
    if intent.get("limit_price") is not None:
        rows.append(("Limit price", _fmt_price(intent["limit_price"])))
    if intent.get("leverage") is not None:
        rows.append(("Leverage", f"{int(intent['leverage'])}x"))
    bracket = intent.get("bracket")
    if bracket:
        rows.append(("Stop loss", _fmt_price(bracket.get("stop_loss"))))
        rows.append(("Take profit", _fmt_price(bracket.get("take_profit"))))
    extras = intent.get("extras") or {}
    if extras.get("position_value") is not None:
        rows.append(("Position notional", _fmt_price(extras["position_value"])))
    if extras.get("reference_entry") is not None:
        rows.append(("Reference entry", _fmt_price(extras["reference_entry"])))
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
    lines = ["┌─ Perps Bracket Intent ──────────────────────────────────"]
    for label, value in rows:
        lines.append(f"│ {label:<{label_w}}  {value}")
    lines.append("└───────────────────────────────────────────────────────────")
    return "\n".join(lines)


def render_confirmation(confirmation: FillConfirmation) -> str:
    """Format a perps FillConfirmation for human display."""
    rows: list[tuple[str, str]] = [
        ("Order ID (open)", confirmation.get("order_id") or "—"),
        ("Pair", confirmation["pair"]),
        ("Side", confirmation["side"].upper()),
        ("Status", confirmation["status"].upper()),
    ]
    bracket = confirmation.get("bracket") or {}
    if bracket.get("open_order_id"):
        rows.append(("  Open order id", str(bracket["open_order_id"])))
    if bracket.get("stop_order_id"):
        rows.append(("  Stop order id", str(bracket["stop_order_id"])))
    if bracket.get("take_profit_order_id"):
        rows.append(("  TP order id", str(bracket["take_profit_order_id"])))
    req = confirmation.get("requested_volume", 0)
    filled = confirmation.get("filled_volume", 0)
    rows.append(("Requested", _fmt_volume(req)))
    rows.append(("Filled", _fmt_volume(filled)))
    if confirmation.get("fill_price") is not None:
        rows.append(("Fill price", _fmt_price(confirmation["fill_price"])))
    if confirmation.get("cost_quote") is not None:
        rows.append(("Cost", _fmt_price(confirmation["cost_quote"])))
    if confirmation.get("fee"):
        fee_text = f"{_fmt_price(confirmation['fee'])} {confirmation.get('fee_currency', '')}".strip()
        rows.append(("Fee", fee_text))
    if confirmation.get("reason"):
        rows.append(("Reason", confirmation["reason"]))
    rows.append(("Timestamp (UTC)", confirmation["timestamp"]))

    label_w = max(len(r[0]) for r in rows)
    lines = ["┌─ Fill Confirmation ────────────────────────────────────"]
    for label, value in rows:
        lines.append(f"│ {label:<{label_w}}  {value}")
    lines.append("└───────────────────────────────────────────────────────────")
    return "\n".join(lines)


def portfolio_asset_symbol(pair: str) -> str:
    """Asset notation for portfolio-mgmt: ``kraken:<PAIR>`` (upper, no separators)."""
    return f"kraken:{pair.replace('-', '').replace('/', '').upper()}"


def write_fill_to_portfolio(
    confirmation: FillConfirmation,
    *,
    portfolio_id: int,
    db_path: str,
    intent: Intent | None = None,
) -> int:
    """Append a perps fill row to portfolio-mgmt's SQLite DB.

    Side mirrors the intent direction: BUY for long perps, SELL for short
    perps. Asset notation is ``kraken:<PAIR>`` (matches the spot adapter).
    The ``notes`` JSON carries the bracket order ids, leverage, and
    stop/tp prices so downstream consumers can reconstruct the trade.
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

    asset = portfolio_asset_symbol(pair)

    notes_blob: dict[str, Any] = {
        "venue": "kraken-perps",
        "open_order_id": confirmation.get("order_id"),
        "bracket": confirmation.get("bracket") or {},
        "fill_price": fill_price,
        "fee": fee,
        "fee_currency": fee_currency,
        "source": "execution-kraken-perps",
    }
    if intent:
        for k in ("strategy", "source_skills", "thesis", "intent_id", "leverage"):
            v = intent.get(k)
            if v is not None:
                notes_blob[k] = v
        bracket = intent.get("bracket") or {}
        for k in ("stop_loss", "take_profit"):
            v = bracket.get(k)
            if v is not None:
                notes_blob[k] = v

        # Record the decision trace in the decisions table.
        from analysis.decision import build_decision_context_from_idea, direction_from_side

        sl = bracket.get("stop_loss")
        tp = bracket.get("take_profit")
        decoration = intent.get("decision_decoration") or {}
        dc = build_decision_context_from_idea(
            intent_id=intent.get("intent_id", ""),
            source_skill=intent.get("strategy", "execution-kraken-perps"),
            idea={
                "direction": direction_from_side(side_raw),
                "conviction": intent.get("conviction"),
                "summary": intent.get("thesis"),
                "entry_price": fill_price,
                "stop_loss": sl,
                "take_profit": [tp] if tp is not None else [],
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
        source="execution-kraken-perps",
        ref=intent.get("intent_id") if intent else None,
        notes=json.dumps(notes_blob),
    )


__all__ = [
    "intent_from_direct_args",
    "load_intent_file",
    "portfolio_asset_symbol",
    "render_confirmation",
    "render_intent_summary",
    "write_fill_to_portfolio",
]
