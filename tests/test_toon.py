"""End-to-end tests for the TOON encoder (ADR-0004 phase 5).

Pins:
  - `toon_dump` / `toon_load` round-trip for the AXI envelope shapes
    (top-level dict, nested dicts, primitive arrays, tabular arrays).
  - A representative AXI envelope is meaningfully smaller in TOON
    than in indent-2 JSON.
  - The `--toon` flag round-trips through `parse_axi_flags`.
  - `market-state --toon --json` emits a TOON payload that
    `toon_load` decodes back to the dashboard dict.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from analysis.toon import toon_dump, toon_load

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(cache_dir: Path, skill: str, payload: dict) -> None:
    (cache_dir / f"{skill}_last.json").write_text(json.dumps(payload, default=str))


class TestToonRoundTrip:
    def test_simple_envelope(self):
        env = {
            "data": {"ticker": "AAPL", "rsi_14": 42, "signal": "NEUTRAL"},
            "count": 1,
            "errors": [],
            "help": ["Run market-ema AAPL --json for trend context"],
        }
        assert toon_load(toon_dump(env)) == env

    def test_primitive_array_with_colons(self):
        env = {
            "data": {"x": 1},
            "count": 0,
            "errors": ["missing cache: movers", "missing cache: watchlist", "missing cache: notes"],
            "help": ["Refresh stale sources with <skill> --json"],
        }
        assert toon_load(toon_dump(env)) == env

    def test_nested_object(self):
        env = {
            "data": {
                "summary": "regime: RISK_ON/EASY/GREED",
                "freshness": {"regime": "4h ago", "valuation": "5h ago"},
                "sources_cached": 3,
            },
            "count": 3,
            "errors": [],
            "help": [],
        }
        assert toon_load(toon_dump(env)) == env

    def test_tabular_array(self):
        env = {
            "data": {
                "ideas": [
                    {"pair": "AAPL", "direction": "long", "conviction": 4, "entry_price": 100.0},
                    {"pair": "GOOGL", "direction": "long", "conviction": 3, "entry_price": 150.0},
                    {"pair": "MSFT", "direction": "short", "conviction": 4, "entry_price": 410.0},
                ]
            },
            "count": 3,
            "errors": [],
            "help": ["Run risk-engine"],
        }
        assert toon_load(toon_dump(env)) == env

    def test_nested_tabular(self):
        env = {
            "data": {
                "conviction": {
                    "total": 2,
                    "top_ideas": [
                        {"ticker": "<PRIVATE_PERP>USD", "conviction": 5},
                        {"ticker": "SOLUSD", "conviction": 4},
                    ],
                }
            },
            "count": 2,
            "errors": [],
            "help": [],
        }
        assert toon_load(toon_dump(env)) == env

    def test_empty_collections(self):
        env = {"data": {}, "count": 0, "errors": [], "help": []}
        assert toon_load(toon_dump(env)) == env

    def test_null_values(self):
        env = {"data": {"a": None, "b": None}, "count": 0, "errors": [], "help": []}
        assert toon_load(toon_dump(env)) == env

    def test_boolean_values(self):
        env = {"data": {"incomplete": True, "complete": False}, "count": 0, "errors": [], "help": []}
        assert toon_load(toon_dump(env)) == env

    def test_string_with_quotes_and_backslashes(self):
        env = {"data": {"text": 'He said "hi" and used \\ backslash'}, "count": 0, "errors": [], "help": []}
        assert toon_load(toon_dump(env)) == env

    def test_string_with_literal_escape_sequences(self):
        env = {
            "data": {"text": "line1\\nline2\\rline3\\tend"},
            "count": 0,
            "errors": [],
            "help": [],
        }
        assert toon_load(toon_dump(env)) == env


class TestToonSize:
    def test_dashboard_default_smaller_than_json(self):
        dashboard = {
            "data": {
                "summary": "regime: RISK_ON/EASY/GREED, valuation: OVEREXTENDED",
                "freshness": {
                    "regime": "4h ago",
                    "valuation": "5h ago",
                    "movers": "no cache",
                    "watchlist": "no cache",
                    "conviction": "3h ago",
                    "notes": "no cache",
                },
                "sources_cached": 3,
                "sources_total": 6,
            },
            "count": 3,
            "errors": ["missing cache: movers", "missing cache: watchlist", "missing cache: notes"],
            "help": [
                "Refresh stale sources with <skill> --json before relying on them",
                "Run market-state --json --full",
                "Pass --fields=<csv> to project or --full for the complete payload",
            ],
        }
        j = json.dumps(dashboard, indent=2)
        t = toon_dump(dashboard)
        assert len(t.encode()) < len(j.encode())

    def test_tabular_dashboard_much_smaller(self):
        ideas = [
            {
                "pair": "AAPL",
                "direction": "long",
                "conviction": 4,
                "version": "v3",
                "entry_price": 100.0,
                "stop_loss": 95.0,
            },
            {
                "pair": "GOOGL",
                "direction": "long",
                "conviction": 3,
                "version": "v2",
                "entry_price": 150.0,
                "stop_loss": 145.0,
            },
            {
                "pair": "MSFT",
                "direction": "short",
                "conviction": 4,
                "version": "v4",
                "entry_price": 410.0,
                "stop_loss": 420.0,
            },
            {
                "pair": "NVDA",
                "direction": "long",
                "conviction": 5,
                "version": "v3",
                "entry_price": 800.0,
                "stop_loss": 750.0,
            },
        ]
        _ = ideas  # used below
        env = {
            "data": {"ticker": "AAPL", "ideas": ideas, "narrative": "4 ideas"},
            "count": 4,
            "errors": [],
            "help": ["Run risk-engine", "Run run-all-l3"],
        }
        j = json.dumps(env, indent=2)
        t = toon_dump(env)
        ratio = len(t.encode()) / len(j.encode())
        assert ratio < 0.55, f"tabular TOON should be <55% of JSON size, got {ratio:.2f}"


class TestParseAxiFlagsToon:
    def test_default_is_off(self):
        from analysis.output import parse_axi_flags

        fields, full, toon, rest = parse_axi_flags(["AAPL", "--json"])
        assert toon is False
        assert rest == ["AAPL", "--json"]

    def test_toon_flag_sets_true(self):
        from analysis.output import parse_axi_flags

        fields, full, toon, rest = parse_axi_flags(["AAPL", "--toon", "--json"])
        assert toon is True
        assert rest == ["AAPL", "--json"]

    def test_toon_with_fields_and_full(self):
        from analysis.output import parse_axi_flags

        fields, full, toon, rest = parse_axi_flags(["AAPL", "--toon", "--full", "--fields=ticker,signal"])
        assert toon is True
        assert full is True
        assert fields == "ticker,signal"
        assert rest == ["AAPL"]


class TestMarketStateToonMode:
    def test_market_state_toon_round_trips(self, tmp_path, monkeypatch, capsys):
        cache_dir = tmp_path / "market-skills"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        _write(
            cache_dir,
            "market-macro",
            {
                "cached_at": _now_iso(),
                "regime": {"risk_appetite": "RISK_ON", "liquidity": "EASY", "sentiment": "NEUTRAL"},
                "regime_note": "x",
                "incomplete": False,
            },
        )

        run_mod = _load("market_state_run_toon", REPO_ROOT / "skills" / "market-state" / "scripts" / "run.py")
        monkeypatch.setattr(sys, "argv", ["run.py", "--toon", "--json", "--full"])
        run_mod.main()
        out = capsys.readouterr().out
        decoded = toon_load(out)
        assert decoded["count"] == 1
        assert decoded["data"]["sources_cached"] == 1
        assert decoded["data"]["sources"]["regime"]["risk_appetite"] == "RISK_ON"

    def test_market_state_toon_smaller_than_json(self, tmp_path, monkeypatch, capsys):
        cache_dir = tmp_path / "market-skills"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        _write(
            cache_dir,
            "market-macro",
            {
                "cached_at": _now_iso(),
                "regime": {"risk_appetite": "RISK_ON", "liquidity": "EASY", "sentiment": "NEUTRAL"},
                "regime_note": "Risk-on tape with easy liquidity",
                "incomplete": False,
            },
        )
        _write(
            cache_dir,
            "l3-conviction-scan",
            {
                "cached_at": _now_iso(),
                "baskets": ["tier_1"],
                "total": 3,
                "ideas": [
                    {"ticker": "<PERP>USD", "strategy": "trend-follow", "conviction": 5, "narrative": "strong uptrend"},
                    {"ticker": "SOLUSD", "strategy": "mean-reversion", "conviction": 4, "narrative": "oversold"},
                    {
                        "ticker": "BTCUSD",
                        "strategy": "breakout-confirm",
                        "conviction": 3,
                        "narrative": "breakout above 70k",
                    },
                ],
                "summary": "3 ideas",
            },
        )

        run_mod = _load("market_state_run_size", REPO_ROOT / "skills" / "market-state" / "scripts" / "run.py")

        monkeypatch.setattr(sys, "argv", ["run.py", "--json", "--full"])
        run_mod.main()
        json_out = capsys.readouterr().out
        json_size = len(json_out.encode("utf-8"))

        monkeypatch.setattr(sys, "argv", ["run.py", "--toon", "--json", "--full"])
        run_mod.main()
        toon_out = capsys.readouterr().out
        toon_size = len(toon_out.encode("utf-8"))

        assert toon_size < json_size
