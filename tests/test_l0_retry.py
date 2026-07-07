"""Unit tests for analysis/providers/data/_retry.py — with_retry() helper.

Reference: specs/L0_RETRY_SPEC.md. The retry helper is the single source of
truth for transient-failure resilience at the L0 provider layer.
"""

import logging
import subprocess
from unittest.mock import patch

import pytest

from analysis.providers.data._retry import (
    DEFAULT_BASE_DELAY_S,
    DEFAULT_JITTER_S,
    TRANSIENT_NETWORK,
    with_retry,
)


def test_returns_first_success():
    """First call succeeds → returns immediately, 1 call total, no sleep."""
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        result = with_retry(fn, label="t")
    assert result == "ok"
    assert len(calls) == 1
    mock_sleep.assert_not_called()


def test_retries_on_transient_then_succeeds():
    """Fail twice with TimeoutError, succeed on attempt 3 → returns value, 3 calls, 2 sleeps."""
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return "ok"

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        result = with_retry(fn, label="t")
    assert result == "ok"
    assert len(calls) == 3
    assert mock_sleep.call_count == 2


def test_gives_up_after_max_attempts():
    """Fail attempts=3 times with TimeoutError → raises the last exception."""
    calls = []

    def fn():
        calls.append(1)
        raise TimeoutError(f"call {len(calls)}")

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        with pytest.raises(TimeoutError, match="call 3"):
            with_retry(fn, attempts=3, label="t")
    assert len(calls) == 3
    assert mock_sleep.call_count == 2  # sleeps after attempts 1 and 2 only


def test_does_not_retry_on_non_transient():
    """Raise ValueError → 1 call only, propagates immediately (no sleep, no retry)."""
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("structural")

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="structural"):
            with_retry(fn, label="t")
    assert len(calls) == 1
    mock_sleep.assert_not_called()


def test_exponential_backoff_with_jitter():
    """Sleep durations follow base * 2^i + uniform(0, jitter) for i in 0..attempts-2."""
    sleeps = []

    def fn():
        raise TimeoutError("never works")

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with patch("analysis.providers.data._retry.time.sleep", side_effect=fake_sleep):
        with pytest.raises(TimeoutError):
            with_retry(
                fn,
                attempts=3,
                base_delay=DEFAULT_BASE_DELAY_S,
                jitter=DEFAULT_JITTER_S,
                label="t",
            )

    assert len(sleeps) == 2
    # i=0 → 1.0 + uniform(0, 0.3); i=1 → 2.0 + uniform(0, 0.3). Never sleeps after the last attempt.
    assert 1.0 <= sleeps[0] < 1.0 + DEFAULT_JITTER_S + 1e-9
    assert 2.0 <= sleeps[1] < 2.0 + DEFAULT_JITTER_S + 1e-9


def test_uses_custom_transient_tuple():
    """When the caller narrows `transient`, an exception outside that tuple propagates immediately."""
    calls = []

    def fn():
        calls.append(1)
        raise TimeoutError("not in tuple")

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        with pytest.raises(TimeoutError):
            with_retry(
                fn,
                transient=(ValueError,),
                attempts=3,
                label="t",
            )
    assert len(calls) == 1
    mock_sleep.assert_not_called()


def test_logs_warning_per_retry(caplog):
    """Each transient failure logs at WARNING level (so cron surfaces it)."""
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return "ok"

    with patch("analysis.providers.data._retry.time.sleep"):
        with caplog.at_level(logging.WARNING, logger="test.retry"):
            with_retry(
                fn,
                logger=logging.getLogger("test.retry"),
                label="kraken.ticker(BTCUSD)",
            )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "kraken.ticker(BTCUSD)" in warnings[0].getMessage()
    assert "attempt 1/3" in warnings[0].getMessage()
    assert "attempt 2/3" in warnings[1].getMessage()


def test_does_not_sleep_after_last_attempt():
    """After the final failure the helper raises immediately — no trailing sleep."""
    calls = []

    def fn():
        calls.append(1)
        raise subprocess.TimeoutExpired(cmd="kraken", timeout=10)

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        with pytest.raises(subprocess.TimeoutExpired):
            with_retry(
                fn,
                transient=TRANSIENT_NETWORK + (subprocess.TimeoutExpired,),
                attempts=3,
                label="t",
            )
    assert len(calls) == 3
    assert mock_sleep.call_count == 2
