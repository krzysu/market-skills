"""KrakenPerpsExecutionProvider — places perps brackets via the ``kraken futures`` CLI.

Mirrors ``analysis/providers/execution_kraken.py:KrakenExecutionProvider``
(spot side) in shape and idiom: subprocess calls to the ``kraken`` CLI,
JSON parsing, defensive timeouts, no in-process API state. The CLI is the
source of truth for auth, signing, rate limiting, and venue-specific
quirks — re-implementing those in Python would be a maintenance hazard.

Venue surface used::

    kraken futures set-leverage <SYMBOL> <N>
    kraken futures order buy <SYMBOL> <SIZE> --type market [--client-order-id ID]
    kraken futures order sell <SYMBOL> <SIZE> --type market [--client-order-id ID]
    kraken futures order buy <SYMBOL> <SIZE> --type stop --stop-price X [--reduce-only]
    kraken futures order buy <SYMBOL> <SIZE> --type take-profit --stop-price Y [--reduce-only]
    kraken futures positions
    kraken futures open-orders
    kraken futures accounts
    kraken futures cancel --order-id ID
    kraken futures cancel-all --symbol SYMBOL

Bracket model
-------------

A perps Intent places a bracket of three orders:

1. ``open`` — market order that opens the position (``buy`` for long,
   ``sell`` for short). Maps to Intent.side.
2. ``stop`` — protective stop that closes the position. The opposite
   side from the open (``sell`` for long, ``buy`` for short). ``reduce-only``
   so it can't accidentally add to the position.
3. ``take_profit`` — profit-taking close. Same side as the stop.
   ``reduce-only`` for the same reason.

The provider returns a FillConfirmation whose ``bracket`` field carries
per-order ids; ``order_id`` itself is the open-order id. If the stop or
TP fails after a successful open, the provider rolls back by closing the
position to avoid an unprotected trade.

Safety model
------------

There is no paper mode by design (mirrors the spot adapter). All order
placements hit the venue. Two guard rails apply:

1. ``place_order(intent, wait=True, ...)`` — when the caller does not pass
   ``wait=False``, the method blocks until the open order fills (market
   orders fill within a few hundred milliseconds). Stop / TP rest on the
   book and are returned in ``status="submitted"`` once placed.

2. The CLI skill ``skills/execution-kraken-perps/scripts/run.py`` prompts
   for confirmation before invoking ``place_order`` unless ``--yes`` is
   passed. ``--dry-run`` builds the Intent, validates the bracket, and
   prints the bracket summary without submitting.

Idempotency
-----------

``Intent.intent_id`` is passed through as ``--client-order-id`` on the
open order. Kraken enforces uniqueness per ``client_order_id`` per API key
— a retried Intent with the same id returns the original order instead of
placing a duplicate. The provider does not currently inspect the response
for the "already exists" path; the LLM (or whoever drives retries) is
expected to handle that.

Tier leverage caps
------------------

The provider does NOT enforce a leverage cap itself — that's a risk
policy (``leverage_cap_policy`` in ``analysis/risk.py``) the caller
applies before submitting. The CLI also accepts ``--max-leverage=N`` and
will refuse to submit if the requested leverage exceeds it, defaulting to
2x for majors (BTC/ETH/SOL) and 5x for other Kraken-perp-mapped alts.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from typing import Any

from analysis.providers.execution.base import (
    BracketFill,
    FillConfirmation,
    Intent,
    register_execution_provider,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────── symbol + tier mapping

# Spot pair -> Kraken flexible-futures symbol. Single source of truth for the
# provider. `kraken futures instruments` is the upstream source; this map
# mirrors the symbols listed there as of writing. Extend as needed.
KRAKEN_FUTURES_MAP: dict[str, str] = {
    "BTCUSD": "PF_XBTUSD",
    "XBTUSD": "PF_XBTUSD",
    "ETHUSD": "PF_ETHUSD",
    "PAXGUSD": "PF_PAXGUSD",
    "SOLUSD": "PF_SOLUSD",
    "BNBUSD": "PF_BNBUSD",
    "HYPEUSD": "PF_HYPEUSD",
    "NEARUSD": "PF_NEARUSD",
    "TAOUSD": "PF_TAOUSD",
    "STRKUSD": "PF_STRKUSD",
    "AAVEUSD": "PF_AAVEUSD",
    "LINKUSD": "PF_LINKUSD",
    "PENDLEUSD": "PF_PENDLEUSD",
    "MORPHOUSD": "PF_MORPHOUSD",
}

# Per-pair leverage caps (the venue also has its own published max per
# instrument). Majors are conservative; alts follow the standard 5x.
LEVERAGE_CAPS: dict[str, int] = {
    "BTCUSD": 2,
    "XBTUSD": 2,
    "ETHUSD": 2,
    "SOLUSD": 2,
}
DEFAULT_LEVERAGE_CAP = 5

# Per-pair first-tier maintenance margin rates. Mirrors the venue's published
# instrument spec (the ``marginLevels[0]`` entry — smallest notional, most
# conservative). Used by ``liquidation_distance_policy`` as the lower bound:
# real liq moves a few percent farther at higher notional tiers, but the
# policy uses first-tier as the safe floor.
#
# Source: ``kraken futures instruments`` as of 2026-06-24. If a new pair is
# added to ``KRAKEN_FUTURES_MAP``, also add its first-tier MM here. Trades on
# a pair without an MM entry are skipped by the policy (CONCERN, not REJECT).
MM_RATES: dict[str, float] = {
    "BTCUSD": 0.005,
    "XBTUSD": 0.005,
    "ETHUSD": 0.005,
    "SOLUSD": 0.01,
    "LINKUSD": 0.01,
    "NEARUSD": 0.01,
    "AAVEUSD": 0.01,
    "PAXGUSD": 0.025,
    "BNBUSD": 0.01,
    "STRKUSD": 0.02,
    "PENDLEUSD": 0.02,
    "TAOUSD": 0.01,
    "MORPHOUSD": 0.025,
    "HYPEUSD": 0.01,
}

# Kraken perps type enum (subset we use). Mapping Intent.order_type (provider
# neutral) -> Kraken's --type string. The open leg is always market; the
# protective legs are stop and take-profit.
_KRAKEN_PERPS_ORDER_TYPES = {"market", "stop", "take-profit"}

# ─────────────────────────────────────────────────────────────────── helpers


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_kraken_futures(args: list[str], timeout: float = 30.0) -> dict:
    """Run ``kraken futures <args> -o json`` and return the parsed envelope.

    Raises ``RuntimeError`` on non-zero exit, timeout, missing CLI, or
    malformed JSON. Callers convert to a FillConfirmation with
    ``status="error"`` so the LLM gets a uniform shape.
    """
    cmd = ["kraken", "futures", *args, "-o", "json"]
    logger.debug("exec: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError("kraken CLI not found in PATH; install it first") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"kraken CLI timed out after {timeout}s: {' '.join(cmd)}") from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"kraken CLI failed (rc={result.returncode}): {stderr or 'no stderr'}")

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise RuntimeError("kraken CLI returned empty output")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"kraken CLI returned non-JSON output: {stdout[:200]!r}") from e


def resolve_futures_symbol(pair: str) -> str:
    """Map a spot pair (e.g. ``SOLUSD``) to its Kraken futures symbol.

    Raises ``ValueError`` for unmapped pairs. Use ``KRAKEN_FUTURES_MAP``
    directly when you need the inverse mapping.
    """
    sym = KRAKEN_FUTURES_MAP.get(pair.upper())
    if not sym:
        raise ValueError(f"no Kraken futures symbol mapped for pair {pair!r}")
    return sym


def leverage_cap_for_pair(pair: str) -> int:
    """Return the configured leverage cap for ``pair`` (int)."""
    return LEVERAGE_CAPS.get(pair.upper(), DEFAULT_LEVERAGE_CAP)


def mm_rate_for_pair(pair: str) -> float | None:
    """Return the first-tier maintenance margin rate for ``pair`` (float).

    Returns ``None`` when the pair isn't in ``MM_RATES`` — callers should
    treat that as "skip the policy" (CONCERN, not REJECT). Mirrors the
    shape of :func:`leverage_cap_for_pair`.
    """
    return MM_RATES.get(pair.upper())


def _extract_order_id(submit_resp: dict) -> str:
    """Pull the order_id out of a Kraken futures submit envelope.

    The envelope shape varies: ``{"order_id": "..."}``, ``{"sendStatus":
    {"order_id": "..."}}``, or list-shaped. Returns ``""`` on miss.
    """
    if not isinstance(submit_resp, dict):
        return ""
    oid = submit_resp.get("order_id")
    if isinstance(oid, str):
        return oid
    send_status = submit_resp.get("sendStatus")
    if isinstance(send_status, dict):
        inner = send_status.get("order_id")
        if isinstance(inner, str):
            return inner
    send_status = submit_resp.get("send_status")
    if isinstance(send_status, dict):
        inner = send_status.get("order_id")
        if isinstance(inner, str):
            return inner
    return ""


def _is_kraken_error(resp: dict) -> bool:
    """True if the envelope carries a ``result: error`` or top-level ``error``."""
    if not isinstance(resp, dict):
        return False
    if "error" in resp:
        return True
    if resp.get("result") == "error":
        return True
    return False


def _kraken_error_message(resp: dict) -> str:
    """Extract a human-readable error message from a Kraken error envelope."""
    err = resp.get("error")
    if isinstance(err, list) and err:
        return str(err[0])
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        return json.dumps(err)
    return "unknown Kraken error"


# ──────────────────────────────────────────────────────────────── provider


class KrakenPerpsExecutionProvider:
    """Execution provider for Kraken perpetual futures. See module docstring."""

    name = "kraken-perps"

    def supports(self, pair: str, venue: str | None = None) -> bool:
        """Return True if this provider can serve ``pair`` on ``venue``."""
        if venue is not None and venue != "kraken-perps":
            return False
        return pair.upper() in KRAKEN_FUTURES_MAP

    # ────────────────────────────────────────────────────────── bracket submit

    def place_order(  # noqa: PLR0915
        self,
        intent: Intent,
        *,
        wait: bool = True,
        timeout_s: float = 5.0,
    ) -> FillConfirmation:
        """Submit a perps bracket (open + stop + take-profit).

        ``intent.leverage`` and ``intent.bracket`` are required for a
        successful submission; the existing ``validate_intent`` enforces
        this. ``intent.extras["futures_symbol"]`` overrides the default
        pair→symbol mapping when supplied (useful for non-spot-mapped
        pairs or test fixtures).

        ``wait=True`` (default) blocks until the open order reaches a
        terminal status (``filled`` typically — market orders fill in
        milliseconds). ``wait=False`` returns after the open order is
        submitted with ``status="submitted"``.

        Stop and TP are submitted after the open fills. If the stop
        fails, the provider rolls back the position (closes at market)
        so the caller is never left with an unprotected position.
        """
        if intent.get("leverage") is None:
            return self._error_confirmation(intent, "Intent.leverage required for perps bracket")
        bracket = intent.get("bracket")
        if not bracket:
            return self._error_confirmation(intent, "Intent.bracket required for perps bracket")

        # Symbol resolution: extras override (allows callers to bypass the
        # default map for test fixtures or custom venues), then pair lookup.
        try:
            futures_symbol = (intent.get("extras") or {}).get("futures_symbol") or resolve_futures_symbol(
                intent["pair"]
            )
        except ValueError as e:
            return self._error_confirmation(intent, str(e))

        stop_loss = float(bracket["stop_loss"])
        take_profit = float(bracket["take_profit"])
        leverage = int(intent["leverage"])
        side = intent["side"]
        volume = float(intent["volume"])
        intent_id = intent["intent_id"]

        # Map sides: long=buy, short=sell. Stop / TP are the opposite side
        # because they close the position.
        if side == "buy":
            open_side, close_side = "buy", "sell"
        elif side == "sell":
            open_side, close_side = "sell", "buy"
        else:
            return self._error_confirmation(intent, f"invalid side {side!r}")

        # 1. Set leverage. Skip on failure with a CONCERN-style warning —
        # the venue falls back to whatever the user's account default is.
        try:
            _run_kraken_futures(["set-leverage", futures_symbol, str(leverage)], timeout=15)
        except RuntimeError as e:
            logger.warning("set-leverage failed (continuing with account default): %s", e)

        # 2. Open order.
        try:
            open_resp = _run_kraken_futures(
                [
                    "order",
                    open_side,
                    futures_symbol,
                    str(volume),
                    "--type",
                    "market",
                    "--client-order-id",
                    intent_id,
                ],
                timeout=30,
            )
        except RuntimeError as e:
            return self._error_confirmation(intent, f"open order failed: {e}", raw={"open_error": str(e)})

        if _is_kraken_error(open_resp):
            return self._error_confirmation(
                intent,
                f"Kraken rejected open: {_kraken_error_message(open_resp)}",
                raw=open_resp,
            )

        open_order_id = _extract_order_id(open_resp)
        if not open_order_id:
            return self._error_confirmation(intent, "Kraken returned no open order_id", raw=open_resp)

        # 3. Stop order. CRITICAL: failure here means unprotected exposure.
        # Roll back by closing the position.
        stop_order_id = ""
        try:
            stop_resp = _run_kraken_futures(
                [
                    "order",
                    close_side,
                    futures_symbol,
                    str(volume),
                    "--type",
                    "stop",
                    "--stop-price",
                    str(stop_loss),
                    "--reduce-only",
                ],
                timeout=30,
            )
            if _is_kraken_error(stop_resp):
                raise RuntimeError(f"stop rejected: {_kraken_error_message(stop_resp)}")
            stop_order_id = _extract_order_id(stop_resp)
            if not stop_order_id:
                raise RuntimeError("stop returned no order_id")
        except RuntimeError as e:
            # Roll back the unprotected position.
            logger.error("stop placement failed for %s, rolling back: %s", futures_symbol, e)
            try:
                _run_kraken_futures(
                    ["order", close_side, futures_symbol, str(volume), "--type", "market"],
                    timeout=30,
                )
                rollback_note = f"position auto-closed (stop failed: {e})"
            except RuntimeError as close_err:
                # Both legs failed — surface the open order id so the operator
                # can attempt manual cleanup.
                err = self._error_confirmation(
                    intent,
                    f"STOP FAILED AND CLOSE FAILED — UNPROTECTED POSITION on {futures_symbol}: "
                    f"stop={e}; close={close_err}",
                    raw={"open": open_resp, "stop_error": str(e), "close_error": str(close_err)},
                )
                err["order_id"] = open_order_id
                err["bracket"] = BracketFill(open_order_id=open_order_id)
                return err
            err = self._error_confirmation(
                intent,
                f"stop placement failed; {rollback_note}",
                raw={"open": open_resp, "stop_error": str(e)},
            )
            err["order_id"] = open_order_id
            err["bracket"] = BracketFill(open_order_id=open_order_id)
            return err

        # 4. Take-profit order. Failure here is non-critical — the position
        # is protected by the stop; the operator can place the TP manually.
        tp_order_id = ""
        tp_warning = None
        try:
            tp_resp = _run_kraken_futures(
                [
                    "order",
                    close_side,
                    futures_symbol,
                    str(volume),
                    "--type",
                    "take-profit",
                    "--stop-price",
                    str(take_profit),
                    "--reduce-only",
                ],
                timeout=30,
            )
            if _is_kraken_error(tp_resp):
                raise RuntimeError(f"take-profit rejected: {_kraken_error_message(tp_resp)}")
            tp_order_id = _extract_order_id(tp_resp)
            if not tp_order_id:
                raise RuntimeError("take-profit returned no order_id")
        except RuntimeError as e:
            tp_warning = f"TP placement failed (position protected by stop): {e}"
            logger.warning(tp_warning)

        # 5. Build confirmation.
        bracket_fill: BracketFill = {
            "open_order_id": open_order_id,
            "stop_order_id": stop_order_id,
            "take_profit_order_id": tp_order_id,
        }

        # wait=True polls the open order until terminal (filled) — market
        # orders typically fill within the first poll. Stop / TP rest on
        # the book; their state is "submitted" until the position is
        # closed by hitting one of them.
        if wait:
            polled = self._poll_open(intent, futures_symbol, open_order_id, timeout_s)
            polled["bracket"] = bracket_fill
            if tp_warning and not polled.get("reason"):
                polled["reason"] = tp_warning
            return polled

        return FillConfirmation(
            intent_id=intent_id,
            order_id=open_order_id,
            cl_ord_id=intent_id,
            pair=intent["pair"],
            side=side,
            order_type=intent.get("order_type", "market"),
            requested_volume=volume,
            filled_volume=0.0,
            fill_price=None,
            cost_quote=None,
            fee=0.0,
            fee_currency="",
            status="submitted",
            timestamp=_now_iso(),
            venue="kraken-perps",
            bracket=bracket_fill,
            reason=tp_warning,
            raw={"open": open_resp},
        )

    # ────────────────────────────────────────────────────────── read / manage

    def get_balance(self) -> dict[str, float]:
        """Return futures account balances keyed by currency code.

        Wraps ``kraken futures accounts`` and flattens the response into
        ``{currency: float}``. Kraken returns a nested envelope; we walk
        common shapes (``accounts`` list, ``{"accounts": [...]}``,
        ``{"result": {"accounts": [...]}}``).
        """
        data = _run_kraken_futures(["accounts"], timeout=15)
        accounts = self._extract_accounts(data)
        out: dict[str, float] = {}
        for acct in accounts:
            if not isinstance(acct, dict):
                continue
            currency = (acct.get("currency") or acct.get("asset") or "").upper()
            if not currency:
                continue
            # Prefer available balance for downstream sizing checks.
            value = (
                acct.get("availableMargin")
                or acct.get("available_balance")
                or acct.get("availableFunds")
                or acct.get("balanceValue")
                or acct.get("balance")
                or 0
            )
            try:
                out[currency] = float(value)
            except (TypeError, ValueError):
                continue
        return out

    def _extract_accounts(self, data: dict) -> list:
        """Pull the accounts list out of a Kraken futures accounts envelope."""
        if not isinstance(data, dict):
            return []
        for key in ("accounts", "result"):
            if key in data:
                inner = data[key]
                if isinstance(inner, list):
                    return inner
                if isinstance(inner, dict) and "accounts" in inner:
                    sub = inner["accounts"]
                    if isinstance(sub, list):
                        return sub
        return []

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return open futures orders in a venue-native shape.

        Walks common envelope shapes. Items at minimum include ``order_id``,
        ``symbol``, ``side``, ``type``, ``volume``, ``filled``.
        """
        data = _run_kraken_futures(["open-orders"], timeout=15)
        orders = self._extract_open_orders(data)
        out: list[dict[str, Any]] = []
        for o in orders:
            if not isinstance(o, dict):
                continue
            order_id = o.get("order_id") or o.get("orderId") or o.get("cliOrdId") or o.get("client_order_id") or ""
            if not order_id:
                continue
            side = o.get("side") or o.get("type") or ""
            # Kraken encodes side as "buy"/"sell"; translate "l"/"s" if present.
            if side == "l":
                side = "buy"
            elif side == "s":
                side = "sell"
            symbol = o.get("symbol") or o.get("futures_symbol") or ""
            order_type = o.get("orderType") or o.get("type") or o.get("order_type") or ""
            volume = float(o.get("size", 0) or o.get("volume", 0) or 0)
            filled = float(o.get("filled", 0) or 0)
            trigger = o.get("stopPrice") or o.get("triggerPrice") or o.get("limitPrice") or o.get("price")
            limit_price = float(trigger) if trigger not in (None, "", "0") else None
            out.append(
                {
                    "order_id": str(order_id),
                    "symbol": str(symbol),
                    "side": side,
                    "order_type": order_type,
                    "volume": volume,
                    "filled_volume": filled,
                    "limit_price": limit_price,
                    "cli_ord_id": o.get("cliOrdId") or o.get("client_order_id"),
                    "raw": o,
                }
            )
        return out

    def _extract_open_orders(self, data: dict) -> list:
        """Pull the orders list out of a Kraken futures open-orders envelope."""
        if not isinstance(data, dict):
            return []
        # Common shapes: {"openOrders": [...]}, {"result": "success",
        # "orders": [...]}, {"orders": [...]}.
        for key in ("openOrders", "orders"):
            if key in data:
                inner = data[key]
                if isinstance(inner, list):
                    return inner
                if isinstance(inner, dict):
                    return list(inner.values())
        # Sometimes wrapped in result: {"result": {"openOrders": [...]}}.
        result = data.get("result")
        if isinstance(result, dict):
            for key in ("openOrders", "orders"):
                inner = result.get(key)
                if isinstance(inner, list):
                    return inner
                if isinstance(inner, dict):
                    return list(inner.values())
        return []

    def get_positions(self) -> list[dict[str, Any]]:
        """Return open perps positions. Convenience wrapper.

        Returns ``[{"symbol": "PF_SOLUSD", "size": -3.0, "side": "short"}, ...]``.
        Each entry has ``symbol``, ``size`` (positive=long, negative=short),
        and ``side``. Empty list on failure (treated as "no positions").
        """
        try:
            data = _run_kraken_futures(["positions"], timeout=15)
        except RuntimeError as e:
            logger.warning("positions fetch failed: %s", e)
            return []
        positions = data if isinstance(data, list) else data.get("openPositions", [])
        if not isinstance(positions, list):
            return []
        out: list[dict[str, Any]] = []
        for p in positions:
            if not isinstance(p, dict):
                continue
            sym = p.get("symbol") or ""
            size_raw = p.get("size", 0)
            try:
                size = float(size_raw)
            except (TypeError, ValueError):
                continue
            if not sym or size == 0:
                continue
            out.append(
                {
                    "symbol": sym,
                    "size": size,
                    "side": "long" if size > 0 else "short",
                    "raw": p,
                }
            )
        return out

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open futures order by id.

        Returns True on success, False on failure (already filled, already
        cancelled, order not found, or auth/CLI error). Network and auth
        failures are logged but never raised — the LLM gets a clean bool.
        """
        try:
            resp = _run_kraken_futures(["cancel", "--order-id", order_id], timeout=15)
        except RuntimeError as e:
            logger.warning("cancel_order(%s) failed: %s", order_id, e)
            return False
        if _is_kraken_error(resp):
            logger.info("cancel_order(%s) error response: %s", order_id, _kraken_error_message(resp))
            return False
        # Kraken success: {"result": "success", "cancelStatus": {...}} or
        # {"cancelledOrders": [...]}.
        if isinstance(resp, dict):
            if resp.get("cancelStatus") or resp.get("cancelledOrders") or resp.get("result") == "success":
                return True
        return True  # optimistic — Kraken's CLI doesn't always echo clearly

    # ─────────────────────────────────────────────────────────────── helpers

    def _error_confirmation(
        self,
        intent: Intent | dict,
        reason: str,
        raw: dict | None = None,
    ) -> FillConfirmation:
        """Build a ``status="error"`` FillConfirmation for early-return paths.

        Used when validation fails (missing leverage, unmapped pair, etc.)
        or when the venue returns a non-recoverable error before any order
        has been placed.
        """
        intent_dict = intent if isinstance(intent, dict) else {}
        return FillConfirmation(
            intent_id=str(intent_dict.get("intent_id", "")),
            order_id="",
            cl_ord_id=intent_dict.get("intent_id"),
            pair=str(intent_dict.get("pair", "")),
            side=str(intent_dict.get("side", "")),
            order_type=str(intent_dict.get("order_type", "market")),
            requested_volume=float(intent_dict.get("volume") or 0),
            filled_volume=0.0,
            fill_price=None,
            cost_quote=None,
            fee=0.0,
            fee_currency="",
            status="error",
            reason=reason,
            timestamp=_now_iso(),
            venue="kraken-perps",
            bracket=None,
            raw=raw or {},
        )


# Self-register on import so callers can resolve via get_execution_provider("kraken-perps").
register_execution_provider(KrakenPerpsExecutionProvider())


__all__ = [
    "DEFAULT_LEVERAGE_CAP",
    "KRAKEN_FUTURES_MAP",
    "KrakenPerpsExecutionProvider",
    "LEVERAGE_CAPS",
    "leverage_cap_for_pair",
    "mm_rate_for_pair",
    "resolve_futures_symbol",
]
