"""End-to-end tests for the home-view wiring (ADR-0004 phase 3).

These tests verify the no-arg home view behavior for representative
skills: one L1, one L2, one L3, one batch runner, and one diagnostic.
They run the actual `main()` of each skill with monkeypatched data
so we exercise the full main() flow including the cache write/read
loop.

The helpers themselves (parse_axi_flags, cache_run_result,
maybe_render_home_view, render_home_view, skill_name_from_file) are
pinned in `tests/test_home_view.py` — this file is the end-to-end
side, proving the wiring works for real scripts.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_skill_run(skill_name: str):
    path = REPO_ROOT / "skills" / skill_name / "scripts" / "run.py"
    spec = importlib.util.spec_from_file_location(f"skill_{skill_name}_run", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n: int = 220, seed: int = 42) -> list[list]:
    random.seed(seed)
    candles = []
    price = 100.0
    for i in range(n):
        price *= 1 + random.gauss(0, 0.01)
        open_p = price * (1 + random.gauss(0, 0.003))
        high_p = max(open_p, price) * (1 + abs(random.gauss(0, 0.003)))
        low_p = min(open_p, price) * (1 - abs(random.gauss(0, 0.003)))
        candles.append(
            [
                i * 86400,
                open_p,
                high_p,
                low_p,
                price,
                random.randint(100, 1000),
            ]
        )
    return candles


def _run_cli(mod, *argv, monkeypatch=None) -> int | None:
    monkeypatch.setattr(sys, "argv", ["run.py", *argv]) if monkeypatch else None
    try:
        return mod.main()
    except SystemExit as e:
        return e.code


def _envelope_of(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestHomeViewEndToEnd:
    def test_market_rsi_run_then_home_view(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("market-rsi")
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())

        rc = _run_cli(mod, "AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["count"] == 1

        cache_path = tmp_path / "market-skills" / "market-rsi_last.json"
        assert cache_path.exists()
        with open(cache_path) as f:
            cache_data = json.load(f)
        assert cache_data["ticker"] == "AAPL"
        assert "summary" in cache_data

        rc = _run_cli(mod, monkeypatch=monkeypatch)
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "AAPL" in out
        assert "try:" in out
        assert "market-rsi" in out

    def test_market_rsi_json_no_ticker_returns_envelope(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("market-rsi")
        rc = _run_cli(mod, "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert "no ticker" in env["errors"][0]
        assert "market-rsi <TICKER> --json" in env["help"][0]

    def test_market_rsi_error_does_not_cache(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("market-rsi")
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: [])
        rc = _run_cli(mod, "AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["count"] == 0

        cache_path = tmp_path / "market-skills" / "market-rsi_last.json"
        assert not cache_path.exists()

    def test_market_trend_quality_run_then_home_view(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("market-trend-quality")
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())

        rc = _run_cli(mod, "AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        _ = capsys.readouterr().out

        rc = _run_cli(mod, monkeypatch=monkeypatch)
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "market-trend-quality" in out
        assert "try:" in out

    def test_strategy_trend_follow_run_then_home_view(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("strategy-trend-follow")
        from analysis import strategy_runner

        monkeypatch.setattr(strategy_runner, "fetch_ohlc", lambda *a, **kw: _make_candles())

        from analysis import watchlist as wl

        monkeypatch.setattr(wl, "metadata_for", lambda t: {"asset_class": "crypto"})

        class _FakeLib:
            @staticmethod
            def analyze(candles, **kwargs):
                return {
                    "ideas": [
                        {
                            "pair": "AAPL",
                            "direction": "long",
                            "conviction": 4,
                            "version": "v3",
                            "entry_price": 100.0,
                            "stop_loss": 95.0,
                            "reasoning": "test reasoning",
                        }
                    ],
                    "narrative": "test narrative",
                }

        monkeypatch.setattr(strategy_runner, "load_lib_for_script", lambda _p: _FakeLib)

        rc = _run_cli(mod, "AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        _ = capsys.readouterr().out

        rc = _run_cli(mod, monkeypatch=monkeypatch)
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "strategy-trend-follow" in out
        assert "try:" in out

    def test_run_all_l2_run_then_home_view(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("run-all-l2")
        monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: _make_candles())

        from analysis import skill_loader as sl_mod

        class _FakeLib:
            @staticmethod
            def analyze(t, candles, **kw):
                return {
                    "skills": {
                        "market-trend-quality": {
                            "pattern": {
                                "present": True,
                                "classification": "HEALTHY_UPTREND",
                                "confidence": 4,
                                "max_confidence": 5,
                            }
                        }
                    },
                    "narrative": "test",
                }

        monkeypatch.setattr(sl_mod, "load_lib_for_script", lambda _p: _FakeLib)
        monkeypatch.setattr(mod, "load_lib_for_script", lambda _p: _FakeLib)

        rc = _run_cli(mod, "AAPL", "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        _ = capsys.readouterr().out

        rc = _run_cli(mod, "--json", monkeypatch=monkeypatch)
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert "no ticker" in env["errors"][0]

    def test_bug_scan_no_args_shows_home_view(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_skill_run("bug-scan")
        rc = _run_cli(mod, monkeypatch=monkeypatch)
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "no cached state yet" in out
        assert "bug-scan" in out
