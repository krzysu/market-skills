"""Tests for analysis/data.py — data fetching and provider routing."""

from analysis.data import _PREFIX_MAP, _REGISTRY, _get_provider, _resolve_ticker_prefix


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


class TestProviderRegistryOrder:
    def test_registry_order_is_pinned(self):
        """Pins the auto-detect order: HL → CCXT(binance) → Kraken → YFinance.

        Reordering changes which provider handles a bare ticker on
        fallback paths. For example ``AAPL`` falls through HL, CCXT(binance),
        Kraken before YFinance picks it up; reordering would silently
        route stocks to crypto venues first.
        """
        names = [p.name for p in _REGISTRY]
        assert names == ["hyperliquid", "ccxt", "kraken", "yfinance"], (
            f"provider registry order changed: {names}. "
            "If intentional, update this test and the ARCHITECTURE.md diagram."
        )

    def test_yfinance_is_last_fallback(self):
        """YFinance must remain the last resort so it doesn't shadow a
        crypto venue that explicitly supports the ticker."""
        assert _REGISTRY[-1].name == "yfinance"

    def test_get_provider_by_name(self):
        hl = _get_provider("hyperliquid")
        assert hl.name == "hyperliquid"
        k = _get_provider("kraken")
        assert k.name == "kraken"

    def test_get_provider_unknown_raises(self):
        import pytest

        with pytest.raises(ValueError, match="Unknown provider"):
            _get_provider("does-not-exist")
