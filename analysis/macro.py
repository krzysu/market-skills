"""Cross-asset macro regime fetcher + classifier.

Fetches six inputs from three sources, derives a three-axis regime
label, and returns a :class:`RegimeSignal` per
``ARCHITECTURE.md 'Macro domain'``.

Sources:
  - F&G         — Alternative.me public API (``requests``).
  - VIX / DXY / US10Y / BTC market cap — yfinance ``fast_info``.
  - Total crypto market cap, BTC dominance (derived) — CoinGecko
    public ``/global`` endpoint.

Design contract:
  - Best-effort + error-isolated: a single source failure never
    crashes the call. The source's input lands as ``None`` and the
    failure is recorded in ``RegimeSignal.errors``.
  - Stateless across processes except for an in-process TTL cache
    (default 300s) and an optional ring-buffer file at
    ``$XDG_DATA_HOME/market-skills/macro_history.json`` (capped at
    200 entries — mirrors the l3_idea_history pattern).
  - Narrate-only: the regime is for the LLM agent brain to read, not
    a conviction modifier. ``analysis.macro`` exposes no
    ``conviction_modifier()`` / ``directional_filter()`` helpers.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import threading
import time
from typing import Any

import requests
import yfinance as yf

# --- Constants ----------------------------------------------------------------

# Alternative.me Fear & Greed Index — free, no key, returns latest value.
_FNG_URL = "https://api.alternative.me/fng/"
_FNG_TIMEOUT_S = 5

# CoinGecko /global — free, no key, ~10-30 req/min on the public tier.
_COINGECKO_URL = "https://api.coingecko.com/api/v3/global"
_COINGECKO_TIMEOUT_S = 5
_COINGECKO_UA = "market-skills/0.1 (https://github.com/krzysu/market-skills)"

# yfinance tickers
_YF_VIX = "^VIX"
_YF_DXY = "DX-Y.NYB"
_YF_US10Y = "^TNX"
_YF_BTC_MCAP = "BTC-USD"

# Source labels used in errors[] / history.
_LABEL_FNG = "fng"
_LABEL_VIX = "vix"
_LABEL_DXY = "dxy"
_LABEL_US10Y = "us10y"
_LABEL_BTC_MCAP = "btc_mcap"
_LABEL_COINGECKO = "coingecko"

# Default TTL on the in-process cache.
DEFAULT_TTL_SECONDS = 300

# History ring-buffer cap (mirrors analysis.chop).
_HISTORY_CAP = 200
_HISTORY_FILENAME = "macro_history.json"


# --- Source fetchers (private) -----------------------------------------------


def _fetch_fng(timeout_s: int = _FNG_TIMEOUT_S) -> tuple[float | None, str | None, str | None]:
    """Fetch the latest F&G value + label from Alternative.me.

    Returns ``(value, label, error)`` — at most one of value/label is
    populated; on failure, ``value=None``, ``label=None``, and
    ``error`` is a short human-readable string suitable for
    ``RegimeSignal.errors``.
    """
    try:
        r = requests.get(_FNG_URL, params={"limit": 1}, timeout=timeout_s)
    except requests.RequestException as e:
        return None, None, f"{_LABEL_FNG}: {type(e).__name__}"
    if r.status_code != 200:
        return None, None, f"{_LABEL_FNG}: http {r.status_code}"
    try:
        payload = r.json()
    except ValueError:
        return None, None, f"{_LABEL_FNG}: invalid json"
    data = (payload or {}).get("data") or []
    if not data:
        return None, None, f"{_LABEL_FNG}: empty payload"
    row = data[0]
    raw_value = row.get("value")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None, None, f"{_LABEL_FNG}: non-numeric value {raw_value!r}"
    label = row.get("value_classification")
    if not isinstance(label, str):
        label = None
    return value, label, None


def _fetch_yf_price(symbol: str, label: str) -> tuple[float | None, str | None]:
    """Fetch a single close/last value from yfinance fast_info.

    ``symbol`` is the yfinance ticker; ``label`` is the short key
    used in the returned error string.
    """
    try:
        info = yf.Ticker(symbol).fast_info
    except Exception as e:  # yfinance raises many subclasses of Exception
        return None, f"{label}: {type(e).__name__}"
    raw = getattr(info, "last_price", None)
    if raw is None:
        return None, f"{label}: no data"
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None, f"{label}: non-numeric price {raw!r}"
    if math.isnan(price):
        return None, f"{label}: nan"
    return price, None


def _fetch_yf_market_cap(symbol: str, label: str) -> tuple[float | None, str | None]:
    """Fetch a market-cap value from yfinance fast_info."""
    try:
        info = yf.Ticker(symbol).fast_info
    except Exception as e:
        return None, f"{label}: {type(e).__name__}"
    raw = getattr(info, "market_cap", None)
    if raw is None:
        return None, f"{label}: no data"
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return None, f"{label}: non-numeric mcap {raw!r}"
    if math.isnan(cap) or cap <= 0:
        return None, f"{label}: invalid mcap {cap!r}"
    return cap, None


def _fetch_coingecko(
    timeout_s: int = _COINGECKO_TIMEOUT_S,
) -> tuple[float | None, float | None, str | None]:
    """Fetch total crypto market cap + BTC.D % from CoinGecko /global.

    Returns ``(total_mcap_usd, btc_dominance_pct, error)``.

    ``btc_dominance_pct`` is CoinGecko's pre-computed BTC.D and is
    a *fallback* — the primary path in :func:`fetch_regime` derives
    BTC.D from the yfinance BTC market cap and this total. We return
    it here so the fetcher has a graceful degradation when yfinance's
    crypto market cap is unavailable (which is common).
    """
    headers = {"User-Agent": _COINGECKO_UA, "Accept": "application/json"}
    try:
        r = requests.get(_COINGECKO_URL, headers=headers, timeout=timeout_s)
    except requests.RequestException as e:
        return None, None, f"{_LABEL_COINGECKO}: {type(e).__name__}"
    if r.status_code != 200:
        return None, None, f"{_LABEL_COINGECKO}: http {r.status_code}"
    try:
        payload = r.json()
    except ValueError:
        return None, None, f"{_LABEL_COINGECKO}: invalid json"
    data = (payload or {}).get("data") or {}
    total = (data.get("total_market_cap") or {}).get("usd")
    btc_pct = (data.get("market_cap_percentage") or {}).get("btc")
    try:
        total_f = float(total) if total is not None else None
    except (TypeError, ValueError):
        total_f = None
    try:
        btc_pct_f = float(btc_pct) if btc_pct is not None else None
    except (TypeError, ValueError):
        btc_pct_f = None
    if total_f is None or total_f <= 0:
        return None, None, f"{_LABEL_COINGECKO}: missing total_market_cap.usd"
    return total_f, btc_pct_f, None


# --- Classification -----------------------------------------------------------


def _missing_inputs_from_errors(errors: list[str]) -> list[str]:
    """Extract a structured list of input names that failed to fetch.

    ``errors[]`` entries follow the pattern ``"<label>: <reason>"`` where
    ``<label>`` is the short source key (one of the ``_LABEL_*``
    constants: ``fng``, ``vix``, ``dxy``, ``us10y``, ``btc_mcap``,
    ``coingecko``). This helper extracts the labels, deduplicates, and
    returns them in the order they first appear — so an LLM agent can
    ask "which inputs failed?" without parsing the human-render
    ``regime_note`` or string-matching ``errors[]``.

    CoinGecko upstream provides both ``total_mcap_usd`` and the
    fallback ``btc_dominance`` (via ``market_cap_percentage``); when
    the call fails, both downstream inputs are reported as missing.
    """
    if not errors:
        return []
    # Map source labels to the user-facing input names from ``inputs``.
    # A single source label can map to multiple inputs when one HTTP
    # call produces several semantic fields.
    label_to_inputs: dict[str, tuple[str, ...]] = {
        _LABEL_FNG: ("fng",),
        _LABEL_VIX: ("vix",),
        _LABEL_DXY: ("dxy",),
        _LABEL_US10Y: ("us10y",),
        _LABEL_BTC_MCAP: ("btc_dominance",),
        _LABEL_COINGECKO: ("btc_dominance", "total_mcap_usd"),
    }
    seen: set[str] = set()
    out: list[str] = []
    for err in errors:
        if not isinstance(err, str) or ":" not in err:
            continue
        label = err.split(":", 1)[0].strip()
        for canonical in label_to_inputs.get(label, ()):
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append(canonical)
    return out


def _classify_risk_appetite(vix: float | None, dxy: float | None, us10y: float | None) -> str:
    """Map (vix, dxy, us10y) to a risk-appetite label.

    VIX is primary. DXY and US10Y act as a *cap* — a strong USD or
    a high 10Y yield pulls the label down to NEUTRAL even when VIX is
    calm, because liquidity-tightening regimes are risk-negative for
    crypto even with low equity vol.
    """
    if vix is None:
        vix_band = "NEUTRAL"
    elif vix < 15:
        vix_band = "RISK_ON"
    elif vix < 25:
        vix_band = "NEUTRAL"
    elif vix < 35:
        vix_band = "RISK_OFF"
    else:
        vix_band = "CRISIS"

    cap_triggered = (dxy is not None and dxy > 105) or (us10y is not None and us10y > 4.5)
    if cap_triggered and vix_band in ("RISK_ON", "NEUTRAL"):
        return "NEUTRAL"
    return vix_band


def _classify_liquidity(us10y: float | None, vix: float | None) -> str:
    """Map (us10y, vix) to a liquidity label.

    STRESS is reserved for the joint *high-yield + high-vol* regime —
    a single high 10Y in a calm market is TIGHT but not yet STRESS,
    and a single high VIX in an easy-yield market is not STRESS.
    """
    if us10y is None:
        return "TIGHTENING"
    if us10y < 3.5:
        base = "EASY"
    elif us10y < 4.5:
        base = "TIGHTENING"
    else:
        base = "TIGHT"
    if vix is not None and vix > 25 and base == "TIGHT":
        return "STRESS"
    return base


def _classify_sentiment(fng: float | None) -> str:
    """Map the F&G value to a 5-bucket sentiment label."""
    if fng is None:
        return "NEUTRAL"
    if fng < 25:
        return "EXTREME_FEAR"
    if fng < 45:
        return "FEAR"
    if fng < 55:
        return "NEUTRAL"
    if fng < 75:
        return "GREED"
    return "EXTREME_GREED"


def _format_regime_note(
    risk_appetite: str,
    liquidity: str,
    sentiment: str,
    inputs: dict[str, float | None],
) -> str:
    """One-line human summary. Read by the LLM for narration."""
    posture = {
        ("RISK_ON", "EASY"): "risk-on, easy liquidity",
        ("RISK_ON", "TIGHTENING"): "risk-on but liquidity tightening",
        ("RISK_ON", "TIGHT"): "risk-on, tight liquidity",
        ("RISK_ON", "STRESS"): "risk-on but stressed",
        ("NEUTRAL", "EASY"): "neutral, easy liquidity",
        ("NEUTRAL", "TIGHTENING"): "neutral, liquidity tightening",
        ("NEUTRAL", "TIGHT"): "neutral, tight liquidity",
        ("NEUTRAL", "STRESS"): "neutral, liquidity stress",
        ("RISK_OFF", "EASY"): "risk-off, easy liquidity",
        ("RISK_OFF", "TIGHTENING"): "risk-off, liquidity tightening",
        ("RISK_OFF", "TIGHT"): "risk-off, tight liquidity",
        ("RISK_OFF", "STRESS"): "risk-off, stressed",
        ("CRISIS", "EASY"): "crisis, easy liquidity",
        ("CRISIS", "TIGHTENING"): "crisis, liquidity tightening",
        ("CRISIS", "TIGHT"): "crisis, tight liquidity",
        ("CRISIS", "STRESS"): "crisis, stressed",
    }
    core = posture.get((risk_appetite, liquidity), f"{risk_appetite.lower()}, {liquidity.lower()}")
    sent = sentiment.lower().replace("_", " ")
    if risk_appetite in ("RISK_OFF", "CRISIS"):
        posture_hint = "defensive posture recommended"
    elif risk_appetite == "NEUTRAL":
        posture_hint = "selective entries"
    else:
        posture_hint = "constructive"
    vix = inputs.get("vix")
    fng = inputs.get("fng")
    extra = []
    if vix is not None:
        extra.append(f"VIX {vix:.1f}")
    if fng is not None:
        extra.append(f"F&G {fng:.0f}")
    extra_s = f" ({', '.join(extra)})" if extra else ""
    return f"Macro: {core}; sentiment {sent}. {posture_hint}.{extra_s}"


# --- TTL cache (in-process) --------------------------------------------------


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
    """Reset the in-process TTL cache. Used by tests; not part of the
    public surface for callers.
    """
    with _cache_lock:
        _cache.clear()


# --- Public fetcher ----------------------------------------------------------


def fetch_regime(
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    write_history: bool = True,
) -> dict[str, Any]:
    """Fetch the current cross-asset macro regime.

    Args:
        ttl_seconds: in-process cache lifetime. ``0`` disables the
            cache (every call hits the network). Default 300s.
        write_history: when True, append this signal to the ring
            buffer at ``$XDG_DATA_HOME/market-skills/macro_history.json``.
            Cache hits do not write; only the call that actually
            performed the fetch does.

    Returns:
        A :class:`RegimeSignal`-shaped dict. See
        ``analysis.contracts.RegimeSignal``.
    """
    cache_key = "regime"
    if ttl_seconds > 0:
        cached = _cache_get(cache_key, ttl_seconds)
        if cached is not None:
            if write_history:
                _safe_append_tick(cached)
            return cached

    fng_value, fng_label, fng_err = _fetch_fng()
    vix, vix_err = _fetch_yf_price(_YF_VIX, _LABEL_VIX)
    dxy, dxy_err = _fetch_yf_price(_YF_DXY, _LABEL_DXY)
    us10y, us10y_err = _fetch_yf_price(_YF_US10Y, _LABEL_US10Y)
    btc_mcap, btc_mcap_err = _fetch_yf_market_cap(_YF_BTC_MCAP, _LABEL_BTC_MCAP)
    total_mcap, cg_btc_dominance, cg_err = _fetch_coingecko()

    # Primary path: derive from yfinance BTC mcap / CoinGecko total mcap.
    # Fallback: when yfinance's BTC market cap is missing (common for
    # crypto tickers), use CoinGecko's pre-computed BTC.D from the
    # same /global call.
    # ``btc_dominance_source`` is carried into the signal so backtests
    # reading macro_history.json can distinguish a yfinance-derived
    # reading from a CoinGecko-fallback reading (matters because the
    # two pipelines measure slightly different universes).
    btc_dominance: float | None = None
    btc_dominance_source: str | None = None
    if btc_mcap is not None and total_mcap is not None and total_mcap > 0:
        btc_dominance = round(btc_mcap / total_mcap * 100, 2)
        btc_dominance_source = "yf"
    elif cg_btc_dominance is not None:
        btc_dominance = round(cg_btc_dominance, 2)
        btc_dominance_source = "coingecko"

    errors: list[str] = []
    for e in (fng_err, vix_err, dxy_err, us10y_err, btc_mcap_err, cg_err):
        if e:
            errors.append(e)

    risk_appetite = _classify_risk_appetite(vix, dxy, us10y)
    liquidity = _classify_liquidity(us10y, vix)
    sentiment = _classify_sentiment(fng_value)

    incomplete = bool(errors)
    if incomplete and risk_appetite != "UNKNOWN":
        # Downgrade the headline axis so naive consumers that read only
        # the label never see a partial regime mislabelled as RISK_ON /
        # RISK_OFF. downstream regime_consistency policy treats UNKNOWN
        # as adverse and fires CONCERN. liquidity / sentiment keep their
        # best-effort labels so the LLM can still narrate "TIGHTENING /
        # EXTREME_FEAR" even when the headline axis is degraded.
        risk_appetite = "UNKNOWN"

    inputs_payload: dict[str, Any] = {
        "fng": fng_value,
        "fng_label": fng_label,
        "vix": vix,
        "dxy": dxy,
        "us10y": us10y,
        "btc_dominance": btc_dominance,
        "btc_dominance_source": btc_dominance_source,
        "total_mcap_usd": total_mcap,
    }
    regime_payload: dict[str, str] = {
        "risk_appetite": risk_appetite,
        "liquidity": liquidity,
        "sentiment": sentiment,
    }
    note = _format_regime_note(risk_appetite, liquidity, sentiment, inputs_payload)
    if incomplete and not note.startswith("[REGIME INCOMPLETE"):
        note = f"[REGIME INCOMPLETE — {len(errors)} input(s) missing] " + note

    # Structured per-input missing map. Lets LLM agents ask
    # "is BTC mcap missing?" without parsing the errors[] strings or the
    # regime_note prefix. Populated from errors[] labels (each carries
    # the input name as its first token, e.g. "fng: timeout").
    missing_inputs = _missing_inputs_from_errors(errors)

    signal: dict[str, Any] = {
        "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
        "inputs": inputs_payload,
        "regime": regime_payload,
        "errors": errors,
        "incomplete": incomplete,
        "missing_inputs": missing_inputs,
        "regime_note": note,
    }

    if ttl_seconds > 0:
        _cache_put(cache_key, signal)
    if write_history:
        _safe_append_tick(signal)
    return signal


# --- History store ------------------------------------------------------------


def default_history_path() -> str:
    """Default ring-buffer path.

    Resolves to ``$XDG_DATA_HOME/market-skills/macro_history.json``.
    Raises :class:`OSError` when ``XDG_DATA_HOME`` is unset — the
    library deliberately does not paper over with a host-specific
    fallback (see AGENTS.md "What to avoid"). Callers may pass
    ``path=`` to ``load_history`` / ``append_tick`` to use an
    explicit location.
    """
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        raise OSError(
            "XDG_DATA_HOME is not set; cannot resolve the macro history "
            "path. Set XDG_DATA_HOME or pass path= explicitly to "
            "load_history / append_tick."
        )
    return os.path.join(base, "market-skills", _HISTORY_FILENAME)


def _resolve_path(path: str | os.PathLike | None) -> str:
    if path is None:
        return default_history_path()
    return os.fspath(path)


def load_history(path: str | os.PathLike | None = None) -> list[dict]:
    """Read the rolling macro history. Returns [] on missing/malformed."""
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


def append_macro_tick(
    signal: dict[str, Any],
    *,
    path: str | os.PathLike | None = None,
) -> int:
    """Append a RegimeSignal-shaped dict to the ring buffer.

    Returns 1 on success, 0 on error (the caller — a cron tick —
    must not crash on a bad write). The stored entry keeps the
    derived regime labels and the regime_note; the raw inputs are
    summarised to keep the file small.
    """
    if not isinstance(signal, dict):
        return 0
    try:
        resolved = _resolve_path(path)
        history = load_history(resolved)
    except OSError:
        return 0

    inputs = signal.get("inputs") or {}
    entry = {
        "ts": signal.get("timestamp") or _dt.datetime.now(_dt.UTC).isoformat(),
        "risk_appetite": (signal.get("regime") or {}).get("risk_appetite"),
        "liquidity": (signal.get("regime") or {}).get("liquidity"),
        "sentiment": (signal.get("regime") or {}).get("sentiment"),
        "vix": inputs.get("vix"),
        "dxy": inputs.get("dxy"),
        "us10y": inputs.get("us10y"),
        "fng": inputs.get("fng"),
        "btc_dominance": inputs.get("btc_dominance"),
        "btc_dominance_source": inputs.get("btc_dominance_source"),
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
    """Best-effort append used by fetch_regime. Never raises."""
    try:
        append_macro_tick(signal)
    except Exception:  # noqa: BLE001 — history write must not crash fetcher
        return
