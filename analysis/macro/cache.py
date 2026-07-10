"""In-process TTL cache for macro regime signals."""

import threading
import time
from typing import Any

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
    with _cache_lock:
        _cache.clear()
