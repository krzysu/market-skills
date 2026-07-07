"""Tests for analysis/providers/data/hyperliquid.py — retry behaviour on ccxt failures.

Reference: specs/L0_RETRY_SPEC.md. ccxt's NetworkError (including RequestTimeout)
is transient; BadRequest (4xx / business-rule errors) is not.
"""

from unittest.mock import patch

import ccxt
import pytest

from analysis.providers.data.hyperliquid import HyperliquidProvider


@pytest.fixture
def provider() -> HyperliquidProvider:
    p = HyperliquidProvider()
    p._markets_loaded = True
    p._exchange.markets = {"LIT/USDC:USDC": {"id": "LIT"}}
    p._exchange.markets_by_id = {"LIT": {"symbol": "LIT/USDC:USDC"}}
    p._exchange.symbols = ["LIT/USDC:USDC"]
    return p


def test_hyperliquid_retries_on_network_error(provider: HyperliquidProvider):
    """ccxt.NetworkError on calls 1+2 → retry, succeed on 3rd."""
    call_count = {"n": 0}
    # ccxt returns ms; provider divides by 1000 to convert to seconds.
    ccxt_return = [[1700000000000, 1.0, 2.0, 0.5, 1.5, 100.0]]

    def fake_fetch_ohlcv(symbol, interval, since):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ccxt.NetworkError("transient network glitch")
        return ccxt_return

    provider._exchange.fetch_ohlcv = fake_fetch_ohlcv

    with patch("analysis.providers.data._retry.time.sleep"):
        candles = provider.fetch("LIT", interval="1d", period="5d")

    # Provider converts ms → s (// 1000) so the output is seconds, not millis.
    expected_seconds = [[1700000000, 1.0, 2.0, 0.5, 1.5, 100.0]]
    assert candles == expected_seconds
    assert call_count["n"] == 3


def test_hyperliquid_does_not_retry_on_bad_request(provider: HyperliquidProvider):
    """ccxt.BadRequest (4xx) → 1 call only, returns empty list."""
    call_count = {"n": 0}

    def fake_fetch_ohlcv(symbol, interval, since):
        call_count["n"] += 1
        raise ccxt.BadRequest("invalid symbol")

    provider._exchange.fetch_ohlcv = fake_fetch_ohlcv

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        candles = provider.fetch("NOPE", interval="1d", period="5d")

    assert candles == []
    assert call_count["n"] == 1
    mock_sleep.assert_not_called()
