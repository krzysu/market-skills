"""Tests for `with_retry` transient_result predicate and kraken API-error detection.

Regression for BUGS-2026-07-08-2 — provider retry only caught
``subprocess.TimeoutExpired``; Kraken error bodies (rc=0,
``{"error":"api","message":"EGeneral:Internal error"}``) bypassed retry
and cascaded into position-watchdog FATAL trips on single-tick blips.

The new ``transient_result`` parameter lets callers declare "if the result
matches this predicate, retry" alongside the existing exception-tuple
mechanism. The kraken adapter wires ``_is_kraken_api_error`` to detect
well-formed transient error bodies and skip permanent errors like
``EQuery:Unknown asset pair``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from analysis.providers.data import kraken as kraken_module
from analysis.providers.data._retry import with_retry
from analysis.providers.data.kraken import KrakenProvider, _is_kraken_api_error

# ---------------------------------------------------------------------------
# `with_retry` transient_result predicate
# ---------------------------------------------------------------------------


def test_transient_result_triggers_retry():
    """Bad result on attempts 1-2, good on 3 → returns the good result, 3 calls."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "bad" if calls["n"] < 3 else "good"

    with patch("analysis.providers.data._retry.time.sleep"):
        result = with_retry(
            fn,
            label="t",
            transient_result=lambda r: r == "bad",
        )
    assert result == "good"
    assert calls["n"] == 3


def test_transient_result_exhausted_returns_bad_result():
    """Always-bad result → returns the bad value after `attempts` calls (no raise)."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "bad"

    with patch("analysis.providers.data._retry.time.sleep"):
        result = with_retry(
            fn,
            attempts=3,
            label="t",
            transient_result=lambda r: r == "bad",
        )
    assert result == "bad"
    assert calls["n"] == 3


def test_transient_result_none_keeps_prior_behavior():
    """`transient_result=None` (default) → bad result returned immediately on attempt 1."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "bad"

    with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
        result = with_retry(fn, label="t")
    assert result == "bad"
    assert calls["n"] == 1
    mock_sleep.assert_not_called()


def test_transient_result_logs_on_outcome(caplog):
    """Logger receives warnings on transient-result retry and exhaustion."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "bad"

    logger = logging.getLogger("test.transient_result_logs")
    with patch("analysis.providers.data._retry.time.sleep"):
        with caplog.at_level(logging.WARNING, logger="test.transient_result_logs"):
            result = with_retry(
                fn,
                attempts=2,
                base_delay=0.0,
                jitter=0.0,
                transient_result=lambda r: r == "bad",
                logger=logger,
                label="probe",
            )
    assert result == "bad"
    assert any("transient result" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# `_is_kraken_api_error` predicate
# ---------------------------------------------------------------------------


def _proc(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["kraken"], returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize(
    "message",
    [
        "EGeneral:Internal error",
        "EService:Unavailable",
        "EInternal:Database error",
        "EService:Busy",
        "EGeneral:Rate limit exceeded",
        "EService:Request timeout",
        "EService:Temporarily unavailable",
    ],
)
def test_kraken_api_error_body_is_transient(message):
    """Each marker on the transient shortlist → predicate True."""
    body = json.dumps({"error": "api", "message": message})
    assert _is_kraken_api_error(_proc(body)) is True


def test_kraken_permanent_error_is_not_transient():
    """``EQuery:Unknown asset pair`` carries no transient marker → predicate False."""
    body = json.dumps({"error": "api", "message": "EQuery:Unknown asset pair"})
    assert _is_kraken_api_error(_proc(body)) is False


def test_kraken_valid_ticker_response_is_not_transient():
    """Real ticker body (no `error` key) → predicate False."""
    body = json.dumps({"XXBTZUSD": {"c": ["50000.0", "1"], "b": ["49999.0", "1"], "a": ["50001.0", "1"]}})
    assert _is_kraken_api_error(_proc(body)) is False


def test_kraken_nonzero_returncode_is_not_transient():
    """Non-zero returncode is handled by the existing returncode check, not this predicate."""
    body = json.dumps({"error": "api", "message": "EGeneral:Internal error"})
    assert _is_kraken_api_error(_proc(body, returncode=1)) is False


def test_kraken_malformed_json_is_not_transient():
    """Malformed stdout → predicate False (let the JSON parse check handle it)."""
    assert _is_kraken_api_error(_proc("not json {")) is False


def test_kraken_error_key_without_message_is_not_transient():
    """Defensive: `error` key set but no `message` → False (no marker to match)."""
    body = json.dumps({"error": "api"})
    assert _is_kraken_api_error(_proc(body)) is False


# ---------------------------------------------------------------------------
# End-to-end: kraken fetch_spot_price retries on transient API-error body
# ---------------------------------------------------------------------------


def _ok_ticker_payload(pair: str = "XXBTZUSD") -> str:
    return json.dumps({pair: {"c": ["50000.0", "1"], "b": ["49999.0", "1"], "a": ["50001.0", "1"]}})


def _ok_result(stdout: str) -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def test_kraken_spot_price_retries_on_transient_api_error_body():
    """Transient error body on calls 1+2 → retry via transient_result, succeed on 3."""
    p = KrakenProvider()
    call_count = {"n": 0}
    bad = json.dumps({"error": "api", "message": "EGeneral:Internal error"})

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return _ok_result(bad)
        return _ok_result(_ok_ticker_payload("XXBTZUSD"))

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep"):
            result = p.fetch_spot_price("BTCUSD")

    assert result is not None
    assert result["price"] == 50000.0
    assert call_count["n"] == 3


def test_kraken_spot_price_does_not_retry_on_permanent_api_error_body():
    """``EQuery:Unknown asset pair`` carries no transient marker → no retry, return None."""
    p = KrakenProvider()
    call_count = {"n": 0}
    bad = json.dumps({"error": "api", "message": "EQuery:Unknown asset pair"})

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return _ok_result(bad)

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep") as mock_sleep:
            result = p.fetch_spot_price("BTCUSD")

    assert result is None
    assert call_count["n"] == 1
    mock_sleep.assert_not_called()


def test_kraken_spot_price_returns_none_after_transient_exhaustion():
    """3 transient error bodies in a row → return None after exhaustion (no raise)."""
    p = KrakenProvider()
    call_count = {"n": 0}
    bad = json.dumps({"error": "api", "message": "EGeneral:Internal error"})

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return _ok_result(bad)

    with patch.object(kraken_module.subprocess, "run", side_effect=fake_run):
        with patch("analysis.providers.data._retry.time.sleep"):
            result = p.fetch_spot_price("BTCUSD")

    assert result is None
    assert call_count["n"] == 3
