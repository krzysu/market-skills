"""Per-fix fixture for the ``conviction_mode`` kwarg on
``strategy-liquidity-sweep.analyze()`` (bead market-skills-7eq).

The kwarg lets a caller (notably the backtest-engine) pin the conviction
formula per ``analyze`` call. Default ``None`` = legacy behaviour (the
formula function's own default applies, currently ``"current"``); setting
``conviction_mode="max_plus_one"`` (etc.) routes the conviction through the
named alternative.

These tests pin the new contract by stubbing the L2s with canned confidences
and asserting the produced idea's conviction matches the formula for the
specified mode.
"""

from __future__ import annotations

import importlib.util
import os

import pytest


def _load_lib():
    lib_path = os.path.join(
        os.path.dirname(__file__), "..", "skills", "strategy-liquidity-sweep", "lib.py"
    )
    spec = importlib.util.spec_from_file_location("strategy_liquidity_sweep_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=120):
    import random

    rng = random.Random(7)
    out = []
    price = 100.0
    for i in range(n):
        open_p = price
        close_p = price + rng.uniform(-1.0, 1.0)
        high_p = max(open_p, close_p) + 1.5
        low_p = min(open_p, close_p) - 1.5
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 200_000])
    return out


@pytest.fixture
def stub_skills(monkeypatch):
    """Pin a controlled L1/L2 response so analyze() takes the branch1 path
    (sweep + accum + volume all confirm). Conviction then computes purely
    from the canned confidences.
    """
    import analysis.skill_loader as sl

    sweep_conf = 2
    accum_conf = 3

    sweep = {
        "pattern": {
            "present": True,
            "confidence": sweep_conf,
            "max_confidence": 5,
            "classification": "FRESH",
            "type": "LIQUIDITY_SWEEP",
        },
        "narrative": "stub",
        "input_scores": {},
    }
    accum = {
        "pattern": {
            "present": True,
            "confidence": accum_conf,
            "max_confidence": 5,
            "classification": "ACCUMULATING",
            "type": "ACCUMULATION",
        },
        "narrative": "stub",
        "input_scores": {},
    }
    vol = {"volume_ratio": 1.5, "obv_trend": "rising"}

    canned = {
        "market-liquidity-sweep": type("S", (), {"analyze": staticmethod(lambda c, **_kw: sweep)})(),
        "market-accumulation": type("A", (), {"analyze": staticmethod(lambda c, **_kw: accum)})(),
        "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: vol)})(),
    }
    monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))
    return sweep_conf, accum_conf


class TestConvictionModeForwarding:
    """The new kwarg must reach ``conviction_from_confidences(mode=...)`` and
    produce the per-mode result; default behaviour must remain unchanged."""

    def test_default_kwarg_uses_legacy_formula(self, stub_skills):
        sweep_conf, accum_conf = stub_skills
        mod = _load_lib()
        # No conviction_mode kwarg -> default. With sweep=2, accum=3:
        # current = 2 + 3 // 2 = 2 + 1 = 3
        result = mod.analyze(_make_candles(), ticker="T")
        assert result["ideas"], f"Expected branch1 idea with stubbed L2s, got {result}"
        assert result["ideas"][0]["conviction"] == 3, (
            f"Default conviction on (sweep=2, accum=3) should be 3 (current); "
            f"got {result['ideas'][0]['conviction']}"
        )

    @pytest.mark.parametrize(
        "mode,expected",
        [
            ("current", 3),         # 2 + 3 // 2 = 2 + 1 = 3
            ("add", 5),             # 2 + 3 = 5
            ("add_minus_one", 4),   # 2 + 3 - 1 = 4
            ("max_plus_one", 4),    # max(2, 3) + 1 = 4
        ],
    )
    def test_each_mode_routes_correctly(self, stub_skills, mode, expected):
        mod = _load_lib()
        result = mod.analyze(
            _make_candles(), ticker="T", conviction_mode=mode
        )
        assert result["ideas"], f"Expected branch1 idea in mode={mode!r}, got {result}"
        assert result["ideas"][0]["conviction"] == expected, (
            f"mode={mode!r} on (sweep=2, accum=3) must yield {expected} "
            f"(via conviction_from_confidences); got {result['ideas'][0]['conviction']}"
        )

    def test_unknown_mode_propagates_value_error(self, monkeypatch, stub_skills):
        """``conviction_from_confidences`` raises ValueError on unknown mode;
        passing that mode through analyze() must surface the same error."""
        mod = _load_lib()
        with pytest.raises(ValueError, match="unknown conviction mode"):
            mod.analyze(
                _make_candles(), ticker="T", conviction_mode="bogus"
            )
