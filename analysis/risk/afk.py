"""AFK asymmetric hard gates.

Three rules that don't depend on LLM judgment — applies BEFORE the
risk-layer advisory and the venue submit. A REJECT verdict aborts the
order before any network call.

Gates:

  1. Position cap. Never more than ``AFK_MAX_POSITION_PCT`` of the
     portfolio on a single execution, regardless of what risk-engine
     says. Default 5% — tighter than the typical risk-engine cap so a
     runaway AI can't blow past the advisory layer.

  2. Sleep window. Refuse intents submitted between
     ``[AFK_SLEEP_WINDOW_START, AFK_SLEEP_WINDOW_END)`` UTC. Default
     ``[02:00, 06:00)`` — thin markets, weird fills, low liquidity. The
     window is configurable but symmetric-feeling hours only — start
     must be < end.

  3. Circuit breaker. Two consecutive CONCERN or REJECT verdicts from
     risk-engine on the same pair = no execution on that pair until the
     user manually resets the state file. Same shape as a tripped
     electrical breaker — once it trips, the human has to flip it back.

Configuration via environment variables (read once per call):

  - ``AFK_MAX_POSITION_PCT`` (float, default 5.0)
  - ``AFK_SLEEP_WINDOW_START_HOUR_UTC`` (int, default 2)
  - ``AFK_SLEEP_WINDOW_END_HOUR_UTC`` (int, default 6)
  - ``AFK_CIRCUIT_BREAKER_THRESHOLD`` (int, default 2)

State persistence:

  Circuit breaker state lives at
  ``$XDG_DATA_HOME/market-skills/circuit-breaker.json``. The map is
  ``{pair: {"consecutive_concerns": int, "tripped_at": iso8601}}``.
  Resets are explicit: ``reset_circuit_breaker(pair, path=...)`` clears
  one pair, ``reset_circuit_breaker(path=None)`` clears all.

  When ``XDG_DATA_HOME`` is unset, the gate treats state as empty
  (``consecutive_concerns == 0`` for every pair) — fail-open to match
  the rest of the analyzer's behaviour (no silent fallback paths).

Wiring: ``skills/execution-kraken-spot`` and
``skills/execution-kraken-perps`` call :func:`vet_afk` BEFORE
``provider.place_order``. REJECT verdicts print a clear message and
return exit code 2. APPROVED / CONCERN proceed (CONCERN surfaces to the
user via the existing ``risk-engine`` verdict the LLM already narrated —
the AFK layer doesn't second-guess CONCERN).

Status taxonomy mirrors ``analysis.contracts.RiskVerdictFragment``:

  - ``APPROVED`` — gate has no objection; proceed.
  - ``CONCERN`` — gate flags but does not block (currently unused; the
    AFK layer is intentionally binary — it blocks or it doesn't — but
    the status field is reserved for future advisory gates).
  - ``REJECT`` — gate hard-stops the order.

This module is independent of risk-engine: vet_afk takes the same
``Intent`` and a slim ``AFKContext`` (total_value + base_ccy), plus the
circuit-breaker state. It does NOT depend on RiskContext — keeping the
AFK layer deterministic and offline.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass, field
from typing import Any, TypedDict

from analysis.providers.execution.base import Intent

ENV_MAX_POSITION_PCT = "AFK_MAX_POSITION_PCT"
ENV_SLEEP_START_HOUR = "AFK_SLEEP_WINDOW_START_HOUR_UTC"
ENV_SLEEP_END_HOUR = "AFK_SLEEP_WINDOW_END_HOUR_UTC"
ENV_CIRCUIT_THRESHOLD = "AFK_CIRCUIT_BREAKER_THRESHOLD"

DEFAULT_MAX_POSITION_PCT = 5.0
DEFAULT_SLEEP_START_HOUR = 2  # 02:00 UTC
DEFAULT_SLEEP_END_HOUR = 6  # 06:00 UTC (exclusive)
DEFAULT_CIRCUIT_THRESHOLD = 2

CIRCUIT_FILENAME = "circuit-breaker.json"


class AFKVerdict(TypedDict):
    gate: str  # "position_cap" | "sleep_window" | "circuit_breaker" | "passed"
    status: str  # "APPROVED" | "CONCERN" | "REJECT"
    reason: str
    detail: dict[str, Any]


@dataclass
class AFKContext:
    """Slim context for AFK gates — only what's needed.

    ``total_value`` is in ``base_ccy``. The position_cap gate sizes
    ``notional = volume * limit_price`` and computes ``pct =
    notional / total_value * 100``. ``total_value <= 0`` skips the
    position_cap gate (mirrors risk-engine's behaviour on missing
    portfolio data — fail-open is intentional for an advisory layer,
    but the AFK gate is supposed to be a hard cap; we accept the
    inconsistency and document it).
    """

    total_value: float = 0.0
    base_ccy: str = "USD"


def _now_hour_utc(now: _dt.datetime | None = None) -> int:
    return (now or _dt.datetime.now(_dt.UTC)).hour


def _approve(gate: str, detail: dict[str, Any] | None = None) -> AFKVerdict:
    return AFKVerdict(gate=gate, status="APPROVED", reason="no objection", detail=detail or {})


def check_position_cap(
    intent: Intent,
    ctx: AFKContext,
    *,
    max_pct: float = DEFAULT_MAX_POSITION_PCT,
) -> AFKVerdict:
    """REJECT when the intended notional exceeds ``max_pct`` of the portfolio.

    Buy: ``notional = volume * limit_price`` (intent.limit_price is
    always required for limit orders; market orders skip the cap with
    APPROVED — matches the risk-engine behaviour).

    Sell: ``notional = volume * current_price held for the asset``.
    Without a held position we can't size the sell as a position, so
    we treat it as APPROVED with a no-info note. The execution layer
    will independently fail (insufficient funds) if the sell is wrong.
    """
    gate = "position_cap"
    if intent["side"] == "buy":
        lp = intent.get("limit_price")
        if lp is None or lp <= 0:
            return _approve(gate, {"reason": "no limit_price on buy — cap not enforceable"})
        notional = float(intent["volume"]) * float(lp)
    else:
        # Sells are risk-reducing; the cap is implicitly satisfied unless
        # we have held data. Without it, skip with no objection.
        return _approve(gate, {"side": "sell", "note": "no held position; cap skipped"})

    if ctx.total_value <= 0 or notional <= 0:
        return _approve(gate, {"reason": "portfolio total_value<=0 — cap not enforceable"})

    pct = (notional / ctx.total_value) * 100
    if pct > max_pct:
        return AFKVerdict(
            gate=gate,
            status="REJECT",
            reason=(
                f"intended notional {pct:.1f}% of portfolio exceeds AFK "
                f"cap {max_pct:.1f}% — reduce size or break into smaller orders"
            ),
            detail={
                "notional": notional,
                "pct": round(pct, 4),
                "max_pct": max_pct,
                "base_ccy": ctx.base_ccy,
            },
        )
    return _approve(gate, {"pct": round(pct, 4), "max_pct": max_pct})


def check_sleep_window(
    *,
    start_hour: int = DEFAULT_SLEEP_START_HOUR,
    end_hour: int = DEFAULT_SLEEP_END_HOUR,
    now: _dt.datetime | None = None,
) -> AFKVerdict:
    """REJECT when the submission time falls inside ``[start_hour, end_hour)`` UTC.

    Defaults ``[02:00, 06:00)``. Configurable via env so a user with a
    different timezone of concern can shift the window. The user-reset
    burden is intentional — AFK means "I'm not watching, don't let
    weird fills surprise me".
    """
    gate = "sleep_window"
    if start_hour == end_hour or start_hour < 0 or end_hour > 24:
        # Misconfigured → skip with note. Caller logs.
        return _approve(
            gate,
            {"reason": "sleep window misconfigured; gate skipped", "start": start_hour, "end": end_hour},
        )
    hour = _now_hour_utc(now)
    in_window = start_hour <= hour < end_hour if start_hour < end_hour else (hour >= start_hour or hour < end_hour)
    if in_window:
        return AFKVerdict(
            gate=gate,
            status="REJECT",
            reason=(
                f"submission at {hour:02d}:xx UTC falls inside AFK sleep "
                f"window [{start_hour:02d}:00, {end_hour:02d}:00) — thin "
                f"liquidity, hold until post-window"
            ),
            detail={"current_hour_utc": hour, "start_hour": start_hour, "end_hour": end_hour},
        )
    return _approve(gate, {"current_hour_utc": hour})


@dataclass
class CircuitBreakerState:
    """Per-pair counter of consecutive CONCERN/REJECT verdicts from risk-engine.

    The state file is the single source of truth across processes. The
    in-memory cache (``store``) is the read-side optimisation; the
    on-disk file is the write-side that survives restarts.
    """

    threshold: int = DEFAULT_CIRCUIT_THRESHOLD
    path: str | None = None  # explicit override
    store: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Map: pair -> {"consecutive_concerns": int, "tripped_at": iso8601|None}.

    Construction reads the file once when ``path`` is given. Otherwise
    reads lazily on the first access via :func:`load_state`.
    """

    @classmethod
    def load(cls, path: str | None = None, *, threshold: int | None = None) -> CircuitBreakerState:
        resolved = path or default_circuit_breaker_path()
        store: dict[str, dict[str, Any]] = {}
        if resolved is not None and os.path.exists(resolved):
            try:
                with open(resolved) as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, dict):
                store = raw
        return cls(
            threshold=threshold
            if threshold is not None
            else _env_int(ENV_CIRCUIT_THRESHOLD, DEFAULT_CIRCUIT_THRESHOLD),
            path=resolved,
            store=store,
        )

    def get(self, pair: str) -> dict[str, Any]:
        bare = _normalize_pair(pair)
        return self.store.get(bare) or {"consecutive_concerns": 0, "tripped_at": None}

    def record(self, pair: str, *, reset: bool = False) -> None:
        """Increment or reset the per-pair counter and persist."""
        bare = _normalize_pair(pair)
        if reset:
            self.store.pop(bare, None)
        else:
            existing = self.store.get(bare) or {"consecutive_concerns": 0, "tripped_at": None}
            existing["consecutive_concerns"] = int(existing.get("consecutive_concerns", 0)) + 1
            existing["tripped_at"] = (
                _dt.datetime.now(_dt.UTC).isoformat()
                if existing["consecutive_concerns"] >= self.threshold
                else existing.get("tripped_at")
            )
            self.store[bare] = existing
        self.persist()

    def is_tripped(self, pair: str) -> bool:
        bare = _normalize_pair(pair)
        entry = self.store.get(bare) or {}
        return int(entry.get("consecutive_concerns", 0)) >= self.threshold

    def persist(self) -> None:
        if self.path is None:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.store, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            return


