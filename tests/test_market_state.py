"""Tests for the market-state session-start dashboard (phase 4).

Pins:
  - `compose_state()` reads each of the 6 source caches and returns
    the dashboard dict.
  - Missing sources are None with freshness "no cache".
  - Stale caches are reported with `_age_human` formatting.
  - `sources_cached` counts the populated sources correctly.
  - The slim views extract the headline fields the LLM needs without
    copying the entire payload.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cache_root = tmp_path / "market-skills"
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


@pytest.fixture
def lib():
    return _load("market_state_lib", REPO_ROOT / "skills" / "market-state" / "lib.py")


def _write(cache_dir: Path, skill: str, payload: dict) -> None:
    (cache_dir / f"{skill}_last.json").write_text(json.dumps(payload, default=str))


def _now_iso(offset_seconds: int = 0) -> str:
    delta = datetime.now(UTC).timestamp() + offset_seconds
    return datetime.fromtimestamp(delta, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestComposeStateNoCache:
    def test_no_caches_returns_zero(self, cache_dir, lib):
        state = lib.compose_state()
        assert state["sources_cached"] == 0
        assert state["sources_total"] == 6
        assert state["summary"] == "no cached state for any of 6 sources"
        assert all(state["sources"][k] is None for k in state["sources"])
        assert all(age == "no cache" for age in state["freshness"].values())

    def test_six_source_labels(self, cache_dir, lib):
        state = lib.compose_state()
        assert set(state["sources"].keys()) == set(lib.SOURCE_LABELS.values())


class TestComposeStateWithPartialCaches:
    def test_macro_only(self, cache_dir, lib):
        _write(
            cache_dir,
            "market-macro",
            {
                "cached_at": _now_iso(),
                "regime": {"risk_appetite": "RISK_ON", "liquidity": "EASY", "sentiment": "NEUTRAL"},
                "regime_note": "Risk on",
                "incomplete": False,
            },
        )
        state = lib.compose_state()
        assert state["sources_cached"] == 1
        assert state["sources"]["regime"]["risk_appetite"] == "RISK_ON"
        assert state["sources"]["valuation"] is None
        assert state["freshness"]["regime"] == "just now"
        assert state["freshness"]["valuation"] == "no cache"

    def test_all_six_populated(self, cache_dir, lib):
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
        _write(
            cache_dir,
            "market-valuation",
            {
                "cached_at": _now_iso(-3600),
                "regime": {"regime": "OVEREXTENDED", "cape_zscore": 2.6},
                "regime_note": "y",
            },
        )
        _write(
            cache_dir,
            "market-movers",
            {
                "cached_at": _now_iso(-60),
                "gainers": [1, 2, 3],
                "losers": [4, 5],
                "trending": [6, 7, 8],
            },
        )
        _write(
            cache_dir,
            "run-watchlist",
            {
                "cached_at": _now_iso(-7200),
                "scope": "tier_1",
                "summary": "3 tickers",
                "tickers_scanned": 3,
                "fired_skills_total": 5,
            },
        )
        _write(
            cache_dir,
            "l3-conviction-scan",
            {
                "cached_at": _now_iso(-1800),
                "baskets": ["tier_1"],
                "total": 3,
                "ideas": [{"ticker": "<PRIVATE_PERP>USD", "conviction": 5}],
                "summary": "3 ideas",
            },
        )
        _write(
            cache_dir,
            "market-notes",
            {
                "cached_at": _now_iso(-90000),
                "pairs": {"<PRIVATE_PERP>USD": [{"note": "x"}], "BTCUSD": [{"note": "y"}]},
                "summary": "2 active notes",
            },
        )

        state = lib.compose_state()
        assert state["sources_cached"] == 6
        assert state["freshness"]["regime"] == "just now"
        assert state["freshness"]["valuation"] == "1h ago"
        assert state["freshness"]["notes"] == "1d ago"
        assert state["sources"]["regime"]["summary"] == "RISK_ON / EASY / NEUTRAL"
        assert state["sources"]["valuation"]["summary"] == "SP500 OVEREXTENDED (z=+2.60)"
        assert state["sources"]["movers"]["gainers_count"] == 3
        assert state["sources"]["watchlist"]["fired_skills_total"] == 5
        assert state["sources"]["conviction"]["top_ideas"][0]["ticker"] == "<PRIVATE_PERP>USD"
        assert state["sources"]["notes"]["pair_count"] == 2


class TestComposeStateSlimExtractors:
    def test_macro_slim_drops_inputs(self, cache_dir, lib):
        _write(
            cache_dir,
            "market-macro",
            {
                "cached_at": _now_iso(),
                "inputs": {"vix": 18, "dxy": 100},
                "regime": {"risk_appetite": "RISK_ON", "liquidity": "EASY", "sentiment": "NEUTRAL"},
                "regime_note": "x",
                "incomplete": False,
            },
        )
        state = lib.compose_state()
        assert "inputs" not in state["sources"]["regime"]

    def test_conviction_top_ideas_capped_at_5(self, cache_dir, lib):
        ideas = [{"ticker": f"T{i}", "conviction": 3} for i in range(10)]
        _write(cache_dir, "l3-conviction-scan", {"cached_at": _now_iso(), "total": 10, "ideas": ideas})
        state = lib.compose_state()
        assert len(state["sources"]["conviction"]["top_ideas"]) == 5

    def test_movers_empty_panels_summary(self, cache_dir, lib):
        _write(cache_dir, "market-movers", {"cached_at": _now_iso(), "gainers": [], "losers": [], "trending": []})
        state = lib.compose_state()
        assert state["sources"]["movers"]["summary"] == "no panels"


class TestComposeStateCorruptCache:
    def test_corrupt_cache_treated_as_missing(self, cache_dir, lib):
        (cache_dir / "market-macro_last.json").write_text("not json {{{")
        state = lib.compose_state()
        assert state["sources"]["regime"] is None
        assert state["freshness"]["regime"] == "no cache"


class TestMarketStateRunScript:
    def test_run_with_no_args_renders_home_view(self, cache_dir, monkeypatch, capsys):
        run_mod = _load("market_state_run", REPO_ROOT / "skills" / "market-state" / "scripts" / "run.py")
        monkeypatch.setattr(sys, "argv", ["run.py"])
        run_mod.main()
        out = capsys.readouterr().out
        assert "no cached state yet" in out
        assert "market-state" in out

    def test_run_with_json_returns_envelope(self, cache_dir, monkeypatch, capsys):
        run_mod = _load("market_state_run", REPO_ROOT / "skills" / "market-state" / "scripts" / "run.py")
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

        monkeypatch.setattr(sys, "argv", ["run.py", "--json", "--full"])
        run_mod.main()
        out = capsys.readouterr().out
        env = json.loads(out)
        assert env["count"] == 1
        assert env["data"]["sources_cached"] == 1
        assert env["data"]["sources"]["regime"]["risk_appetite"] == "RISK_ON"
        assert any("missing cache" in e for e in env["errors"])

    def test_run_with_json_caches_its_own_result(self, cache_dir, monkeypatch, capsys):
        run_mod = _load("market_state_run", REPO_ROOT / "skills" / "market-state" / "scripts" / "run.py")
        monkeypatch.setattr(sys, "argv", ["run.py", "--json"])
        run_mod.main()
        capsys.readouterr()
        cache_path = cache_dir / "market-state_last.json"
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert "sources_cached" in cached
