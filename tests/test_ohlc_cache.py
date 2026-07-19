"""Unit tests for the opt-in OHLC disk cache (analysis.providers.data.cache).

Pure and hermetic: exercises the cache helpers directly with a temp file path,
no network, no XDG_DATA_HOME dependency.
"""

import time

from analysis.providers.data import cache


def test_ttl_defaults_disabled_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MARKET_SKILLS_OHLC_CACHE_TTL", raising=False)
    assert cache.cache_ttl_seconds() == 0
    assert cache.cache_ttl_seconds(override=0) == 0


def test_ttl_override_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_SKILLS_OHLC_CACHE_TTL", "60")
    assert cache.cache_ttl_seconds(override=900) == 900
    assert cache.cache_ttl_seconds() == 60


def test_invalid_env_ttl_disables_cache(monkeypatch):
    monkeypatch.setenv("MARKET_SKILLS_OHLC_CACHE_TTL", "not-a-number")
    assert cache.cache_ttl_seconds() == 0


def test_make_key_is_stable_and_deterministic():
    a = cache.make_key("yfinance", "AAPL", "1d", "1y")
    b = cache.make_key("yfinance", "AAPL", "1d", "1y")
    assert a == b == "yfinance:AAPL:1d:1y"


def test_get_cached_miss_when_empty(tmp_path):
    assert cache.get_cached(cache.make_key("k", "T", "1d", "1y"), 60, path=str(tmp_path / "c.json")) is None


def test_put_then_get_roundtrip(tmp_path):
    p = str(tmp_path / "c.json")
    key = cache.make_key("yfinance", "AAPL", "1d", "1y")
    candles = [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]]
    cache.put_cached(key, candles, 60, path=p)
    got = cache.get_cached(key, 60, path=p)
    assert got == candles


def test_get_cached_expired_after_ttl(tmp_path):
    p = str(tmp_path / "c.json")
    key = cache.make_key("yfinance", "AAPL", "1d", "1y")
    cache.put_cached(key, [[1, 2, 3, 4, 5, 6]], ttl_seconds=1, path=p)
    time.sleep(1.1)
    # TTL is a per-read window: reading with the same 1s window now misses.
    assert cache.get_cached(key, ttl_seconds=1, path=p) is None


def test_put_noop_when_ttl_zero(tmp_path):
    p = str(tmp_path / "c.json")
    key = cache.make_key("yfinance", "AAPL", "1d", "1y")
    cache.put_cached(key, [[1, 2, 3, 4, 5, 6]], ttl_seconds=0, path=p)
    assert cache.get_cached(key, ttl_seconds=60, path=p) is None


def test_put_noop_when_candles_empty(tmp_path):
    p = str(tmp_path / "c.json")
    key = cache.make_key("yfinance", "AAPL", "1d", "1y")
    cache.put_cached(key, [], ttl_seconds=60, path=p)
    assert cache.get_cached(key, ttl_seconds=60, path=p) is None


def test_clear_cache_removes_file(tmp_path):
    p = str(tmp_path / "c.json")
    key = cache.make_key("yfinance", "AAPL", "1d", "1y")
    cache.put_cached(key, [[1, 2, 3, 4, 5, 6]], ttl_seconds=60, path=p)
    cache.clear_cache(path=p)
    assert cache.get_cached(key, ttl_seconds=60, path=p) is None


def test_cache_evicts_oldest_beyond_cap(tmp_path, monkeypatch):
    p = str(tmp_path / "c.json")
    monkeypatch.setattr(cache, "_MAX_ENTRIES", 3)
    for i in range(5):
        cache.put_cached(f"k{i}", [[i, 0, 0, 0, 0, 0]], ttl_seconds=60, path=p)
    # k0, k1 should have been evicted (oldest); k2..k4 survive.
    assert cache.get_cached("k0", ttl_seconds=60, path=p) is None
    assert cache.get_cached("k4", ttl_seconds=60, path=p) is not None
