"""Pilot envelope tests for the AXI phase-1 rollout (ADR-0004).

Pins the on-the-wire envelope shape for the four pilot sites:
  - market-rsi (L1)
  - market-trend-quality (L2)
  - strategy-trend-follow (L3)
  - run-all-l3 (batch runner)

Tests load each `scripts/run.py` via importlib (the same pattern as
``tests/test_risk_engine.py::TestRiskEngineCLI``), patch the
network-touching helper (``fetch_ohlc``), and assert the envelope.

These tests are the phase-1 exit criteria: when the pilot passes,
phase 2 can sweep the remaining L1/L2/L3 skills using the same
fixture shape.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from unittest.mock import patch

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _make_candles(n: int = 220, seed: int = 42) -> list[list]:
    rng = random.Random(seed)
    rows = []
    price = 100.0
    for i in range(n):
        price *= 1.0 + rng.uniform(-0.005, 0.012)
        rows.append([i * 86400, price, price + 0.5, price - 0.5, price, 100000])
    return rows


def _load_skill_run(skill_name: str):
    """Load ``skills/<name>/scripts/run.py`` as an importlib module."""
    run_path = os.path.join(REPO_ROOT, "skills", skill_name, "scripts", "run.py")
    spec = importlib.util.spec_from_file_location(f"{skill_name.replace('-', '_')}_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(mod, *argv):
    """Invoke ``mod.main()`` with the supplied argv. Returns rc.

    Returns None when main() doesn't explicitly return; the
    caller should treat None as "ran to completion" and rely on
    the captured stdout (capsys) to assert the shape.
    """
    full = ["run.py", *argv]
    with patch.object(sys, "argv", full):
        try:
            return mod.main()
        except SystemExit as e:
            return e.code


def _envelope_of(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out)


class TestMarketRSIEnvelope:
    def _run(self, *argv, candles=None, monkeypatch):
        mod = _load_skill_run("market-rsi")
        if candles is None:
            candles = _make_candles()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        rc = self._run("AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1
        assert env["errors"] == []
        assert isinstance(env["help"], list) and env["help"]

    def test_default_fields(self, capsys, monkeypatch):
        rc = self._run("AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env["data"].keys()) == {"ticker", "rsi_14", "signal", "score"}
        assert env["data"]["ticker"] == "AAPL"

    def test_full_payload(self, capsys, monkeypatch):
        rc = self._run("AAPL", "--json", "--full", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert "indicators" in env["data"] or "rsi_7d_ago" in env["data"]
        assert env["count"] == 1

    def test_fields_projection(self, capsys, monkeypatch):
        rc = self._run("AAPL", "--json", "--fields=ticker,signal", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env["data"].keys()) == {"ticker", "signal"}

    def test_help_lines_reference_next_steps(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "market-ema" in joined
        assert "market-trend-quality" in joined

    def test_no_data_returns_empty_state(self, capsys, monkeypatch):
        rc = self._run("ZZZZ", "--json", candles=[], monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["errors"]
        assert env["help"]


class TestMarketTrendQualityEnvelope:
    def _run(self, *argv, candles=None, monkeypatch):
        mod = _load_skill_run("market-trend-quality")
        if candles is None:
            candles = _make_candles()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        rc = self._run("AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1
        assert env["errors"] == []

    def test_default_fields_include_fired(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "fired" in env["data"]
        assert "classification" in env["data"]
        assert "confidence" in env["data"]

    def test_full_includes_signals_and_input_scores(self, capsys, monkeypatch):
        self._run("AAPL", "--json", "--full", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "signals" in env["data"]
        assert "input_scores" in env["data"]
        assert "narrative" in env["data"]

    def test_narrative_truncated_in_default_view(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        if "narrative" in env["data"]:
            assert env["data"]["narrative"] is None or len(env["data"]["narrative"]) <= 200

    def test_help_lines_reference_l3(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert any("strategy-trend-follow" in h for h in env["help"])

    def test_no_data_returns_empty_state(self, capsys, monkeypatch):
        self._run("ZZZZ", "--json", candles=[], monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["errors"]


class TestStrategyTrendFollowEnvelope:
    def _run(self, *argv, candles=None, monkeypatch):
        mod = _load_skill_run("strategy-trend-follow")
        if candles is None:
            candles = _make_candles()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        rc = self._run("AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] >= 0
        assert env["errors"] == []

    def test_data_carries_ideas_list(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "ideas" in env["data"]
        assert isinstance(env["data"]["ideas"], list)
        assert env["count"] == len(env["data"]["ideas"])

    def test_default_fields_per_idea(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        if env["data"]["ideas"]:
            for idea in env["data"]["ideas"]:
                for k in ("pair", "direction", "conviction", "version", "entry_price", "stop_loss"):
                    assert k in idea, f"missing {k} in default idea fields"

    def test_full_includes_reasoning(self, capsys, monkeypatch):
        self._run("AAPL", "--json", "--full", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        if env["data"]["ideas"]:
            for idea in env["data"]["ideas"]:
                assert "reasoning" in idea

    def test_help_lines_reference_risk_engine(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "risk-engine" in joined

    def test_no_data_returns_empty_state(self, capsys, monkeypatch):
        self._run("ZZZZ", "--json", candles=[], monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None or env["data"].get("ideas") == []
        assert env["errors"]


class TestRunAllL3Envelope:
    def _run(self, *argv, candles_by_ticker=None, monkeypatch):
        mod = _load_skill_run("run-all-l3")
        if candles_by_ticker is None:
            candles_by_ticker = {"AAPL": _make_candles(), "SPY": _make_candles(seed=99)}

        def fake_fetch(ticker, **_):
            return candles_by_ticker.get(ticker, [])

        monkeypatch.setattr(mod, "fetch_ohlc", fake_fetch)
        monkeypatch.setattr(
            mod,
            "fetch_regime",
            lambda: {"regime": {}, "regime_note": "test", "errors": [], "incomplete": False},
        )
        monkeypatch.setattr(mod, "metadata_for", lambda t: {})
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        rc = self._run("AAPL", "SPY", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 2
        assert env["errors"] == []

    def test_data_carries_tickers_macro_interval_period(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "interval" in env["data"]
        assert "period" in env["data"]
        assert "tickers" in env["data"]
        assert "macro" in env["data"]
        assert "AAPL" in env["data"]["tickers"]

    def test_per_ticker_ideas_count(self, capsys, monkeypatch):
        self._run("AAPL", "SPY", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        for ticker, entry in env["data"]["tickers"].items():
            if "ideas_count" in entry:
                assert entry["ideas_count"] >= 0
                assert entry["fired_strategies"] >= 0

    def test_top_caps_ideas_per_ticker(self, capsys, monkeypatch):
        self._run("AAPL", "SPY", "--json", "--top=1", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        for ticker, entry in env["data"]["tickers"].items():
            if "strategies" in entry:
                for strat_name, strat in entry["strategies"].items():
                    if "ideas" in strat:
                        assert len(strat["ideas"]) <= 1

    def test_fired_only_drops_empty_strategies(self, capsys, monkeypatch):
        self._run("AAPL", "--json", "--fired-only", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        for ticker, entry in env["data"]["tickers"].items():
            if "strategies" in entry:
                for strat_name, strat in entry["strategies"].items():
                    if "ideas" in strat:
                        assert len(strat["ideas"]) > 0

    def test_help_lines_reference_top_and_fields(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "--top" in joined
        assert "--fired-only" in joined or "--fields" in joined

    def test_no_tickers_returns_empty_state(self, capsys, monkeypatch):
        rc = self._run("--json", monkeypatch=monkeypatch)
        assert rc == 2
        out = capsys.readouterr().out
        env = json.loads(out)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["errors"]
