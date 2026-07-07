"""Cross-asset valuation fetcher: SP500 Shiller CAPE z-score.

Single-asset CAPE/Shiller fair-value framework — the only quantitative
valuation model with ~150 years of academic backing. BTC/oil/DXY
regression models ship nowhere in the repo: their out-of-sample R² on
rolling fits is too low to trust, and a placeholder signal that the
LLM agent brain would treat as authoritative is worse than no signal.

Sources:
  - multpl.com — current Shiller CAPE (single HTML scrape, meta-tag parse).
  - yfinance ``^GSPC`` — SP500 spot (fallback for stale missing meta).

Design contract mirrors :mod:`analysis.macro`:
  - Best-effort + error-isolated: a single source failure never
    crashes the call. The source's input lands as ``None`` and the
    failure is recorded in ``errors``.
  - Stateless across processes except for an in-process TTL cache
    (default 3600s — slower-moving than price) and a ring-buffer at
    ``$XDG_DATA_HOME/market-skills/valuation_history.json`` (capped at
    200 entries, mirroring macro_history).
  - Narrate-only by design (ADR-0002). The CAPE z-score is for the
    LLM agent brain to read; downstream consumers (strategy-mean-
    reversion's ``veto_reasons`` tag) attach a soft tag and let the
    LLM decide.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import threading
import time
from typing import Any

import requests
import yfinance as yf

# --- Constants --------------------------------------------------------------

_MULTPL_URL = "https://www.multpl.com/shiller-pe"
_MULTPL_TIMEOUT_S = 10

_YF_SP500 = "^GSPC"

_LABEL_MULTPL = "multpl"
_LABEL_SP500 = "sp500_yf"

# Hardcoded 50y stats from Shiller's published dataset (1881-present,
# trimmed to the post-1976 window so the constants reflect the regime
# the LLM is actually trading in). Spot-check against the latest Yale
# monthly CSV before bumping. ``cape_zscore`` uses (cape - mean) / std
# verbatim — these two numbers are the single source of truth.
_CAPE_50Y_MEAN = 21.0
_CAPE_50Y_STD = 9.0

# Regime bands on cape_zscore. |z| >= 2 is the historical "worst
# decile" territory; L3 ``veto_reasons`` tags fire when the score
# crosses this band.
_REGIME_BANDS: tuple[tuple[float, str], ...] = (
    (2.0, "OVEREXTENDED"),
    (1.0, "ELEVATED"),
    (-1.0, "FAIR"),
    (-2.0, "DEPRESSED"),
)
# Anything below -2 falls through to OVERSOLD.

DEFAULT_TTL_SECONDS = 3600
_HISTORY_CAP = 200
_HISTORY_FILENAME = "valuation_history.json"


# --- Source fetchers --------------------------------------------------------


def _fetch_shiller_cape(timeout_s: int = _MULTPL_TIMEOUT_S) -> tuple[float | None, str | None]:
    """Scrape the current Shiller CAPE from multpl.com's meta-description.

    The number is in a single meta tag (``<meta name="description"
    content="Current Shiller PE Ratio is 41.97, a change of ..."/>``),
    which is a more stable parse target than the JS-rendered chart
    on the page. ``41.97`` in the example would yield ``cape=41.97``.

    Returns ``(cape, error)`` — at most one populated.
    """
    try:
        r = requests.get(
            _MULTPL_URL,
            timeout=timeout_s,
            headers={"User-Agent": "market-skills/0.1"},
        )
    except requests.RequestException as e:
        return None, f"{_LABEL_MULTPL}: {type(e).__name__}"
    if r.status_code != 200:
        return None, f"{_LABEL_MULTPL}: http {r.status_code}"
    m = re.search(r"Current Shiller PE Ratio is (\d+\.\d+)", r.text)
    if not m:
        return None, f"{_LABEL_MULTPL}: parse miss"
    try:
        cape = float(m.group(1))
    except ValueError:
        return None, f"{_LABEL_MULTPL}: non-numeric cape {m.group(1)!r}"
    if math.isnan(cape) or cape <= 0 or cape > 100:
        return None, f"{_LABEL_MULTPL}: implausible cape {cape}"
    return cape, None


def _fetch_sp500_spot() -> tuple[float | None, str | None]:
    """Fetch the latest SP500 spot from yfinance ``^GSPC`` fast_info.

    Mirrors :func:`analysis.macro._fetch_yf_price`. Used to label the
    CAPE reading with a contemporaneous price so the LLM can quote a
    concrete number when narrating ("CAPE 41.97 at SPX 5,400").
    """
    try:
        info = yf.Ticker(_YF_SP500).fast_info
    except Exception as e:
        return None, f"{_LABEL_SP500}: {type(e).__name__}"
    raw = getattr(info, "last_price", None)
    if raw is None:
        return None, f"{_LABEL_SP500}: no data"
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None, f"{_LABEL_SP500}: non-numeric price {raw!r}"
    if math.isnan(price) or price <= 0:
        return None, f"{_LABEL_SP500}: invalid price {price!r}"
    return price, None


# --- Classification ---------------------------------------------------------


def _classify_regime(zscore: float | None) -> str:
    """Map a CAPE z-score to a coarse valuation regime label.

    Mirrors ``market-macro``'s 5-bucket style. Returns ``"UNKNOWN"``
    when the z-score is missing — by design, never an
    aggressive label. Downstream consumers should treat UNKNOWN as
    "no view" (similar to macro's degraded-risk-appetite pattern).
    """
    if zscore is None or math.isnan(zscore):
        return "UNKNOWN"
    if zscore >= _REGIME_BANDS[0][0]:
        return "OVEREXTENDED"
    if zscore >= _REGIME_BANDS[1][0]:
        return "ELEVATED"
    if zscore >= _REGIME_BANDS[2][0]:
        return "FAIR"
    if zscore >= _REGIME_BANDS[3][0]:
        return "DEPRESSED"
    return "OVERSOLD"


def _format_regime_note(regime: str, inputs: dict[str, float | None], zscore: float | None) -> str:
    """One-line summary for the LLM agent brain.

    Format is intentionally short — the morning brief pipes this
    through verbatim. Includes the raw CAPE + z-score so the LLM can
    quote either number without re-reading ``inputs``.
    """
    cape = inputs.get("cape")
    if cape is None:
        return "Valuation: CAPE unavailable."
    z_str = f"{zscore:+.2f}σ" if zscore is not None and not math.isnan(zscore) else "z-score n/a"
    return f"Valuation: CAPE {cape:.1f} ({z_str}) — {regime.lower()}."


# --- TTL cache (in-process) -------------------------------------------------

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str, ttl_seconds: float) -> dict[str, Any] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.monotonic() - ts > ttl_seconds:
            return None
        return val


def _cache_put(key: str, val: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), val)


def clear_cache() -> None:
    """Reset the in-process TTL cache. Used by tests; not part of the public surface."""
    with _cache_lock:
        _cache.clear()


# --- Public fetcher ---------------------------------------------------------


def fetch_valuation(
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    write_history: bool = True,
) -> dict[str, Any]:
    """Fetch the current SP500 valuation signal.

    Args:
        ttl_seconds: in-process cache lifetime. ``0`` disables the cache.
            Default 3600s — CAPE updates monthly so a longer TTL than
            :func:`analysis.macro.fetch_regime` (300s) is appropriate.
        write_history: when True, append this signal to the ring buffer at
            ``$XDG_DATA_HOME/market-skills/valuation_history.json``.
            Cache hits do not write; only the call that actually
            performed the fetch does.

    Returns:
        A ValuationSignal-shaped dict with the same error-handling contract
        as :class:`~analysis.contracts.RegimeSignal`. See SKILL.md for the
        full schema.
    """
    cache_key = "valuation"
    if ttl_seconds > 0:
        cached = _cache_get(cache_key, ttl_seconds)
        if cached is not None:
            if write_history:
                _safe_append_tick(cached)
            return cached

    cape, cape_err = _fetch_shiller_cape()
    sp500, sp500_err = _fetch_sp500_spot()

    errors: list[str] = []
    for e in (cape_err, sp500_err):
        if e:
            errors.append(e)

    zscore: float | None = None
    if cape is not None:
        zscore = round((cape - _CAPE_50Y_MEAN) / _CAPE_50Y_STD, 3)

    regime = _classify_regime(zscore)
    incomplete = bool(errors)
    # Mirror macro: a degraded signal downgrades the headline label so
    # naive consumers never see "OVEREXTENDED" when CAPE was actually
    # missing. L3 veto_reasons hooks (strategy-mean-reversion) treat
    # UNKNOWN as "no view" and do not tag the idea.
    if incomplete and regime != "UNKNOWN":
        regime = "UNKNOWN"

    inputs_payload: dict[str, Any] = {
        "sp500": sp500,
        "cape": cape,
        "cape_mean_50y": _CAPE_50Y_MEAN,
        "cape_std_50y": _CAPE_50Y_STD,
    }
    regime_payload: dict[str, Any] = {
        "cape_zscore": zscore,
        "regime": regime,
    }
    note = _format_regime_note(regime, inputs_payload, zscore)
    if incomplete and not note.startswith("[VALUATION INCOMPLETE"):
        note = f"[VALUATION INCOMPLETE — {len(errors)} input(s) missing] " + note

    signal: dict[str, Any] = {
        "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
        "inputs": inputs_payload,
        "regime": regime_payload,
        "errors": errors,
        "incomplete": incomplete,
        "regime_note": note,
    }

    if ttl_seconds > 0:
        _cache_put(cache_key, signal)
    if write_history:
        _safe_append_tick(signal)
    return signal


# --- History store ----------------------------------------------------------


def default_history_path() -> str:
    """Default ring-buffer path.

    Resolves to ``$XDG_DATA_HOME/market-skills/valuation_history.json``.
    Raises :class:`OSError` when ``XDG_DATA_HOME`` is unset — the
    library deliberately does not paper over with a host-specific
    fallback (see AGENTS.md "What to avoid"). Callers may pass
    ``path=`` to ``load_history`` / ``append_tick`` to use an explicit
    location.
    """
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        raise OSError(
            "XDG_DATA_HOME is not set; cannot resolve the valuation history "
            "path. Set XDG_DATA_HOME or pass path= explicitly to "
            "load_history / append_tick."
        )
    return os.path.join(base, "market-skills", _HISTORY_FILENAME)


def _resolve_path(path: str | os.PathLike | None) -> str:
    if path is None:
        return default_history_path()
    return os.fspath(path)


def load_history(path: str | os.PathLike | None = None) -> list[dict]:
    """Read the rolling valuation history. Returns [] on missing/malformed."""
    resolved = _resolve_path(path)
    if not os.path.exists(resolved):
        return []
    try:
        with open(resolved) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return data


def append_valuation_tick(
    signal: dict[str, Any],
    *,
    path: str | os.PathLike | None = None,
) -> int:
    """Append a ValuationSignal-shaped dict to the ring buffer.

    Returns 1 on success, 0 on error (the caller — a cron tick — must
    not crash on a bad write). Mirrors :func:`analysis.macro.append_macro_tick`.
    """
    if not isinstance(signal, dict):
        return 0
    try:
        resolved = _resolve_path(path)
        history = load_history(resolved)
    except OSError:
        return 0

    inputs = signal.get("inputs") or {}
    regime = signal.get("regime") or {}
    entry = {
        "ts": signal.get("timestamp") or _dt.datetime.now(_dt.UTC).isoformat(),
        "sp500": inputs.get("sp500"),
        "cape": inputs.get("cape"),
        "cape_zscore": regime.get("cape_zscore"),
        "regime": regime.get("regime"),
        "regime_note": signal.get("regime_note"),
    }
    history.append(entry)
    if len(history) > _HISTORY_CAP:
        history = history[-_HISTORY_CAP:]
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w") as f:
            json.dump(history, f, indent=2)
    except OSError:
        return 0
    return 1


def _safe_append_tick(signal: dict[str, Any]) -> None:
    """Best-effort append used by fetch_valuation. Never raises."""
    try:
        append_valuation_tick(signal)
    except Exception:  # noqa: BLE001 — history write must not crash fetcher
        return
