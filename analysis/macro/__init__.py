"""Cross-asset macro regime fetcher + classifier.

Fetches six inputs from three sources, derives a three-axis regime
label, and returns a ``RegimeSignal`` per ``ARCHITECTURE.md 'Macro domain'``.

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
from typing import Any

from analysis.macro._constants import (
    _LABEL_BTC_MCAP,
    _LABEL_DXY,
    _LABEL_US10Y,
    _LABEL_VIX,
    _YF_BTC_MCAP,
    _YF_DXY,
    _YF_US10Y,
    _YF_VIX,
    DEFAULT_TTL_SECONDS,
)
from analysis.macro.cache import _cache_get, _cache_put, clear_cache
from analysis.macro.classify import (
    _classify_liquidity,
    _classify_risk_appetite,
    _classify_sentiment,
    _format_regime_note,
)
from analysis.macro.fetchers import (
    _fetch_coingecko,
    _fetch_fng,
    _fetch_yf_market_cap,
    _fetch_yf_price,
)
from analysis.macro.history import (
    _safe_append_tick,
    append_macro_tick,
    default_history_path,
    load_history,
)


def fetch_regime(
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    write_history: bool = True,
) -> dict[str, Any]:
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

    # Resolve missing canonical inputs from the final payload, not from
    # raw error diagnostics. A primary-source error whose fallback has
    # already populated the canonical value (e.g. btc_mcap error but
    # coingecko supplied btc_dominance) must not mark the regime
    # incomplete — that would poison the fallback-success path with a
    # false UNKNOWN headline and a [REGIME INCOMPLETE] note prefix.
    missing_inputs = [
        name
        for name in ("fng", "vix", "dxy", "us10y", "btc_dominance", "total_mcap_usd")
        if inputs_payload.get(name) is None
    ]

    risk_appetite = _classify_risk_appetite(vix, dxy, us10y)
    liquidity = _classify_liquidity(us10y, vix)
    sentiment = _classify_sentiment(fng_value)

    incomplete = bool(missing_inputs)
    if incomplete and risk_appetite != "UNKNOWN":
        risk_appetite = "UNKNOWN"

    regime_payload: dict[str, str] = {
        "risk_appetite": risk_appetite,
        "liquidity": liquidity,
        "sentiment": sentiment,
    }
    note = _format_regime_note(risk_appetite, liquidity, sentiment, inputs_payload)
    if incomplete and not note.startswith("[REGIME INCOMPLETE"):
        note = f"[REGIME INCOMPLETE — {len(missing_inputs)} input(s) missing] " + note

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


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "append_macro_tick",
    "clear_cache",
    "default_history_path",
    "fetch_regime",
    "load_history",
]
