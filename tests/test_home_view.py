"""Tests for the home-view wiring (ADR-0004 phase 3).

These tests pin the behavior of:
  - `skill_name_from_file` (derive skill name from a scripts/run.py path)
  - `cache_run_result` (add `cached_at` and write to the per-skill state path)
  - `maybe_render_home_view` (text-mode home view + JSON empty_state fallback)

End-to-end wiring (skill main() actually returns the home view when run
with no args) is covered by the per-pattern tests in
`tests/test_home_view_wiring.py` — this file pins the helper contracts
in isolation.
"""

import json
import os
import tempfile
from unittest.mock import patch

from analysis.output import (
    cache_run_result,
    maybe_render_home_view,
    skill_name_from_file,
    write_state_cache,
)


class TestSkillNameFromFile:
    def test_derives_from_standard_skills_path(self):
        assert skill_name_from_file("/repo/skills/market-rsi/scripts/run.py") == "market-rsi"

    def test_works_with_analyze_journal(self):
        assert skill_name_from_file("/repo/skills/daily-trade-pick/scripts/analyze_journal.py") == "daily-trade-pick"

    def test_falls_back_to_stem_for_non_skills_path(self):
        assert skill_name_from_file("/tmp/random_helper.py") == "random_helper"


class TestCacheRunResult:
    def test_writes_with_cached_at_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                cache_run_result(
                    "skills/market-rsi/scripts/run.py",
                    {"ticker": "AAPL", "rsi_14": 42, "summary": "AAPL rsi=42 NEUTRAL"},
                )
            cache_path = os.path.join(tmp, "market-skills", "market-rsi_last.json")
            assert os.path.exists(cache_path)
            with open(cache_path) as f:
                data = json.load(f)
            assert "cached_at" in data
            assert data["cached_at"].endswith("Z")
            assert data["ticker"] == "AAPL"
            assert data["rsi_14"] == 42
            assert data["summary"] == "AAPL rsi=42 NEUTRAL"

    def test_skips_results_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                cache_run_result(
                    "skills/market-rsi/scripts/run.py",
                    {"ticker": "AAPL", "error": "no data"},
                )
            cache_path = os.path.join(tmp, "market-skills", "market-rsi_last.json")
            assert not os.path.exists(cache_path)

    def test_skips_none_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                cache_run_result("skills/market-rsi/scripts/run.py", None)
            cache_path = os.path.join(tmp, "market-skills", "market-rsi_last.json")
            assert not os.path.exists(cache_path)


class TestMaybeRenderHomeView:
    def test_returns_false_when_ticker_present(self):
        rendered = maybe_render_home_view("skills/market-rsi/scripts/run.py", "AAPL", json_mode=True)
        assert rendered is False

    def test_json_mode_emits_empty_state_envelope(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                rendered = maybe_render_home_view("skills/market-rsi/scripts/run.py", None, json_mode=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert rendered is True
        assert parsed["count"] == 0
        assert "no ticker" in parsed["errors"][0]
        assert "market-rsi" in parsed["help"][0]
        assert "Run `market-rsi <TICKER> --json`" in parsed["help"][0]

    def test_text_mode_with_no_cache_prints_fallback(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                rendered = maybe_render_home_view("skills/market-rsi/scripts/run.py", None, json_mode=False)
        out = capsys.readouterr().out
        assert rendered is True
        assert "no cached state yet" in out
        assert "market-rsi" in out

    def test_text_mode_with_cache_prints_summary(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                write_state_cache(
                    "market-rsi",
                    {
                        "summary": "AAPL rsi=72 overbought",
                        "cached_at": "2026-07-07T14:30:00Z",
                    },
                )
                rendered = maybe_render_home_view("skills/market-rsi/scripts/run.py", None, json_mode=False)
        out = capsys.readouterr().out
        assert rendered is True
        assert "AAPL rsi=72 overbought" in out
        assert "2026-07-07T14:30:00Z" in out
        assert "try:" in out

    def test_works_with_analyze_journal_path(self):
        rendered = maybe_render_home_view("skills/daily-trade-pick/scripts/analyze_journal.py", None, json_mode=True)
        assert rendered is True
