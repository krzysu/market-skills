"""Disk-backed OHLC candle cache.

Sits in front of :func:`analysis.data.fetch_ohlc` so repeated fetches of the
same ``(provider, ticker, interval, period)`` within a TTL window hit the disk
store instead of the venue. The cache is keyed by the fully-resolved
``provider:ticker`` routing plus timeframe, and lives under
``$XDG_DATA_HOME/market-skills/ohlc_cache.json``.

The cache is **opt-in**: it is only consulted when a TTL greater than zero is
configured (via :func:`cache_ttl_seconds` — env ``MARKET_SKILLS_OHLC_CACHE_TTL``
or an explicit argument). With TTL = 0 (the default) callers fall straight
through to the live provider, preserving today's deterministic behavior. This
keeps the change backward-compatible: no skill script edits are required, and
the agent enables caching per-run (e.g. for cron) by setting the env var.

Candles are plain ``list[list]`` (timestamp, open, high, low, close, volume), so
they serialize to JSON losslessly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "ohlc_cache.json"
_DEFAULT_TTL_ENV = "MARKET_SKILLS_OHLC_CACHE_TTL"
_MAX_ENTRIES = 2000

_lock = threading.Lock()


def _cache_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        raise OSError(
            "XDG_DATA_HOME is not set; cannot resolve the OHLC cache path. "
            "Set XDG_DATA_HOME or pass path= explicitly to the cache helpers."
        )
    return os.path.join(base, "market-skills")


def _cache_path(path: str | None = None) -> str:
    if path:
        return path
    return os.path.join(_cache_dir(), _CACHE_FILENAME)


def cache_ttl_seconds(override: int | None = None) -> int:
    """Resolve the active TTL in seconds.

    Precedence: explicit ``override`` > ``$MARKET_SKILLS_OHLC_CACHE_TTL`` >
    0 (disabled). A TTL of 0 means the cache is bypassed entirely.
    """
    if override is not None:
        return max(0, int(override))
    raw = os.environ.get(_DEFAULT_TTL_ENV)
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; OHLC cache disabled", _DEFAULT_TTL_ENV, raw)
        return 0


def make_key(provider: str, ticker: str, interval: str, period: str) -> str:
    """Build a stable cache key for a (provider, ticker, interval, period) tuple."""
    return f"{provider}:{ticker}:{interval}:{period}"


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.debug("ohlc cache load (%s): %s", path, e)
    return {}


def _persist(path: str, store: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh)
    os.replace(tmp, path)


def get_cached(
    key: str,
    ttl_seconds: int,
    path: str | None = None,
) -> list[list] | None:
    """Return cached candles for ``key`` if present and within TTL, else None."""
    if ttl_seconds <= 0:
        return None
    path = _cache_path(path)
    with _lock:
        store = _load(path)
        entry = store.get(key)
        if entry is None:
            return None
        ts = entry.get("ts")
        candles = entry.get("candles")
        if ts is None or candles is None:
            return None
        if time.time() - float(ts) > ttl_seconds:
            return None
        return candles


def put_cached(
    key: str,
    candles: list[list],
    ttl_seconds: int,
    path: str | None = None,
) -> None:
    """Store ``candles`` under ``key`` (no-op when TTL disabled or candles empty)."""
    if ttl_seconds <= 0 or not candles:
        return
    path = _cache_path(path)
    with _lock:
        store = _load(path)
        store[key] = {"ts": time.time(), "candles": candles}
        if len(store) > _MAX_ENTRIES:
            ordered = sorted(store.items(), key=lambda kv: kv[1].get("ts", 0.0))
            store = dict(ordered[len(store) - _MAX_ENTRIES :])
        _persist(path, store)


def clear_cache(path: str | None = None) -> None:
    """Delete the on-disk cache file if it exists."""
    path = _cache_path(path)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug("ohlc cache clear (%s): %s", path, e)
