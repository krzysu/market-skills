"""Tests for analysis/data.py — data fetching and provider routing."""

from unittest.mock import MagicMock

from analysis.data import (
    _PREFIX_MAP,
    _REGISTRY,
    _get_provider,
    _resolve_ticker_prefix,
    fetch_funding_rate,
)


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


def _mock_provider(name: str, *, supports_funding: bool, supports_ticker: bool, funding_result: dict | None):
    """Build a MagicMock provider for fetch_funding_rate tests."""
    p = MagicMock()
    p.name = name
    if supports_funding:
        p.fetch_funding_rate = MagicMock(return_value=funding_result)
    else:
        # Remove the attribute so hasattr() returns False, mirroring real
        # providers that don't implement funding rates (e.g. yfinance).
        del p.fetch_funding_rate
    p.supports = MagicMock(return_value=supports_ticker)
    return p


class TestFetchFundingRatePrefixRouting:
    """Verifies ``provider:ticker`` prefixes are stripped before auto-detect."""

    def test_hl_prefix_strips_and_runs_auto_detect(self, monkeypatch):
        """`hl:HYPE` should strip the prefix and call auto-detect with `HYPE`."""
        hl = _mock_provider(
            "hyperliquid",
            supports_funding=True,
            supports_ticker=True,
            funding_result={"fundingRate": 0.0001, "source": "hyperliquid"},
        )
        ccxt = _mock_provider(
            "ccxt",
            supports_funding=True,
            supports_ticker=True,
            funding_result=None,
        )
        kraken = _mock_provider(
            "kraken",
            supports_funding=False,
            supports_ticker=True,
            funding_result=None,
        )
        yfinance = _mock_provider(
            "yfinance",
            supports_funding=False,
            supports_ticker=True,
            funding_result=None,
        )
        monkeypatch.setattr("analysis.data._REGISTRY", [hl, ccxt, kraken, yfinance])

        result = fetch_funding_rate("hl:HYPE")

        assert result == {"fundingRate": 0.0001, "source": "hyperliquid"}
        # The prefix must be stripped before supports()/fetch_funding_rate().
        hl.supports.assert_called_with("HYPE")
        hl.fetch_funding_rate.assert_called_once_with("HYPE")
        # yfinance lacks fetch_funding_rate, so it must never be asked.
        yfinance.supports.assert_not_called()

    def test_yf_prefix_returns_none(self, monkeypatch):
        """`yf:AAPL` strips to `AAPL`, but yfinance has no funding rate method."""
        hl = _mock_provider(
            "hyperliquid",
            supports_funding=True,
            supports_ticker=False,
            funding_result=None,
        )
        ccxt = _mock_provider(
            "ccxt",
            supports_funding=True,
            supports_ticker=False,
            funding_result=None,
        )
        kraken = _mock_provider(
            "kraken",
            supports_funding=False,
            supports_ticker=False,
            funding_result=None,
        )
        yfinance = _mock_provider(
            "yfinance",
            supports_funding=False,
            supports_ticker=True,
            funding_result=None,
        )
        monkeypatch.setattr("analysis.data._REGISTRY", [hl, ccxt, kraken, yfinance])

        result = fetch_funding_rate("yf:AAPL")

        assert result is None
        # Prefix stripped: bare AAPL is what providers see.
        hl.supports.assert_called_with("AAPL")
        ccxt.supports.assert_called_with("AAPL")
        # yfinance has no fetch_funding_rate, so it's skipped silently.
        assert not hasattr(yfinance, "fetch_funding_rate")

    def test_bare_ticker_still_works_without_prefix(self, monkeypatch):
        """`HYPEUSD` (no prefix) must follow the same auto-detect path."""
        hl = _mock_provider(
            "hyperliquid",
            supports_funding=True,
            supports_ticker=True,
            funding_result={"fundingRate": 0.0002, "source": "hyperliquid"},
        )
        ccxt = _mock_provider(
            "ccxt",
            supports_funding=True,
            supports_ticker=True,
            funding_result=None,
        )
        kraken = _mock_provider(
            "kraken",
            supports_funding=False,
            supports_ticker=True,
            funding_result=None,
        )
        yfinance = _mock_provider(
            "yfinance",
            supports_funding=False,
            supports_ticker=True,
            funding_result=None,
        )
        monkeypatch.setattr("analysis.data._REGISTRY", [hl, ccxt, kraken, yfinance])

        result = fetch_funding_rate("HYPEUSD")

        assert result == {"fundingRate": 0.0002, "source": "hyperliquid"}
        hl.supports.assert_called_with("HYPEUSD")
        hl.fetch_funding_rate.assert_called_once_with("HYPEUSD")

    def test_kraken_prefix_strips_and_matches_bare_ticker(self, monkeypatch):
        """`kraken:BTCUSD` strips to `BTCUSD` and matches `fetch_funding_rate('BTCUSD')`.

        Acceptance criterion #2: the prefix must not select the kraken
        provider — it is stripped, and auto-detect runs on the bare
        ticker. The result is therefore identical to calling with no
        prefix at all.
        """
        kraken = _mock_provider(
            "kraken",
            supports_funding=True,
            supports_ticker=True,
            funding_result={"fundingRate": 0.00005, "source": "kraken"},
        )
        ccxt = _mock_provider(
            "ccxt",
            supports_funding=True,
            supports_ticker=True,
            funding_result=None,
        )
        hl = _mock_provider(
            "hyperliquid",
            supports_funding=True,
            supports_ticker=False,
            funding_result=None,
        )
        yfinance = _mock_provider(
            "yfinance",
            supports_funding=False,
            supports_ticker=True,
            funding_result=None,
        )
        monkeypatch.setattr("analysis.data._REGISTRY", [kraken, ccxt, hl, yfinance])

        prefixed = fetch_funding_rate("kraken:BTCUSD")
        bare = fetch_funding_rate("BTCUSD")

        # Acceptance criterion #2: same result either way.
        assert prefixed == bare == {"fundingRate": 0.00005, "source": "kraken"}
        # Prefix stripped: kraken sees BTCUSD, never "kraken:BTCUSD".
        kraken.supports.assert_called_with("BTCUSD")
        kraken.fetch_funding_rate.assert_called_with("BTCUSD")
        assert not any(call.args[0] == "kraken:BTCUSD" for call in kraken.supports.call_args_list)