def default_circuit_breaker_path() -> str | None:
    """Resolve the canonical path; ``None`` when XDG_DATA_HOME is unset."""
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        return None
    return os.path.join(base, "market-skills", CIRCUIT_FILENAME)


def reset_circuit_breaker(pair: str | None = None, *, path: str | None = None) -> None:
    """Clear the breaker state for ``pair`` (or all pairs when ``None``).

    Called by the user manually after addressing whatever tripped the
    breaker (typically a real risk-engine REJECT on the pair).
    """
    state = CircuitBreakerState.load(path=path)
    if pair is None:
        state.store = {}
    else:
        state.store.pop(_normalize_pair(pair), None)
    state.persist()


def check_circuit_breaker(
    intent: Intent,
    state: CircuitBreakerState,
) -> AFKVerdict:
    """REJECT when the per-pair circuit-breaker has tripped."""
    gate = "circuit_breaker"
    if state.is_tripped(intent["pair"]):
        entry = state.get(intent["pair"])
        tripped_at = entry.get("tripped_at") or "?"
        return AFKVerdict(
            gate=gate,
            status="REJECT",
            reason=(
                f"circuit breaker tripped on {intent['pair']} after "
                f"{entry.get('consecutive_concerns', state.threshold)} "
                f"consecutive concerns (last tripped at {tripped_at}) — "
                f"manual reset required via reset_circuit_breaker()"
            ),
            detail={
                "pair": intent["pair"],
                "consecutive_concerns": entry.get("consecutive_concerns"),
                "threshold": state.threshold,
                "tripped_at": tripped_at,
            },
        )
    pair = intent["pair"]
    concerns = int(state.get(pair).get("consecutive_concerns", 0))
    return _approve(gate, {"pair": pair, "consecutive_concerns": concerns})


