"""Tests for analysis/providers/data/yfinance.py — retry behaviour on yf.download() failures.

Reference: specs/L0_RETRY_SPEC.md.
"""

from unittest.mock import patch

import pandas as pd

from analysis.providers.data import yfinance as yf_module
from analysis.providers.data.yfinance import YFinanceProvider


def _ok_df() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [1000.0, 1100.0, 1200.0],
        },
        index=idx,
    )


def test_yfinance_retries_on_connection_error():
    """yf.download raises ConnectionError twice → retry, succeed on 3rd."""
    p = YFinanceProvider()
    call_count = {"n": 0}

    def fake_download(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ConnectionError("network glitch")
        return _ok_df()

    with patch.object(yf_module.yf, "download", side_effect=fake_download):
        with patch("analysis.providers.data._retry.time.sleep"):
            candles = p.fetch("AAPL", interval="1d", period="5d")

    assert len(candles) == 3
    assert call_count["n"] == 3


def test_yfinance_does_not_retry_on_value_error():
    """yf.download raises ValueError → 1 call only, returns empty list."""
    p = YFinanceProvider()
    call_count = {"n": 0}

    def fake_download(*args, **kwargs):
        call_count["n"] += 1
        raise ValueError("invalid ticker")

    with patch.object(yf_module.yf, "download", side_effect=fake_download):
        with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
            candles = p.fetch("BOGUS", interval="1d", period="5d")

    assert candles == []
    assert call_count["n"] == 1
    mock_sleep.assert_not_called()
