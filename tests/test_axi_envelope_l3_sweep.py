"""AXI envelope tests for the L3 sweep (ADR-0004 phase 2c).

Pins the on-the-wire envelope shape for the 5 L3 strategies migrated
in phase 2c: strategy-mean-reversion, strategy-accumulation-swing,
strategy-breakout-confirm, strategy-exhaustion-fade,
strategy-liquidity-sweep. strategy-trend-follow was the L3 pilot
in test_pilot_envelope.py.
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


L3_SKILLS = [
    "strategy-mean-reversion",
    "strategy-accumulation-swing",
    "strategy-breakout-confirm",
    "strategy-exhaustion-fade",
    "strategy-liquidity-sweep",
]


def _run_skill(skill_name, *argv, candles=None, monkeypatch):
    mod = _load_skill_run(skill_name)
    if candles is None:
        candles = _make_candles()
    from analysis import strategy_runner

    monkeypatch.setattr(strategy_runner, "fetch_ohlc", lambda *a, **kw: candles)
    monkeypatch.setattr(strategy_runner, "metadata_for", lambda t: {})
    return _run_cli(mod, *argv), mod


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_envelope_keys(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert set(env.keys()) == {"data", "count", "errors", "help"}
    assert env["count"] >= 0
    assert env["errors"] == []


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_data_has_ideas_list(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert "ideas" in env["data"]
    assert isinstance(env["data"]["ideas"], list)
    assert env["count"] == len(env["data"]["ideas"])


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_default_fields_per_idea(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    if env["data"]["ideas"]:
        for idea in env["data"]["ideas"]:
            for k in ("pair", "direction", "conviction", "version", "entry_price", "stop_loss"):
                assert k in idea, f"{skill_name}: missing {k} in default idea fields"


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_full_includes_reasoning(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", "--full", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    if env["data"]["ideas"]:
        for idea in env["data"]["ideas"]:
            assert "reasoning" in idea


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_fields_projection_works(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", "--fields=pair,conviction", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    for idea in env["data"]["ideas"]:
        assert set(idea.keys()) == {"pair", "conviction"}


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_no_data_returns_empty_state(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "ZZZZ", "--json", candles=[], monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert env["count"] == 0
    assert env["data"] is None or env["data"].get("ideas") == []
    assert env["errors"]


@pytest.mark.parametrize("skill_name", L3_SKILLS)
def test_l3_help_references_risk_engine(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    joined = " ".join(env["help"])
    assert "risk-engine" in joined or "run-all-l3" in joined