def vet_afk(
    intent: Intent,
    ctx: AFKContext,
    state: CircuitBreakerState,
    *,
    max_pct: float | None = None,
    sleep_start_hour: int | None = None,
    sleep_end_hour: int | None = None,
    now: _dt.datetime | None = None,
) -> AFKVerdict:
    """Run the three AFK gates in order; first REJECT short-circuits.

    Pure function over its inputs (``now`` is the only side channel and
    it's injectable for deterministic tests). Defaults to environment
    variables for the per-gate thresholds; explicit args win.
    """
    cap_pct = max_pct if max_pct is not None else _env_float(ENV_MAX_POSITION_PCT, DEFAULT_MAX_POSITION_PCT)
    sw_start = (
        sleep_start_hour if sleep_start_hour is not None else _env_int(ENV_SLEEP_START_HOUR, DEFAULT_SLEEP_START_HOUR)
    )
    sw_end = sleep_end_hour if sleep_end_hour is not None else _env_int(ENV_SLEEP_END_HOUR, DEFAULT_SLEEP_END_HOUR)

    cap = check_position_cap(intent, ctx, max_pct=cap_pct)
    if cap["status"] == "REJECT":
        return cap

    sleep = check_sleep_window(start_hour=sw_start, end_hour=sw_end, now=now)
    if sleep["status"] == "REJECT":
        return sleep

    breaker = check_circuit_breaker(intent, state)
    if breaker["status"] == "REJECT":
        return breaker

    return AFKVerdict(
        gate="passed",
        status="APPROVED",
        reason="all AFK gates cleared",
        detail={"max_pct": cap_pct, "sleep_window": [sw_start, sw_end], "threshold": state.threshold},
    )


# ────────────────────────────────────────────────────────────── helpers


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _normalize_pair(pair: str) -> str:
    return pair.replace("-", "").replace("/", "").upper()


__all__ = [
    "AFKContext",
    "AFKVerdict",
    "CircuitBreakerState",
    "check_circuit_breaker",
    "check_position_cap",
    "check_sleep_window",
    "default_circuit_breaker_path",
    "reset_circuit_breaker",
    "vet_afk",
]
