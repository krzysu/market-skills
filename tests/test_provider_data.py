"""Integration tests for analysis/providers/data — live API calls.

Marked ``network`` to skip on CI or offline runs (``pytest --skip-network``).

Import order: ``analysis.data`` MUST be imported before
``analysis.providers.data.hyperliquid`` because the latter shadows
``sys.modules["ccxt"]``, breaking ``CCXTProvider("binance")`` module-level init.
"""

import pytest

from analysis import data  # noqa: F401 — pre-load real ccxt before HL shadows it


@pytest.mark.network
class TestHyperliquidFetchSpotPriceIntegration:
    """fetch_spot_price must work against the real HL API, not just mocks."""

    def test_fetch_spot_price_lit_returns_dict(self):
        from analysis.providers.data.hyperliquid import HyperliquidProvider

        p = HyperliquidProvider()
        result = p.fetch_spot_price("LIT")
        assert result is not None, (
            "HL fetch_spot_price returned None for LIT — likely the "
            "fetch_spot_markets TypeError bug. See BUGS-2026-07-09-4."
        )
        assert "price" in result
        assert "last" in result
        assert result["price"] > 0

    def test_fetch_spot_price_hl_prefix_resolves(self):
        from analysis.data import fetch_spot_price

        result = fetch_spot_price("hl:LIT")
        assert result is not None, (
            "fetch_spot_price('hl:LIT') returns None — contract violation: "
            "the prefix MUST resolve and the provider MUST return a price."
        )

    def test_analysis_data_fetch_spot_price_hl_does_not_silently_fail(self):
        from analysis.data import fetch_spot_price

        cases = [
            ("hl:LIT", "hyperliquid"),
            ("kraken:BTCUSD", "kraken"),
        ]
        for ticker, label in cases:
            r = fetch_spot_price(ticker)
            assert r is not None, f"{label}: fetch_spot_price({ticker!r}) returned None"
