"""KrakenExecutionProvider — places orders via the ``kraken`` CLI shellout.

Mirrors ``analysis/providers/kraken.py:Provider`` (data-side) in shape and
idiom: subprocess calls to the ``kraken`` CLI, JSON parsing, defensive timeouts,
no in-process API state. The CLI is the source of truth for auth, signing,
rate limiting, and venue-specific quirks — re-implementing those in Python
would be a maintenance hazard and would also block shipping until the
provider has been audited.

Venue surface used:
    kraken order buy <PAIR> <VOLUME> --type <TYPE> --price X [--cl-ord-id ID]
        [--validate] [--timeinforce GTC|IOC|GTD] [--deadline RFC3339]
        [--price2 X2] [--trigger last|index]
    kraken order sell <PAIR> <VOLUME> ...   (mirror of buy)
    kraken order cancel <TXID|...> [--yes]
    kraken balance                           (cash balances)
    kraken open-orders                       (open orders)
    kraken query-orders <TXID>               (poll after submit)

Safety model
------------

There is no paper mode by design — fills always hit the venue. Two guard
rails apply:

  1. ``place_order(intent, wait=True, ...)`` — when the caller does not pass
     ``wait=False``, the method blocks until the order reaches a terminal
     status (filled / partial / rejected / cancelled / expired) or the
     timeout elapses. This gives the LLM a populated fill price before
     portfolio-mgmt writes the row.

  2. The CLI skill ``skills/execution-kraken/scripts/run.py`` prompts for
     confirmation before invoking ``place_order`` unless ``--yes`` is passed.
     This is the human-in-the-loop gate — never bypassed. The LLM is the
     agent brain; it calls this skill only after the user has said yes.
     ``--dry-run`` calls the CLI with ``--validate`` and skips submit.

Idempotency
-----------

``Intent.intent_id`` is passed through as ``--cl-ord-id`` on every submit.
Kraken enforces uniqueness per ``cl-ord-id`` per API key — a retried intent
with the same ``cl-ord-id`` returns the original order instead of placing a
duplicate. The provider does not currently inspect the response for the
"already exists" path; the caller is expected to handle that.
"""

import json
import logging
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

from analysis.providers.execution.base import (
    FillConfirmation,
    Intent,
    register_execution_provider,
)

logger = logging.getLogger(__name__)

# Kraken CLI's --type enum. Mapping Intent.order_type (provider-neutral)
# -> Kraken's --type string is identity except for the kebab-case names.
_KRAKEN_ORDER_TYPES = {
    "market",
    "limit",
    "stop-loss",
    "take-profit",
    "stop-loss-limit",
    "take-profit-limit",
    "trailing-stop",
    "trailing-stop-limit",
    "iceberg",
    "settle-position",
}

