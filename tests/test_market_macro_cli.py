"""CLI surface tests for market-macro (scripts/run.py).

Tests the CLI wrapper (flag parsing, envelope shape, error handling)
rather than the classifier logic (already covered in test_macro.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from unittest.mock import patch

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_mod():
    run_path = os.path.join(REPO_ROOT, "skills", "market-macro", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("market_macro_run", run_path)
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


_FAKE_SIGNAL = {
    "timestamp": "2026-07-09T12:00:00+00:00",
    "inputs": {
        "fng": 22.0,
        "fng_label": "Extreme Fear",
        "vix": 28.4,
        "dxy": 104.1,
        "us10y": 4.32,
        "btc_dominance": 53.8,
        "btc_dominance_source": "yf",
        "total_mcap_usd": 2.41e12,
    },
    "regime": {
        "risk_appetite": "RISK_OFF",
        "liquidity": "TIGHTENING",
        "sentiment": "EXTREME_FEAR",
    },
    "errors": [],
    "incomplete": False,
    "regime_note": "Macro: risk-off environment with tightening liquidity and extreme fear",
}


class TestMacroCliEnvelope:
    def test_envelope_keys(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        rc = _run_cli(mod, "--json")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["errors"] == []

    def test_default_fields(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        _run_cli(mod, "--json")
        env = _envelope_of(capsys)
        assert "regime" in env["data"]
        assert "regime_note" in env["data"]
        assert "incomplete" in env["data"]

    def test_full_includes_inputs(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        _run_cli(mod, "--json", "--full")
        env = _envelope_of(capsys)
        assert "inputs" in env["data"]

    def test_fields_projection(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        _run_cli(mod, "--json", "--fields=regime_note")
        env = _envelope_of(capsys)
        assert set(env["data"].keys()) == {"regime_note"}

    def test_errors_surfaced_in_envelope(self, capsys, monkeypatch):
        def _failing(**kw):
            return {**_FAKE_SIGNAL, "errors": ["coingecko: 429"], "incomplete": True}

        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", _failing)
        _run_cli(mod, "--json")
        env = _envelope_of(capsys)
        assert "coingecko: 429" in str(env["errors"])

    def test_help_lines_reference_next_steps(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        _run_cli(mod, "--json")
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "run-all-l3" in joined
        assert "market-valuation" in joined

    def test_no_args_renders_home_view(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        rc = _run_cli(mod)
        assert rc in (0, None)
        out = capsys.readouterr().out
        assert "last cached state" in out

    def test_cache_disabled_flag(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        rc = _run_cli(mod, "--json", "--no-cache")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["data"]["regime"]["risk_appetite"] == "RISK_OFF"


class TestMacroCliTextMode:
    def test_text_output_contains_regime_labels(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        _run_cli(mod, "--no-history")
        out = capsys.readouterr().out
        assert "RISK_OFF" in out
        assert "TIGHTENING" in out
        assert "EXTREME_FEAR" in out

    def test_text_output_contains_inputs(self, capsys, monkeypatch):
        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", lambda **kw: _FAKE_SIGNAL)
        _run_cli(mod, "--no-history")
        out = capsys.readouterr().out
        assert "VIX" in out
        assert "DXY" in out
        assert "F&G" in out

    def test_error_in_text_output(self, capsys, monkeypatch):
        def _failing(**kw):
            return {**_FAKE_SIGNAL, "errors": ["fng: timeout"]}

        mod = _load_mod()
        monkeypatch.setattr(mod, "fetch_regime", _failing)
        _run_cli(mod, "--no-history")
        out = capsys.readouterr().out
        assert "fng: timeout" in out
