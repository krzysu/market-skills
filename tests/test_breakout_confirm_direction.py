"""Tests for strategy-breakout-confirm's direction contract (bead market-skills-k4c).

Regression for the L2/L3 contract mismatch documented in market-skills-hsg:
``market-breakout``'s ``pattern.classification`` is a status string
(FRESH/FAILED/STALE/CONFIRMED), NOT a direction. The L3 used to check
``"BULL"/"BEAR" in classification`` -- dead code against the actual L2 output.
The fix: market-breakout now returns ``pattern.direction`` (bull/bear/None) and
the L3 keys off that. These tests assert the new contract on the L3 side.

Each positive test reproduces the exact shape that triggered the bug:
classification is a non-directional status; direction is what unlocks the entry.
"""

from __future__ import annotations

import importlib.util
import os

import pytest


def _load_l3_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "strategy-breakout-confirm", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_breakout_confirm_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=250, base=100.0, half_range=1.5, seed=42):
    """Build [ts, open, high, low, close, volume] candles.

    ``half_range=1.5`` (= 3% per candle) ensures ATR-derived stops clear the
    2% swing-minimum floor for any direction the test exercises.
    """
    import random

    rng = random.Random(seed)
    price = base
    out = []
    for i in range(n):
        open_p = price
        close_p = price + rng.uniform(-1.0, 1.0)
        high_p = max(open_p, close_p) + half_range
        low_p = min(open_p, close_p) - half_range
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 200_000])
    return out


@pytest.fixture
def patched_skill_loader(monkeypatch):
    """Patch analysis.skill_loader.load_skill; tests populate `canned` per case."""
    import analysis.skill_loader as sl

    canned = {}

    def fake_load_skill(name):
        return canned.get(name)

    monkeypatch.setattr(sl, "load_skill", fake_load_skill)
    return canned


def _stub(name, payload):
    """Tiny stand-in for a skill module with a staticmethod ``analyze``."""
    return type(name, (), {"analyze": staticmethod(lambda c, **_kw: payload)})()


class TestDirectionContractLong:
    """Long branch: bo_direction == 'bull' + volume_ok + (squeeze_long OR obv_rising) -> long idea."""

    def test_long_when_direction_bull_with_status_only_classification(self, monkeypatch, patched_skill_loader):
        """The hsg bug shape: classification='CONFIRMED' has no BULL/BEAR substring.
        With direction='bull' set, the post-fix L3 emits a long idea. Pre-fix:
        substring check missed, 0 ideas regardless. Per AGENTS.md: this test fails
        on the pre-fix lib and passes on the post-fix lib."""
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": True,
                    "confidence": 4,
                    "max_confidence": 5,
                    "classification": "CONFIRMED",
                    "direction": "bull",
                    "type": "BREAKOUT",
                },
                "signals": {"structure_break": {"present": True, "weight": 0.35}},
                "narrative": "Confirmed bullish breakout",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.5, "obv_trend": "rising"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "BULLISH"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert len(result["ideas"]) == 1, (
            f"Expected exactly 1 long idea when bo_direction=='bull' + volume + squeeze, "
            f"got {len(result['ideas'])}: {result['ideas']}"
        )
        idea = result["ideas"][0]
        assert idea["direction"] == "long"
        assert "CONFIRMED" in idea["reasoning"], (
            f"Narrative must still reference classification='CONFIRMED' (informational, "
            f"not a gate), got: {idea['reasoning']!r}"
        )

    def test_long_obv_only_when_squeeze_neutral(self, monkeypatch, patched_skill_loader):
        """Squeeze signal absent/NEUTRAL; OBV rising provides the (c) gate."""
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": True,
                    "confidence": 4,
                    "max_confidence": 5,
                    "classification": "FRESH",
                    "direction": "bull",
                    "type": "BREAKOUT",
                },
                "signals": {"structure_break": {"present": True, "weight": 0.35}},
                "narrative": "Fresh bullish breakout",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.5, "obv_trend": "rising"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "NEUTRAL"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert len(result["ideas"]) == 1
        assert result["ideas"][0]["direction"] == "long"

    def test_long_blocked_when_volume_below_1_2(self, monkeypatch, patched_skill_loader):
        """Even with direction='bull', volume_ratio must clear 1.2 (gate b)."""
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": True,
                    "confidence": 4,
                    "max_confidence": 5,
                    "classification": "FRESH",
                    "direction": "bull",
                    "type": "BREAKOUT",
                },
                "signals": {"structure_break": {"present": True, "weight": 0.35}},
                "narrative": "Fresh bullish breakout",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.0, "obv_trend": "rising"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "BULLISH"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert result["ideas"] == [], "gate (b) must block vol_ratio <= 1.2"


