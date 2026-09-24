"""CLI surface tests for market-breadth (scripts/run.py).

Tests the CLI wrapper (flag parsing, envelope shape, error handling)
rather than the breadth math (already covered in test_breadth.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from unittest.mock import patch

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_mod():
    run_path = os.path.join(REPO_ROOT, "skills", "market-breadth", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("market_breadth_run", run_path)
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


_FAKE_PAYLOAD = {
    "window_days": 7,
    "effective_window_days": 7,
    "benchmark": "BTCUSD",
    "basket": "tier_1",
    "members": 3,
    "pct_beating": 66.7,
    "btc_return_pct": 8.0,
    "median_alt_return_pct": 10.0,
    "regime": "alt_rotation",
    "leaders": [{"ticker": "AAAUSD", "return_pct": 20.0}],
    "laggards": [{"ticker": "CCCUSD", "return_pct": -2.0}],
    "narrative": (
        "Breadth 7d: 66.7% of 3 members beating BTCUSD (BTCUSD +8.00%, median +10.00%) -> alt_rotation. "
        "Regime bands (>=60 / <=40) are uncalibrated first guesses - trust pct_beating, not the label."
    ),
    "errors": [],
}


class TestBreadthCliEnvelope:
    def test_envelope_keys(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(_FAKE_PAYLOAD))
        rc = _run_cli(mod, "--json")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] is None
        assert env["errors"] == []

    def test_default_fields(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(_FAKE_PAYLOAD))
        _run_cli(mod, "--json")
        env = _envelope_of(capsys)
        assert set(env["data"].keys()) == {
            "basket",
            "window_days",
            "members",
            "pct_beating",
            "regime",
            "narrative",
        }

    def test_full_includes_leaders_and_laggards(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(_FAKE_PAYLOAD))
        _run_cli(mod, "--json", "--full")
        env = _envelope_of(capsys)
        assert "leaders" in env["data"]
        assert "laggards" in env["data"]
        assert "benchmark" in env["data"]
        assert "btc_return_pct" in env["data"]

    def test_fields_projection(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(_FAKE_PAYLOAD))
        _run_cli(mod, "--json", "--fields=regime,pct_beating")
        env = _envelope_of(capsys)
        assert set(env["data"].keys()) == {"regime", "pct_beating"}

    def test_errors_forwarded_to_envelope(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()

        def _with_errors(**kw):
            payload = dict(_FAKE_PAYLOAD)
            payload["errors"] = ["[BREADTH CCCUSD FETCH FAILED — no daily candles returned]"]
            return payload

        monkeypatch.setattr(mod, "analyze", _with_errors)
        _run_cli(mod, "--json")
        env = _envelope_of(capsys)
        assert "[BREADTH CCCUSD FETCH FAILED" in " ".join(env["errors"])

    def test_help_lines_are_runnable_templates(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(_FAKE_PAYLOAD))
        _run_cli(mod, "--json")
        env = _envelope_of(capsys)
        assert env["help"]
        assert any(line.startswith("Run `") for line in env["help"])
        for line in env["help"]:
            assert line.strip()

    def test_window_days_zero_exits_2(self, capsys, monkeypatch):
        mod = _load_mod()
        rc = _run_cli(mod, "--json", "--window-days=0")
        assert rc == 2
        assert "window-days" in capsys.readouterr().err

    def test_window_days_negative_exits_2(self, capsys, monkeypatch):
        mod = _load_mod()
        rc = _run_cli(mod, "--json", "--window-days=-3")
        assert rc == 2

    def test_window_days_non_integer_exits_2(self, capsys, monkeypatch):
        mod = _load_mod()
        rc = _run_cli(mod, "--json", "--window-days=abc")
        assert rc == 2

    def test_unknown_flag_exits_2(self, capsys, monkeypatch):
        mod = _load_mod()
        rc = _run_cli(mod, "--json", "--nope")
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err


class TestBreadthCliEmptyState:
    _EMPTY_STATE = {
        "data": None,
        "count": 0,
        "errors": ["basket 'nope' not found or empty in watchlist"],
        "help": ["available baskets: tier_1"],
    }

    def test_json_empty_state_shape(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(self._EMPTY_STATE))
        rc = _run_cli(mod, "--json")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["data"] is None
        assert env["count"] == 0
        assert "nope" in " ".join(env["errors"])

    def test_text_empty_state_prints_error_and_help(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(self._EMPTY_STATE))
        rc = _run_cli(mod, "--window-days=7")
        assert rc in (0, None)
        captured = capsys.readouterr()
        assert "nope" in captured.err
        assert "available baskets: tier_1" in captured.out


class TestBreadthCliTextMode:
    def test_text_output_contains_regime_and_pct(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(_FAKE_PAYLOAD))
        rc = _run_cli(mod, "--window-days=7")
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "alt_rotation" in out
        assert "66.7%" in out
        assert "+8.00%" in out
        assert "+10.00%" in out
        assert "AAAUSD" in out
        assert "CCCUSD" in out
        assert "uncalibrated first guesses" in out

    def test_text_output_contains_truncation_note(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        payload = dict(_FAKE_PAYLOAD, effective_window_days=3, window_days=7)
        monkeypatch.setattr(mod, "analyze", lambda **kw: payload)
        _run_cli(mod, "--window-days=7")
        out = capsys.readouterr().out
        assert "effective 3d" in out


class TestBreadthCliHomeView:
    def test_no_args_renders_home_view(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        rc = _run_cli(mod)
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "no cached state" in out

    def test_no_args_json_emits_structured_empty_state(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        monkeypatch.setattr(mod, "analyze", lambda **kw: dict(TestBreadthCliEmptyState._EMPTY_STATE))
        rc = _run_cli(mod, "--json")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["data"] is None
        assert env["count"] == 0
        assert "nope" in " ".join(env["errors"])
        assert env["help"] == ["available baskets: tier_1"]


class TestBreadthCliBasketArgument:
    def test_no_basket_flag_passes_none_to_analyze(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        captured: dict = {}

        def _capture(**kw):
            captured.update(kw)
            return dict(_FAKE_PAYLOAD)

        monkeypatch.setattr(mod, "analyze", _capture)
        rc = _run_cli(mod, "--json")
        assert rc in (0, None)
        assert "basket" in captured
        assert captured["basket"] is None

    def test_explicit_basket_is_passed_through(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        mod = _load_mod()
        captured: dict = {}

        def _capture(**kw):
            captured.update(kw)
            return dict(_FAKE_PAYLOAD)

        monkeypatch.setattr(mod, "analyze", _capture)
        rc = _run_cli(mod, "--json", "--basket=tier_2")
        assert rc in (0, None)
        assert captured["basket"] == "tier_2"

    def test_empty_basket_value_exits_2(self, capsys, monkeypatch):
        mod = _load_mod()
        rc = _run_cli(mod, "--json", "--basket=")
        assert rc == 2
        assert "--basket" in capsys.readouterr().err
