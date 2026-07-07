"""AXI envelope tests for the L1 sweep (ADR-0004 phase 2a).

Pins the on-the-wire envelope shape for the 8 L1 skills migrated
in phase 2a: market-ema, market-macd, market-volume,
market-volatility, market-squeeze, market-trend, market-fibonacci,
market-s-r.

Same harness as ``tests/test_pilot_envelope.py``: load each
``scripts/run.py`` via importlib, patch ``fetch_ohlc`` to return
canned candles, and assert the envelope.
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


L1_SKILLS = [
    "market-ema",
    "market-macd",
    "market-volume",
    "market-volatility",
    "market-squeeze",
    "market-trend",
    "market-fibonacci",
    "market-s-r",
]


def _run_skill(skill_name, *argv, candles=None, monkeypatch):
    mod = _load_skill_run(skill_name)
    if candles is None:
        candles = _make_candles()
    monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)
    return _run_cli(mod, *argv), mod


@pytest.mark.parametrize("skill_name", L1_SKILLS)
def test_l1_envelope_keys(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert set(env.keys()) == {"data", "count", "errors", "help"}
    assert env["count"] == 1
    assert env["errors"] == []
    assert isinstance(env["help"], list) and env["help"]


@pytest.mark.parametrize("skill_name", L1_SKILLS)
def test_l1_default_schema_is_minimal(skill_name, capsys, monkeypatch):
    """AXI principle 2: 3-4 fields per item by default. Each L1's
    default schema is in the script's DEFAULT_FIELDS constant; we
    assert it's at most 6 (squeeze/trend need a few more) and
    includes the ticker.
    """
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    data_keys = list(env["data"].keys())
    assert "ticker" in data_keys
    assert 1 <= len(data_keys) <= 6, f"{skill_name} default schema too wide: {data_keys}"


@pytest.mark.parametrize("skill_name", L1_SKILLS)
def test_l1_full_includes_all_indicators(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", "--full", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert env["count"] == 1
    assert env["data"].get("skill") == skill_name


@pytest.mark.parametrize("skill_name", L1_SKILLS)
def test_l1_fields_projection_works(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "AAPL", "--json", "--fields=ticker", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert list(env["data"].keys()) == ["ticker"]


@pytest.mark.parametrize("skill_name", L1_SKILLS)
def test_l1_no_data_returns_empty_state(skill_name, capsys, monkeypatch):
    _run_skill(skill_name, "ZZZZ", "--json", candles=[], monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    assert env["count"] == 0
    assert env["data"] is None
    assert env["errors"]
    assert env["help"]


@pytest.mark.parametrize("skill_name", L1_SKILLS)
def test_l1_help_references_other_skills(skill_name, capsys, monkeypatch):
    """AXI principle 9: contextual disclosure — help[] lines should
    point at least one related skill or projection flag.
    """
    _run_skill(skill_name, "AAPL", "--json", monkeypatch=monkeypatch)
    env = _envelope_of(capsys)
    joined = " ".join(env["help"])
    assert "market-" in joined or "--fields" in joined or "--full" in joined