# Kraken asset code -> canonical code. Subset that's actually used in the
# portfolio-mgmt asset notation. Extend as needed; unknown codes pass through.
_KRAKEN_ASSET_MAP = {
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "XLTC": "LTC",
    "XXRP": "XRP",
    "XXLM": "XLM",
    "ZCAD": "CAD",
    "ZAUD": "AUD",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_asset(code: str) -> str:
    return _KRAKEN_ASSET_MAP.get(code, code)


def _to_kraken_pair(pair: str) -> str:
    """Normalise pair notation to what ``kraken`` CLI accepts.

    The CLI uses bare pair names like ``XBTUSD`` / ``BTCUSD`` / ``HYPEUSD``
    (no dash, no slash). The provider accepts any of the common notations
    and strips separators.
    """
    return pair.replace("-", "").replace("/", "").upper()


def _run_kraken(args: list[str], timeout: float = 30.0) -> dict:
    """Run ``kraken <args> -o json`` and return the parsed JSON envelope.

    Raises ``RuntimeError`` on non-zero exit, timeout, missing CLI, or
    malformed JSON. Callers should let those propagate — both ``place_order``
    and the CLI wrapper convert them into a ``FillConfirmation`` with
    ``status="error"`` so the LLM gets a uniform shape.
    """
    cmd = ["kraken", *args, "-o", "json"]
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


class KrakenExecutionProvider:
    """Execution provider for Kraken spot. See module docstring."""

    name = "kraken"

    def supports(self, pair: str, venue: str | None = None) -> bool:
        if venue is not None and venue != "kraken":
            return False
        try:
            data = _run_kraken(["pairs", "--pair", _to_kraken_pair(pair)], timeout=10)
        except RuntimeError:
            return False
        if isinstance(data, dict) and "error" in data:
            return False
        return True

    def place_order(  # noqa: PLR0915
        self,
        intent: Intent,
        *,
        wait: bool = True,
        timeout_s: float = 5.0,
    ) -> FillConfirmation:
        """Submit an order to Kraken via the CLI.

        ``wait=True`` (default) blocks until the order reaches a terminal
        status, polled via ``kraken query-orders``. ``wait=False`` returns
        immediately with ``status="submitted"`` after the CLI returns.

        The fill poll uses a short interval (~500ms) up to ``timeout_s``
        total. Kraken market orders typically fill within 1-2 seconds;
        limit orders may sit on the book for hours and are best submitted
        with ``wait=False`` so the LLM can hand off to the watchdog.
        """
        order_type = intent["order_type"]
        if order_type not in _KRAKEN_ORDER_TYPES:
            return self._error_confirmation(
                intent,
                f"order_type {order_type!r} not supported by Kraken CLI",
            )

        cmd = [
            "order",
            intent["side"],
            _to_kraken_pair(intent["pair"]),
            str(intent["volume"]),
            "--type",
            order_type,
        ]
        if order_type != "market":
            lp = intent.get("limit_price")
            if lp is None:
                return self._error_confirmation(intent, "limit_price required for non-market order")
            cmd += ["--price", str(lp)]
        if intent.get("stop_price") is not None:
            cmd += ["--price2", str(intent["stop_price"])]
        if intent.get("time_in_force"):
            cmd += ["--timeinforce", intent["time_in_force"]]
        if intent.get("deadline"):
            cmd += ["--deadline", intent["deadline"]]
        if intent.get("intent_id"):
            cmd += ["--cl-ord-id", intent["intent_id"]]

        # Provider-specific extras pass-through (e.g. --leverage, --oflags).
        extras = intent.get("extras") or {}
        for k, v in extras.items():
            cmd += [f"--{k.replace('_', '-')}", str(v)]

        try:
            submit_resp = _run_kraken(cmd, timeout=30)
        except RuntimeError as e:
            return self._error_confirmation(intent, str(e))

        if isinstance(submit_resp, dict) and "error" in submit_resp:
            errs = submit_resp["error"]
            return self._error_confirmation(intent, f"Kraken rejected order: {errs}", raw=submit_resp)

        # Kraken success envelope: {"txid": ["..."], "descr": {"order": "...", "close": "..."}}
        txids = []
        if isinstance(submit_resp, dict):
            txids = submit_resp.get("txid") or []
        order_id = txids[0] if txids else ""

        if not order_id:
            return self._error_confirmation(intent, "Kraken returned no txid", raw=submit_resp)

        if not wait:
            return FillConfirmation(
                intent_id=intent["intent_id"],
                order_id=order_id,
                cl_ord_id=intent.get("intent_id"),
                pair=intent["pair"],
                side=intent["side"],
                order_type=order_type,
                requested_volume=float(intent["volume"]),
                filled_volume=0.0,
                fill_price=None,
                cost_quote=None,
                fee=0.0,
                fee_currency="",
                status="submitted",
                timestamp=_now_iso(),
                venue="kraken",
                raw=submit_resp,
            )

        return self._poll_fill(intent, order_id, submit_resp, timeout_s)

    def _poll_fill(
        self,
        intent: Intent,
        order_id: str,
        submit_resp: dict,
        timeout_s: float,
    ) -> FillConfirmation:
        """Poll ``kraken query-orders`` until terminal state or timeout.

        Maps Kraken order status codes:
            "filled"  -> status="filled"
            "partial" -> status="partial"
            "open" / "pending" -> continues polling
            "canceled" / "expired" / "rejected" -> terminal, no fill
        """
        deadline = time.monotonic() + timeout_s
        poll_interval = 0.5
        last_resp: dict = submit_resp

        while time.monotonic() < deadline:
            try:
                resp = _run_kraken(["query-orders", order_id, "--trades"], timeout=10)
            except RuntimeError as e:
                logger.warning("query-orders poll failed: %s", e)
                time.sleep(poll_interval)
                continue

            last_resp = resp if isinstance(resp, dict) else {}
            order = self._extract_order(last_resp, order_id)
            if order is None:
                time.sleep(poll_interval)
                continue

            kraken_status = (order.get("status") or "").lower()
            if kraken_status in ("open", "pending", "new"):
                time.sleep(poll_interval)
                continue

            return self._confirmation_from_query(intent, order_id, order, submit_resp, last_resp)

        # Timeout — return submitted state with whatever fill info we have
        return FillConfirmation(
            intent_id=intent["intent_id"],
            order_id=order_id,
            cl_ord_id=intent.get("intent_id"),
            pair=intent["pair"],
            side=intent["side"],
            order_type=intent["order_type"],
            requested_volume=float(intent["volume"]),
            filled_volume=0.0,
            fill_price=None,
            cost_quote=None,
            fee=0.0,
            fee_currency="",
            status="open",
            reason=f"timeout after {timeout_s}s waiting for fill",
            timestamp=_now_iso(),
            venue="kraken",
            raw={"submit": submit_resp, "last_poll": last_resp},
        )

    def _extract_order(self, query_resp: dict, order_id: str) -> dict | None:
        if not isinstance(query_resp, dict):
            return None
        return query_resp.get(order_id)

    def _confirmation_from_query(
        self,
        intent: Intent,
        order_id: str,
        order: dict,
        submit_resp: dict,
        query_resp: dict,
    ) -> FillConfirmation:
        """Build a FillConfirmation from a kraken ``query-orders`` response."""
        kraken_status = (order.get("status") or "").lower()
        vol_exec = float(order.get("vol_exec", 0) or 0)
        cost = order.get("cost")
        fee = order.get("fee")
        price = order.get("price")
        avg_price = float(price) if price not in (None, "0", 0) else None
        fill_price = avg_price
        cost_quote = float(cost) if cost not in (None, "0", 0) else None

        if kraken_status == "filled":
            status = "filled"
        elif kraken_status == "partial":
            status = "partial"
        elif kraken_status in ("canceled", "cancelled"):
            status = "cancelled"
        elif kraken_status == "expired":
            status = "expired"
        elif kraken_status == "rejected":
            status = "rejected"
        else:
            status = kraken_status or "unknown"

        # If vol_exec > 0 but status was canceled, treat as partial.
        if vol_exec > 0 and status == "cancelled":
            status = "partial"

        fee_currency_raw = order.get("fee_currency") or ""
        return FillConfirmation(
            intent_id=intent["intent_id"],
            order_id=order_id,
            cl_ord_id=intent.get("intent_id"),
            pair=intent["pair"],
            side=intent["side"],
            order_type=intent["order_type"],
            requested_volume=float(intent["volume"]),
            filled_volume=vol_exec,
            fill_price=fill_price,
            cost_quote=cost_quote,
            fee=float(fee) if fee not in (None, "0", 0) else 0.0,
            fee_currency=_canonical_asset(fee_currency_raw) if fee_currency_raw else "",
            status=status,
            reason=order.get("reason") or order.get("descr", {}).get("order", "")
            if isinstance(order.get("descr"), dict)
            else order.get("reason", ""),
            timestamp=_now_iso(),
            venue="kraken",
            raw={"submit": submit_resp, "query": query_resp},
        )

    def _error_confirmation(
        self,
        intent: Intent,
        reason: str,
        raw: dict | None = None,
    ) -> FillConfirmation:
        return FillConfirmation(
            intent_id=intent.get("intent_id", ""),
            order_id="",
            cl_ord_id=intent.get("intent_id"),
            pair=intent.get("pair", ""),
            side=intent.get("side", ""),
            order_type=intent.get("order_type", ""),
            requested_volume=float(intent.get("volume") or 0),
            filled_volume=0.0,
            fill_price=None,
            cost_quote=None,
            fee=0.0,
            fee_currency="",
            status="error",
            reason=reason,
            timestamp=_now_iso(),
            venue="kraken",
            raw=raw or {},
        )

    def get_balance(self) -> dict[str, float]:
        data = _run_kraken(["balance"], timeout=15)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected balance response shape: {type(data).__name__}")
        if "error" in data:
            raise RuntimeError(f"Kraken balance error: {data['error']}")
        return {_canonical_asset(k): float(v) for k, v in data.items()}

    def get_open_orders(self) -> list[dict[str, Any]]:
        data = _run_kraken(["open-orders", "--trades"], timeout=15)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected open-orders response shape: {type(data).__name__}")
        if "error" in data:
            raise RuntimeError(f"Kraken open-orders error: {data['error']}")
        # Kraken envelope: {"open": {<txid>: <order>}}
        open_orders = data.get("open") or {}
        result: list[dict[str, Any]] = []
        for txid, order in open_orders.items():
            if not isinstance(order, dict):
                continue
            descr = order.get("descr") if isinstance(order.get("descr"), dict) else {}
            result.append(
                {
                    "order_id": txid,
                    "pair": descr.get("pair", ""),
                    "side": descr.get("type", ""),  # "buy" / "sell"
                    "order_type": descr.get("ordertype", ""),
                    "volume": float(order.get("vol", 0) or 0),
                    "filled_volume": float(order.get("vol_exec", 0) or 0),
                    "limit_price": float(descr.get("price", 0) or 0) or None,
                    "cl_ord_id": descr.get("cl_ord_id"),
                    "raw": order,
                }
            )
        return result

    def cancel_order(self, order_id: str) -> bool:
        try:
            resp = _run_kraken(["order", "cancel", order_id, "--yes"], timeout=15)
        except RuntimeError as e:
            logger.warning("cancel_order(%s) failed: %s", order_id, e)
            return False
        if not isinstance(resp, dict):
            return False
        if "error" in resp:
            logger.info("cancel_order(%s) error response: %s", order_id, resp["error"])
            return False
        # Kraken success envelope: {"count": N, "pending": bool}
        return int(resp.get("count", 0) or 0) > 0


# Self-register on import so callers can resolve via get_execution_provider("kraken").
register_execution_provider(KrakenExecutionProvider())


__all__ = ["KrakenExecutionProvider"]
