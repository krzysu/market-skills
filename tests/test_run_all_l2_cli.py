"""CLI surface tests for run-all-l2 (scripts/run.py).

Tests the CLI wrapper (flag parsing, envelope shape, --fired-only,
--fields= projection, error handling) rather than the L2 analysis
logic (already covered in test_run_all_envelope.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from unittest.mock import patch

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _make_candles(n=200, seed=42):
    rng = random.Random(seed)
    cs = []
    p = 100.0
    for i in range(n):
        p *= 1.0 + rng.uniform(-0.005, 0.012)
        cs.append([i * 86400, p, p + 0.5, p - 0.5, p, 100000])
    return cs


def _load_mod():
    run_path = os.path.join(REPO_ROOT, "skills", "run-all-l2", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("run_all_l2_run", run_path)
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


class TestRunAllL2CliEnvelope:
    def test_single_ticker_envelope_keys(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        rc = _run_cli(mod, "AAPL", "--json")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert "tickers" in env["data"]
        assert "interval" in env["data"]
        assert "period" in env["data"]
        assert "summary" in env["data"]

    def test_single_ticker_count(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        _run_cli(mod, "AAPL", "--json")
        env = _envelope_of(capsys)
        assert env["count"] == 1

    def test_multi_ticker_count(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        _run_cli(mod, "AAPL", "SPY", "QQQ", "--json")
        env = _envelope_of(capsys)
        assert env["count"] == 3

    def test_per_ticker_skills_key(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        _run_cli(mod, "AAPL", "--json")
        env = _envelope_of(capsys)
        ticker_data = env["data"]["tickers"]["AAPL"]
        assert "skills" in ticker_data
        assert "fired_skills" in ticker_data
        assert "skill_count" in ticker_data

    def test_fired_only_drops_absent(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        _run_cli(mod, "AAPL", "--json", "--fired-only")
        env = _envelope_of(capsys)
        for ticker, entry in env["data"]["tickers"].items():
            for skill_name, skill_result in entry["skills"].items():
                pat = skill_result.get("pattern") or {}
                assert pat.get("present") is True

    def test_fields_projection(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        _run_cli(mod, "AAPL", "--json", "--fields=tickers")
        env = _envelope_of(capsys)
        assert set(env["data"].keys()) == {"tickers"}

    def test_bad_ticker_errors(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: [])
        _run_cli(mod, "ZZZZ", "--json")
        env = _envelope_of(capsys)
        assert env["errors"]

    def test_empty_ticker_list_returns_home_view(self, capsys, monkeypatch):
        mod = _load_mod()
        rc = _run_cli(mod, "--json")
        assert rc in (0, None)
        out = capsys.readouterr().out
        env = json.loads(out)
        assert env["count"] == 0

    def test_help_lines_refer_to_fired_only(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())
        _run_cli(mod, "AAPL", "--json")
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "--fired-only" in joined