class TestDirectionContractShort:
    """Short branch: bo_direction == 'bear' + volume_ok + (squeeze_short OR obv_falling) -> short idea."""

    def test_short_when_direction_bear(self, monkeypatch, patched_skill_loader):
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": True,
                    "confidence": 4,
                    "max_confidence": 5,
                    "classification": "FAILED",
                    "direction": "bear",
                    "type": "BREAKOUT",
                },
                "signals": {"structure_break": {"present": True, "weight": 0.35}},
                "narrative": "Failed bullish / bearish breakdown",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.5, "obv_trend": "falling"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "BEARISH"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert len(result["ideas"]) == 1
        assert result["ideas"][0]["direction"] == "short"
        assert "FAILED" in result["ideas"][0]["reasoning"]


class TestDirectionContractNoTrade:
    """Negative paths: direction absent/None, no direction yielded."""

    def test_no_trade_when_direction_none(self, monkeypatch, patched_skill_loader):
        """Pre-fix would also have returned 0 ideas here (no BULL/BEAR substring in
        classification='FRESH'); post-fix must be explicit: direction=None -> 0."""
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": True,
                    "confidence": 3,
                    "max_confidence": 5,
                    "classification": "FRESH",
                    "direction": None,
                    "type": "BREAKOUT",
                },
                "signals": {"structure_break": {"present": False, "weight": 0.35}},
                "narrative": "ambiguous",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.5, "obv_trend": "rising"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "BULLISH"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert result["ideas"] == [], f"direction=None must yield 0 ideas (post-fix explicit), got {result['ideas']}"

    def test_no_trade_when_direction_key_absent(self, monkeypatch, patched_skill_loader):
        """Simulates a stale L2 that hasn't been updated to expose direction. The L3
        must treat missing direction as None and emit no ideas."""
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": True,
                    "confidence": 4,
                    "max_confidence": 5,
                    "classification": "CONFIRMED",
                    "type": "BREAKOUT",
                },
                "signals": {"structure_break": {"present": True, "weight": 0.35}},
                "narrative": "stale",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.5, "obv_trend": "rising"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "BULLISH"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert result["ideas"] == [], f"Stale L2 (no direction key) must produce 0 ideas, got {result['ideas']}"

    def test_no_trade_when_l2_not_present(self, monkeypatch, patched_skill_loader):
        """L2 pattern absent -> bo_classification is None -> bo_pattern.get('direction') None."""
        patched_skill_loader["market-breakout"] = _stub(
            "BO",
            {
                "pattern": {
                    "present": False,
                    "confidence": 1,
                    "max_confidence": 5,
                    "classification": None,
                    "type": "BREAKOUT",
                },
                "signals": {},
                "narrative": "no breakout",
            },
        )
        patched_skill_loader["market-volume"] = _stub(
            "V",
            {"volume_ratio": 1.5, "obv_trend": "rising"},
        )
        patched_skill_loader["market-squeeze"] = _stub("Z", {"signal": "BULLISH"})

        mod = _load_l3_lib()
        result = mod.analyze(_make_candles(), ticker="BTCUSD", interval="1d", period="1y")

        assert result["ideas"] == []
