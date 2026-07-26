"""Integration tests — run skills via subprocess and validate the AXI envelope shape.

These tests exercise the full CLI path (arg parsing, data fetch, lib.py,
envelope emit) for a representative set of skills. They require network
access (yfinance) and are intentionally slow; mark with ``integration``
so CI can skip them when offline.
"""

import json
import subprocess
import sys

import pytest

REPO_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)


def _run_skill(script: str, *args: str) -> dict:
    cmd = [sys.executable, f"skills/{script}/scripts/run.py", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 0, f"skill exited {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


@pytest.mark.integration
class TestAXIEnvelopeShape:
    def test_l1_rsi_envelope(self):
        env = _run_skill("market-rsi", "SPY", "--json", "--source=yfinance")
        assert "data" in env
        assert "count" in env
        assert "errors" in env
        assert "help" in env
        assert isinstance(env["errors"], list)
        assert isinstance(env["help"], list)
        assert env["count"] == 1

    def test_l2_breakout_envelope(self):
        env = _run_skill("market-breakout", "SPY", "--json", "--source=yfinance")
        assert "data" in env
        assert "count" in env
        assert isinstance(env["errors"], list)
        assert isinstance(env["help"], list)

    def test_l3_trend_follow_envelope(self):
        env = _run_skill("strategy-trend-follow", "SPY", "--json", "--source=yfinance")
        assert "data" in env
        assert "count" in env
        assert isinstance(env["errors"], list)
        assert isinstance(env["help"], list)
        assert env["count"] >= 0

    def test_l1_fields_projection(self):
        env = _run_skill("market-rsi", "SPY", "--json", "--source=yfinance", "--fields=signal,score")
        data = env["data"]
        assert set(data.keys()) <= {"signal", "score"}

    def test_no_ticker_json_returns_empty_state(self):
        cmd = [sys.executable, "skills/market-rsi/scripts/run.py", "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=30)
        assert result.returncode == 0
        env = json.loads(result.stdout)
        assert env["data"] is None
        assert env["count"] == 0
        assert len(env["errors"]) > 0
