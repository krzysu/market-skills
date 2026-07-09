"""Tests for the AXI output envelope and helpers (ADR-0004 phase 0).

These tests pin the shape of `analysis.output.envelope()` and its
companions so the phase-1 pilot (market-rsi, market-trend-quality,
strategy-trend-follow, run-all-l3) can rely on the contract.

The fixtures here are intentionally framework-light: they do not
shell out to a skill, they exercise the helper directly. Per-skill
envelope coverage lands in phase 1 when those scripts are migrated
(see `tests/test_axi_envelope.py::TestPilotSkills` placeholder).
"""

import json
import os
import tempfile
from unittest.mock import patch

from analysis.contracts import AXIEnvelope
from analysis.output import (
    DEFAULT_TRUNCATE_LIMIT,
    emit_envelope_json,
    empty_state,
    envelope,
    project_fields,
    render_home_view,
    truncate,
    write_state_cache,
)
from analysis.toon import toon_dump


class TestEnvelopeShape:
    def test_minimum_envelope_has_all_keys(self):
        env = envelope({"x": 1})
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["data"] == {"x": 1}
        assert env["count"] is None
        assert env["errors"] == []
        assert env["help"] == []

    def test_envelope_with_count(self):
        env = envelope([1, 2, 3], count=3)
        assert env["data"] == [1, 2, 3]
        assert env["count"] == 3

    def test_errors_and_help_are_lists_not_none(self):
        env = envelope(None, errors=None, help=None)
        assert env["errors"] == []
        assert env["help"] == []
        env2 = envelope(None, errors=["boom"], help=["run x --json"])
        assert env2["errors"] == ["boom"]
        assert env2["help"] == ["run x --json"]

    def test_envelope_accepts_iterables(self):
        env = envelope({}, errors=("a", "b"), help=("c",))
        assert env["errors"] == ["a", "b"]
        assert env["help"] == ["c"]

    def test_envelope_satisfies_typed_dict(self):
        env = envelope({"k": "v"}, count=1, errors=[], help=[])
        typed: AXIEnvelope = env
        assert typed["data"] == {"k": "v"}
        assert typed["count"] == 1

    def test_empty_state_shape(self):
        e = empty_state()
        assert e == {"data": None, "count": 0, "errors": [], "help": []}
        e2 = empty_state(help=["next"], errors=["e1"])
        assert e2["count"] == 0
        assert e2["help"] == ["next"]
        assert e2["errors"] == ["e1"]


class TestProjectFields:
    def test_none_returns_input(self):
        assert project_fields({"a": 1, "b": 2}, None) == {"a": 1, "b": 2}

    def test_all_string_returns_input(self):
        assert project_fields({"a": 1, "b": 2}, "all") == {"a": 1, "b": 2}

    def test_empty_string_returns_input(self):
        assert project_fields({"a": 1, "b": 2}, "") == {"a": 1, "b": 2}

    def test_comma_separated_string(self):
        assert project_fields({"a": 1, "b": 2, "c": 3}, "a,c") == {"a": 1, "c": 3}

    def test_list_of_fields(self):
        assert project_fields({"a": 1, "b": 2, "c": 3}, ["a", "b"]) == {"a": 1, "b": 2}

    def test_unknown_fields_silently_skipped(self):
        assert project_fields({"a": 1}, ["a", "missing"]) == {"a": 1}

    def test_non_dict_input_returned_unchanged(self):
        assert project_fields([1, 2, 3], ["a"]) == [1, 2, 3]
        assert project_fields("hello", ["a"]) == "hello"

    def test_envelope_projects_data_not_envelope_keys(self):
        env = envelope({"a": 1, "b": 2, "c": 3}, count=1, fields="a,c")
        assert env["data"] == {"a": 1, "c": 3}
        assert env["count"] == 1


class TestTruncate:
    def test_none_passthrough(self):
        assert truncate(None) is None

    def test_non_string_passthrough(self):
        assert truncate(42) == 42
        assert truncate([1, 2]) == [1, 2]

    def test_short_string_unchanged(self):
        s = "short"
        assert truncate(s) == s

    def test_long_string_truncated_with_hint(self):
        s = "x" * (DEFAULT_TRUNCATE_LIMIT + 50)
        out = truncate(s)
        assert len(out) < len(s) + 100
        assert out.startswith("x" * DEFAULT_TRUNCATE_LIMIT)
        assert "(truncated," in out
        assert "chars total" in out
        assert "use --full" in out

    def test_long_string_truncated_no_hint(self):
        s = "x" * (DEFAULT_TRUNCATE_LIMIT + 50)
        out = truncate(s, hint=False)
        assert "(truncated," not in out
        assert out.endswith("...")

    def test_custom_limit(self):
        s = "abcdefghij"
        assert truncate(s, limit=5) == "abcde ... (truncated, 10 chars total - use --full to see complete body)"

    def test_exact_length_unchanged(self):
        s = "x" * DEFAULT_TRUNCATE_LIMIT
        assert truncate(s) == s


