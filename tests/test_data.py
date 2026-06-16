"""Tests for lib/data.py — data fetching and provider routing."""

from analysis.data import _PREFIX_MAP, _resolve_ticker_prefix


class TestResolveTickerPrefix:
    def test_hl_prefix(self):
        result = _resolve_ticker_prefix("hl:LIT")
        assert result == ("LIT", "hyperliquid")

    def test_kraken_prefix(self):
        result = _resolve_ticker_prefix("kraken:BTC-USD")
        assert result == ("BTC-USD", "kraken")

    def test_yf_prefix(self):
        result = _resolve_ticker_prefix("yf:AAPL")
        assert result == ("AAPL", "yfinance")

    def test_yfinance_prefix(self):
        result = _resolve_ticker_prefix("yfinance:AAPL")
        assert result == ("AAPL", "yfinance")

    def test_no_prefix(self):
        assert _resolve_ticker_prefix("SPY") is None

    def test_unknown_prefix(self):
        assert _resolve_ticker_prefix("unknown:FOO") is None

    def test_empty_string(self):
        assert _resolve_ticker_prefix("") is None


class TestPrefixMap:
    def test_all_prefixes_mapped(self):
        known_providers = {"hyperliquid", "kraken", "yfinance"}
        mapped = set(_PREFIX_MAP.values())
        for p in known_providers:
            assert p in mapped, f"provider {p} not in _PREFIX_MAP"
