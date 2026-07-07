"""AXI envelope tests for the diagnostics sweep (ADR-0004 phase 2f).

Pins the on-the-wire envelope shape for the 3 diagnostic skills
migrated in phase 2f: bug-scan, l3-conviction-scan, daily-trade-pick.
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


class TestBugScanEnvelope:
    def _run(self, *argv, monkeypatch):
        mod = _load_skill_run("bug-scan")

        class _FakeLib:
            @staticmethod
            def run_scan(**kw):
                return {
                    "ok": True,
                    "findings": [{"shape": "ghost-classification", "ticker": "AAPL"}],
                    "scan_summary": {"total_tickers": 1},
                    "tickers_scanned": ["AAPL"],
                }

            @staticmethod
            def default_state_path():
                return "/tmp/state.json"

        monkeypatch.setattr(mod, "load_lib_for_script", lambda _p: _FakeLib)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1

    def test_default_fields(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "findings" in env["data"]
        assert "scan_summary" in env["data"]

    def test_full_includes_all(self, capsys, monkeypatch):
        self._run("AAPL", "--json", "--full", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "findings" in env["data"]
        assert "tickers_scanned" in env["data"]

    def test_fields_projection_works(self, capsys, monkeypatch):
        self._run("AAPL", "--json", "--fields=findings", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "findings" in env["data"]

    def test_help_references_next_step(self, capsys, monkeypatch):
        self._run("AAPL", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "bug-scan" in joined

    def test_error_returns_empty_state(self, capsys, monkeypatch):
        mod = _load_skill_run("bug-scan")

        class _FakeLib:
            @staticmethod
            def run_scan(**kw):
                return {"ok": False, "error": "no findings available", "findings": []}

            @staticmethod
            def default_state_path():
                return "/tmp/state.json"

        monkeypatch.setattr(mod, "load_lib_for_script", lambda _p: _FakeLib)
        rc = _run_cli(mod, "AAPL", "--json")
        assert rc == 1
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["errors"]


class TestL3ConvictionScanEnvelope:
    def _run(self, *argv, monkeypatch):
        mod = _load_skill_run("l3-conviction-scan")

        class _FakeLib:
            @staticmethod
            def scan(baskets, **kw):
                return [{"ticker": "AAPL", "strategy": "trend-follow", "conviction": 4, "narrative": "..."}]

            @staticmethod
            def render_json(rows, **kwargs):
                return {
                    "baskets": kwargs.get("baskets", []),
                    "interval": kwargs.get("interval", "1d"),
                    "period": kwargs.get("period", "1y"),
                    "total": len(rows),
                    "ideas": [
                        {"ticker": r["ticker"], "strategy": r["strategy"], "conviction": r["conviction"]}
                        for r in rows
                    ],
                }

        monkeypatch.setattr(mod, "load_lib_for_script", lambda _p: _FakeLib)
        return _run_cli(mod, *argv)

    def test_envelope_keys(self, capsys, monkeypatch):
        self._run("tier_1", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1

    def test_default_fields(self, capsys, monkeypatch):
        self._run("tier_1", "--json", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "ideas" in env["data"]
        assert "baskets" in env["data"]
        assert "total" in env["data"]

    def test_full_includes_all(self, capsys, monkeypatch):
        self._run("tier_1", "--json", "--full", monkeypatch=monkeypatch)
        env = _envelope_of(capsys)
        assert "ideas" in env["data"]

    def test_empty_returns_empty_state(self, capsys, monkeypatch):
        mod = _load_skill_run("l3-conviction-scan")

        class _FakeLib:
            @staticmethod
            def scan(baskets, **kw):
                return []

            @staticmethod
            def render_json(rows, **kw):
                return {"ideas": [], "baskets": [], "total": 0}

        monkeypatch.setattr(mod, "load_lib_for_script", lambda _p: _FakeLib)
        _run_cli(mod, "tier_1", "--json")
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["help"]


class TestDailyTradePickEnvelope:
    def _run(self, *argv, monkeypatch, tmp_path):
        journal = tmp_path / "picks.json"
        journal.write_text(
            json.dumps(
                [
                    {
                        "type": "scan",
                        "id": "s1",
                        "created_ts": "2026-07-01T00:00:00Z",
                        "ideas": [
                            {
                                "ticker": "AAPL",
                                "pair": "AAPL",
                                "direction": "long",
                                "conviction": 4,
                                "picked": True,
                                "status": "closed",
                                "outcome_verdict": "hit",
                                "actual_return_pct": 5.0,
                            }
                        ],
                    }
                ]
            )
        )
        monkeypatch.setenv("MARKET_SKILLS_DAILY_TRADE_PICK_PATH", str(journal))
        spec = importlib.util.spec_from_file_location(
            "dtp_run_under_test",
            os.path.join(REPO_ROOT, "skills", "daily-trade-pick", "scripts", "analyze_journal.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(sys, "argv", ["analyze_journal.py", *argv]):
            try:
                return mod.main(), mod
            except SystemExit as e:
                return e.code, mod

    def test_envelope_keys(self, capsys, monkeypatch, tmp_path):
        self._run("--json", monkeypatch=monkeypatch, tmp_path=tmp_path)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1

    def test_default_fields(self, capsys, monkeypatch, tmp_path):
        self._run("--json", monkeypatch=monkeypatch, tmp_path=tmp_path)
        env = _envelope_of(capsys)
        assert "total_ideas" in env["data"]
        assert "hit_rate" in env["data"]
        assert "by_ticker" in env["data"]

    def test_full_includes_all(self, capsys, monkeypatch, tmp_path):
        self._run("--json", "--full", monkeypatch=monkeypatch, tmp_path=tmp_path)
        env = _envelope_of(capsys)
        assert "by_ticker" in env["data"]
        assert "by_direction" in env["data"]
        assert "by_conviction" in env["data"]

    def test_empty_journal_returns_empty_state(self, capsys, monkeypatch, tmp_path):
        journal = tmp_path / "picks.json"
        journal.write_text("[]")
        monkeypatch.setenv("MARKET_SKILLS_DAILY_TRADE_PICK_PATH", str(journal))
        spec = importlib.util.spec_from_file_location(
            "dtp_run_under_test",
            os.path.join(REPO_ROOT, "skills", "daily-trade-pick", "scripts", "analyze_journal.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(sys, "argv", ["analyze_journal.py", "--json"]):
            mod.main()
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert env["data"] is None
        assert env["help"]
