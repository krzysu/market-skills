"""Tests for analysis/providers/data/kraken.py — retry behaviour on subprocess failures.

Reference: specs/L0_RETRY_SPEC.md. Retry is purely additive; the existing
return shapes (None / [] on failure, populated data on success) are preserved.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from analysis.providers.data import kraken as kraken_module
from analysis.providers.data.kraken import KrakenProvider


def _ok_ticker_payload(pair: str = "XXBTZUSD") -> str:
    return json.dumps({pair: {"c": ["50000.0", "1"], "b": ["49999.0", "1"], "a": ["50001.0", "1"]}})


def _ok_ohlc_payload(pair: str = "XXBTZUSD") -> str:
    return json.dumps({pair: [[1700000000, "49900", "50100", "49800", "50050", "50000", "10"]]})


def _ok_result(stdout: str) -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def test_kraken_spot_price_retries_on_timeout_expired():
    """Subprocess.TimeoutExpired on calls 1+2 → retry, succeed on 3."""
    p = KrakenProvider()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise subprocess.TimeoutExpired(cmd="kraken", timeout=10)
        return _ok_result(_ok_ticker_payload("XXBTZUSD"))

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep"):
            result = p.fetch_spot_price("BTCUSD")

    assert result is not None
    assert result["price"] == 50000.0
    assert result["last"] == 50000.0
    assert call_count["n"] == 3


def test_kraken_ohlc_retries_on_timeout_expired():
    """fetch() retries on TimeoutExpired and returns populated candles on success."""
    p = KrakenProvider()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise subprocess.TimeoutExpired(cmd="kraken", timeout=30)
        return _ok_result(_ok_ohlc_payload("XXBTZUSD"))

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep"):
            candles = p.fetch("BTCUSD", interval="1d", period="5d")

    assert len(candles) == 1
    assert candles[0][0] == 1700000000
    assert candles[0][4] == 50050.0
    assert call_count["n"] == 3


def test_kraken_does_not_retry_on_nonzero_returncode():
    """subprocess.returncode != 0 → 1 call only, no retry, returns empty."""
    p = KrakenProvider()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "API error"
        return r

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
            candles = p.fetch("BTCUSD")

    assert candles == []
    assert call_count["n"] == 1
    mock_sleep.assert_not_called()


def test_kraken_does_not_retry_on_json_decode_error():
    """returncode=0 but malformed JSON → 1 call only, no retry, returns empty."""
    p = KrakenProvider()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return _ok_result("this is not json {")

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
            candles = p.fetch("BTCUSD")

    assert candles == []
    assert call_count["n"] == 1
    mock_sleep.assert_not_called()


def test_kraken_does_not_retry_on_file_not_found():
    """FileNotFoundError means the kraken CLI is not installed — no retry, return empty."""
    p = KrakenProvider()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        raise FileNotFoundError("kraken CLI not in PATH")

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
            candles = p.fetch("BTCUSD")
            spot = p.fetch_spot_price("BTCUSD")

    assert candles == []
    assert spot is None
    assert call_count["n"] == 2  # once for fetch(), once for fetch_spot_price()
    mock_sleep.assert_not_called()
