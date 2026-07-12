"""Source fetchers for cross-asset macro regime inputs.

Four HTTP fetch functions (F&G, VIX/DXY/US10Y, BTC mcap, CoinGecko).
Each is error-isolated — exceptions return (None, error-str) tuples
so a single source failure never crashes the caller.
"""

import math

import requests
import yfinance as yf

from analysis.macro._constants import (
    _COINGECKO_TIMEOUT_S,
    _COINGECKO_UA,
    _COINGECKO_URL,
    _FNG_TIMEOUT_S,
    _FNG_URL,
    _LABEL_COINGECKO,
    _LABEL_FNG,
)


def _fetch_fng(timeout_s: int = _FNG_TIMEOUT_S) -> tuple[float | None, str | None, str | None]:
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
    try:
        info = yf.Ticker(symbol).fast_info
        raw = getattr(info, "last_price", None)
    except Exception as e:  # yfinance raises many subclasses of Exception
        return None, f"{label}: {type(e).__name__}"
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
    try:
        info = yf.Ticker(symbol).fast_info
        raw = getattr(info, "market_cap", None)
    except Exception as e:
        return None, f"{label}: {type(e).__name__}"
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