class TestToonDump:
    def test_round_trip_with_toon_load(self):
        from analysis.toon import toon_load

        obj = {"a": 1, "b": [1, 2, 3], "c": None}
        out = toon_dump(obj)
        assert toon_load(out) == obj

    def test_handles_non_serializable_via_default_str(self):
        from datetime import datetime

        ts = datetime(2026, 7, 7, 12, 0, 0)
        out = toon_dump({"ts": ts})
        assert "2026" in out

    def test_smaller_than_indent2_json(self):
        obj = {
            "data": {
                "ticker": "AAPL",
                "rsi_14": 42,
                "signal": "NEUTRAL",
                "score": 0,
            },
            "count": 1,
            "errors": [],
            "help": [
                "Run market-ema AAPL --json for trend context",
                "Pass --full or --fields=<csv> to project",
            ],
        }
        j = json.dumps(obj, indent=2, default=str)
        t = toon_dump(obj)
        assert len(t.encode("utf-8")) < len(j.encode("utf-8"))


class TestEmitEnvelopeJson:
    def test_prints_valid_envelope_json(self, capsys):
        emit_envelope_json({"a": 1}, count=1, help=["try x --json"])
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["data"] == {"a": 1}
        assert parsed["count"] == 1
        assert parsed["help"] == ["try x --json"]

    def test_toon_flag_emits_toon_payload(self, capsys):
        from analysis.toon import toon_load

        emit_envelope_json({"a": 1}, count=1, toon=True)
        out = capsys.readouterr().out
        assert toon_load(out) == {"data": {"a": 1}, "count": 1, "errors": [], "help": []}

    def test_fields_projection_applied(self, capsys):
        emit_envelope_json({"a": 1, "b": 2, "c": 3}, count=1, fields=["a", "c"])
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["data"] == {"a": 1, "c": 3}


class TestHomeView:
    def test_no_cache_falls_back_to_hint(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                view = render_home_view("market-rsi")
        assert "no cached state yet" in view
        assert "market-rsi --json" in view

    def test_no_cache_uses_custom_command_hint(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                view = render_home_view("market-rsi", command_hint="market-rsi AAPL --json")
        assert "market-rsi AAPL --json" in view

    def test_cache_renders_summary_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                write_state_cache(
                    "market-rsi",
                    {
                        "summary": "AAPL rsi=42 NEUTRAL",
                        "timestamp": "2026-07-07T14:30:00Z",
                    },
                )
                view = render_home_view("market-rsi")
        assert "AAPL rsi=42 NEUTRAL" in view
        assert "2026-07-07T14:30:00Z" in view

    def test_cache_uses_narrative_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                write_state_cache(
                    "market-trend-quality",
                    {"narrative": "HEALTHY_UPTREND", "timestamp": "2026-07-07T15:00:00Z"},
                )
                view = render_home_view("market-trend-quality")
        assert "HEALTHY_UPTREND" in view

    def test_cache_uses_ticker_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                write_state_cache(
                    "market-trend",
                    {"ticker": "HYPEUSD", "timestamp": "2026-07-07T15:00:00Z"},
                )
                view = render_home_view("market-trend")
        assert "HYPEUSD" in view

    def test_corrupt_cache_falls_back_to_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "market-skills", "market-rsi_last.json")
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as f:
                f.write("not json {{{")
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                view = render_home_view("market-rsi")
        assert "no cached state yet" in view


class TestWriteStateCache:
    def test_writes_under_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                write_state_cache("foo", {"k": "v"})
            cache_path = os.path.join(tmp, "market-skills", "foo_last.json")
            assert os.path.exists(cache_path)
            with open(cache_path) as f:
                assert json.load(f) == {"k": "v"}

    def test_silent_on_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            readonly = os.path.join(tmp, "readonly")
            os.makedirs(readonly, mode=0o555)
            try:
                with patch.dict(os.environ, {"XDG_DATA_HOME": readonly}):
                    write_state_cache("foo", {"k": "v"})
            finally:
                os.chmod(readonly, 0o755)
