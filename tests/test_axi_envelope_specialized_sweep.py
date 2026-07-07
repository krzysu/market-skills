"""AXI envelope tests for the specialized sweep (ADR-0004 phase 2d).

Pins the on-the-wire envelope shape for the 6 specialized skills
migrated in phase 2d: market-snapshot, market-overview, market-movers,
market-basis, market-macro, market-valuation. market-snapshot,
market-movers, and market-basis are ticker-based; market-macro and
market-valuation are ticker-agnostic singletons.
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
    run_path = os.path.join(REPO_ROOT, "skills", skill_name, "scripts", "run.py")
    spec = importlib.util.spec_from_file_location(f"{skill_name.replace('-', '_')}_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(mod, *argv):
    full = ["run.py", *argv]
    with patch.object(sys, "argv", full):
        try:
            return mod.main()
        except SystemExit as e:
            return e.code


def _envelope_of(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out)


class TestMarketSnapshotEnvelope:
    def _run(self, *argv, candles=None, monkeypatch):
        mod = _load_skill_run("market-snapshot")
        if candles is None:
            candles = _make_candles()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1

    def test_default_fields(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "ticker" in env["data"]
        assert "ma_alignment" in env["data"]
        assert "agrees_with_idea" in env["data"]


class TestMarketOverviewEnvelope:
    def _run(self, *argv, monkeypatch):
        mod = _load_skill_run("market-overview")
        monkeypatch.setattr(
            mod,
            "scan",
            lambda tickers, **kw: (
                [{"ticker": "SPY", "price": 500.0, "unified_score": 75.0, "action": "BUY"}],
                [],
            ),
        )
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("SPY", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1

    def test_default_fields(self, capsys, monkeypatch):
        self._run("SPY", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        for r in env["data"]["ranked"]:
            assert set(r.keys()) == {"ticker", "price", "unified_score", "action"}

    def test_all_errors_returns_empty_state(self, capsys, monkeypatch):
        mod = _load_skill_run("market-overview")
        monkeypatch.setattr(
            mod,
            "scan",
            lambda tickers, **kw: ([], [{"ticker": "X", "error": "no data"}]),
        )
        _run_cli(mod, "X", "--json")
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["errors"]


class TestMarketMoversEnvelope:
    def _run(self, *argv, monkeypatch):
        mod = _load_skill_run("market-movers")

        class _FakeLib:
            @staticmethod
            def fetch_movers(**kw):
                return {
                    "gainers": [{"symbol": "AAA"}],
                    "losers": [{"symbol": "BBB"}],
                    "trending": [{"symbol": "CCC"}],
                    "categories": [],
                    "fetched_at": "2026-07-07T12:00:00Z",
                }

        monkeypatch.setattr(mod, "load_lib_for_script", lambda _path: _FakeLib)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 3

    def test_default_fields(self, capsys, monkeypatch):
        self._run("--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "gainers" in env["data"]
        assert "losers" in env["data"]
        assert "trending" in env["data"]


class TestMarketBasisEnvelope:
    def _run(self, *argv, candles=None, monkeypatch):
        mod = _load_skill_run("market-basis")
        if candles is None:
            candles = _make_candles()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
        monkeypatch.setattr(mod, "fetch_funding_rate", lambda *a, **kw: None)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("BTC/USDT", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1

    def test_default_fields(self, capsys, monkeypatch):
        self._run("BTC/USDT", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "ticker" in env["data"]


class TestMarketMacroEnvelope:
    def _run(self, *argv, monkeypatch):
        mod = _load_skill_run("market-macro")
        from analysis.macro import fetch_regime

        def fake_fetch_regime(**kwargs):
            return {
                "timestamp": "2026-07-07T12:00:00Z",
                "inputs": {"fng": 50, "vix": 20, "dxy": 100, "us10y": 4.0, "btc_dominance": 50.0},
                "regime": {"risk_appetite": "NEUTRAL", "liquidity": "EASY", "sentiment": "NEUTRAL"},
                "errors": [],
                "incomplete": False,
                "regime_note": "Stable regime",
            }

        monkeypatch.setattr(fetch_regime, "__defaults__", (None,) * 0)
        from analysis import macro as macro_mod

        monkeypatch.setattr(macro_mod, "fetch_regime", fake_fetch_regime)
        monkeypatch.setattr(macro_mod, "clear_cache", lambda: None)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}

    def test_default_fields(self, capsys, monkeypatch):
        self._run("--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "regime" in env["data"]
        assert "regime_note" in env["data"]


class TestMarketValuationEnvelope:
    def _run(self, *argv, monkeypatch):
        mod = _load_skill_run("market-valuation")
        from analysis import valuation as val_mod

        def fake_fetch_valuation(**kwargs):
            return {
                "timestamp": "2026-07-07T12:00:00Z",
                "inputs": {"sp500": 5500.0, "cape": 30.0, "cape_mean_50y": 17.0, "cape_std_50y": 5.0},
                "regime": {"regime": "ELEVATED", "cape_zscore": 2.6},
                "errors": [],
                "incomplete": False,
                "regime_note": "SP500 elevated above 50y mean",
            }

        monkeypatch.setattr(val_mod, "fetch_valuation", fake_fetch_valuation)
        monkeypatch.setattr(val_mod, "clear_cache", lambda: None)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}

    def test_default_fields(self, capsys, monkeypatch):
        self._run("--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "regime" in env["data"]
