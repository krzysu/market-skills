"""AXI envelope tests for the L2 sweep (ADR-0004 phase 2b).

Pins the on-the-wire envelope shape for the 4 L2 skills migrated
in phase 2b: market-accumulation, market-breakout,
market-exhaustion, market-liquidity-sweep. market-trend-quality
was the L2 pilot in test_pilot_envelope.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from unittest.mock import patch

import pytest

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


L2_SKILLS = [
    "market-accumulation",
    "market-breakout",
    "market-exhaustion",
    "market-liquidity-sweep",
]


def _run_skill(skill_name, *argv, candles=None, monkeypatch):
    mod = _load_skill_run(skill_name)
    if candles is None:
        candles = _make_candles()
    monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
    return _run_cli(mod, *argv), mod


@pytest.mark.parametrize("skill_name", L2_SKILLS)
def test_l2_envelope_keys(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert set(env.keys()) == {"data", "count", "errors", "help"}
    assert env["count"] == 1
    assert env["errors"] == []


@pytest.mark.parametrize("skill_name", L2_SKILLS)
def test_l2_default_fields_include_fired(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert "fired" in env["data"]
    assert "classification" in env["data"]
    assert "confidence" in env["data"]


@pytest.mark.parametrize("skill_name", L2_SKILLS)
def test_l2_full_includes_signals(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", "--full", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert "signals" in env["data"]
    assert "input_scores" in env["data"]
    assert "narrative" in env["data"]


@pytest.mark.parametrize("skill_name", L2_SKILLS)
def test_l2_fields_projection_works(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", "--fields=ticker,fired", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert set(env["data"].keys()) == {"ticker", "fired"}


@pytest.mark.parametrize("skill_name", L2_SKILLS)
def test_l2_no_data_returns_empty_state(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "ZZZZ", "--json", candles=[], monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert env["count"] == 0
    assert env["data"] is None
    assert env["errors"]


@pytest.mark.parametrize("skill_name", L2_SKILLS)
def test_l2_help_references_l3(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    joined = " ".join(env["help"])
    assert "strategy-" in joined or "--fields" in joined or "--full" in joined
