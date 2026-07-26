"""Read-only perps state fetchers.

Pulls open positions, current funding rate, and instrument maintenance
margin for the kraken-perps flow. Used by ``risk-engine.build_context``
to populate the perps-only fields on ``RiskContext`` (``open_perps_positions``,
``funding_rate_per_8h``, ``maintenance_margin_rate``).

Why a separate module
---------------------

The :mod:`analysis.providers.execution.kraken_perps` module is the
execution provider — it places orders. This module is read-only state.
Splitting them keeps the provider's surface focused on execution and
lets the risk layer depend on a thin data-only API.

Auth handling
-------------

``kraken futures positions`` is auth-required. When the CLI returns the
``{"error": "auth", ...}`` envelope, the fetchers return ``None`` /
empty rather than raise. The risk-engine treats that as "perps state
unavailable" and the perps policies degrade to their no-info path
(APPROVED with reason "no objection" for most; CONCERN for the policies
that need a specific value).
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from analysis.providers.execution.kraken_perps import (
    KRAKEN_FUTURES_MAP,
    mm_rate_for_pair,
    resolve_futures_symbol,
)

logger = logging.getLogger(__name__)


def _run_kraken_futures(args: list[str], timeout: float = 30.0) -> dict | None:
    """Run ``kraken futures <args> -o json`` and return the parsed envelope.

    Returns ``None`` when:
      - the CLI exits with an ``{"error": "auth", ...}`` envelope (auth not
        configured — caller treats as "perps state unavailable")
      - the CLI is missing from PATH (caller treats as "perps state
        unavailable")
      - the response is empty or non-JSON

    Raises ``RuntimeError`` for other errors (timeout, non-zero exit with
    a non-auth error envelope, malformed JSON with no error envelope).
    """
    cmd = ["kraken", "futures", *args, "-o", "json"]
    logger.debug("exec: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        logger.warning("kraken CLI not found in PATH; perps state unavailable")
        return None
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"kraken CLI timed out after {timeout}s: {' '.join(cmd)}") from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # Parse stdout even on non-zero exit — Kraken returns auth errors
        # on stdout with rc=0 sometimes, but the spot case is non-zero.
        stdout = (result.stdout or "").strip()
        if stdout:
            try:
                env = json.loads(stdout)
                if isinstance(env, dict) and env.get("error") == "auth":
                    logger.warning("kraken futures auth not configured")
                    return None
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"kraken CLI failed (rc={result.returncode}): {stderr or 'no stderr'}")

    stdout = (result.stdout or "").strip()
    if not stdout:
        return None
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"kraken CLI returned non-JSON output: {stdout[:200]!r}") from e

    if isinstance(env, dict) and env.get("error") == "auth":
        logger.warning("kraken futures auth not configured")
        return None
    return env


def get_open_positions() -> list[dict[str, Any]] | None:
    """Return open perps positions from ``kraken futures positions``.

    Returns a list of ``{symbol: str, size: float}`` dicts (positive=long,
    negative=short), or ``None`` when the call fails (auth missing, CLI
    not in PATH, etc.). ``ctx.open_perps_positions`` accepts ``None`` and
    the duplicate-position policy treats it as "no info".
    """
    env = _run_kraken_futures(["positions"])
    if env is None:
        return None
    if not isinstance(env, dict):
        return None

    # The envelope shape is ``{"positions": [...]}`` where each entry has
    # at minimum ``symbol`` and ``size`` (signed: + for long, - for short).
    # Some instrument entries have ``size: 0`` (closed); filter those out.
    raw_positions = env.get("positions")
    if not isinstance(raw_positions, list):
        # Some versions of the CLI return a flat dict keyed by symbol.
        if isinstance(raw_positions, dict):
            raw_positions = [{"symbol": sym, **data} for sym, data in raw_positions.items() if isinstance(data, dict)]
        else:
            return []

    out: list[dict[str, Any]] = []
    for p in raw_positions:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol") or p.get("instrument") or ""
        if not sym:
            continue
        try:
            size = float(p.get("size", 0) or 0)
        except (TypeError, ValueError):
            continue
        if size == 0:
            continue
        out.append({"symbol": str(sym), "size": size})
    return out


def get_funding_rate(pair: str, side: str) -> float | None:
    """Return the current 8h funding rate for ``pair``, signed for ``side``.

    Sign convention: positive = this trade pays funding, negative = this
    trade receives funding. Longs pay when funding > 0, shorts pay when
    funding < 0. The CLI's raw rate is "longs-pay" convention; this
    function flips the sign for shorts so the returned value matches
    :func:`analysis.risk.perps.funding_drag_policy`'s contract.

    Returns ``None`` when:
      - the call fails (auth missing, CLI not in PATH, etc.)
      - the pair isn't mapped to a Kraken futures symbol
      - the response has no recent rate entry
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    try:
        symbol = resolve_futures_symbol(pair)
    except ValueError:
        logger.warning("no Kraken futures symbol for pair %r; funding rate unavailable", pair)
        return None

    env = _run_kraken_futures(["historical-funding-rates", symbol])
    if env is None:
        return None
    if not isinstance(env, dict):
        return None

    rates = env.get("rates")
    if not isinstance(rates, list) or not rates:
        return None

    last = rates[-1]
    if not isinstance(last, dict):
        return None
    raw = last.get("fundingRate")
    if raw is None:
        return None
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return None

    # CLI convention: positive = longs pay shorts. Flip for shorts so the
    # returned value is "this trade pays" for whichever side was requested.
    return rate if side == "buy" else -rate


def get_mm_rate(pair: str) -> float | None:
    """Return the first-tier maintenance margin rate for ``pair``.

    Wrapper around :func:`analysis.providers.execution.kraken_perps.mm_rate_for_pair`
    exposed here so the risk-engine imports a single module for perps
    state. Returns ``None`` when the pair isn't in the MM table — the
    liquidation-distance policy treats that as "skip".
    """
    return mm_rate_for_pair(pair)


# Re-export for callers that want the inverse mapping without touching
# the provider module.
__all__ = [
    "KRAKEN_FUTURES_MAP",
    "get_funding_rate",
    "get_mm_rate",
    "get_open_positions",
]
