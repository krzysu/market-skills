"""Tests for L2 pattern detection lib.py files."""

import importlib.util
import os
import random

import pytest

random.seed(42)


@pytest.fixture
def candles():
    vals = []
    price = 100.0
    for i in range(250):
        price += random.uniform(-2, 2)
        vals.append([i * 86400, price, price + 1, price - 1, price, random.randint(100000, 500000)])
    return vals


def _load_l2_skill(skill_name):
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", skill_name, "lib.py")
    spec = importlib.util.spec_from_file_location(f"{skill_name}_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMarketTrendQuality:
    def test_analyze_returns_pattern(self, candles):
        mod = _load_l2_skill("market-trend-quality")
        result = mod.analyze(candles)
        assert "pattern" in result
        assert "present" in result["pattern"]
        assert "confidence" in result["pattern"]
        assert result["pattern"]["type"] == "TREND_QUALITY"

    def test_present_is_bool(self, candles):
        mod = _load_l2_skill("market-trend-quality")
        result = mod.analyze(candles)
        assert isinstance(result["pattern"]["present"], bool)

    def test_present_and_confidence_consistent(self, candles):
        mod = _load_l2_skill("market-trend-quality")
        result = mod.analyze(candles)
        pat = result["pattern"]
        if pat["present"]:
            assert 1 <= pat["confidence"] <= pat["max_confidence"]
        else:
            assert pat["confidence"] >= 1

    def test_insufficient_data(self):
        mod = _load_l2_skill("market-trend-quality")
        result = mod.analyze([], interval="1d", period="1y")
        assert result["pattern"]["present"] is False


class TestMarketBreakout:
    def test_analyze_returns_pattern(self, candles):
        mod = _load_l2_skill("market-breakout")
        result = mod.analyze(candles)
        assert "pattern" in result
        assert result["pattern"]["type"] == "BREAKOUT"

    def test_insufficient_data(self):
        mod = _load_l2_skill("market-breakout")
        result = mod.analyze([], interval="1d", period="1y")
        assert result["pattern"]["present"] is False


class TestMarketExhaustion:
    def test_analyze_returns_pattern(self, candles):
        mod = _load_l2_skill("market-exhaustion")
        result = mod.analyze(candles)
        assert "pattern" in result
        assert result["pattern"]["type"] == "EXHAUSTION"

    def test_insufficient_data(self):
        mod = _load_l2_skill("market-exhaustion")
        result = mod.analyze([], interval="1d", period="1y")
        assert result["pattern"]["present"] is False


class TestMarketTrendAnalysis:
    def test_analyze_returns_pattern(self, candles):
        mod = _load_l2_skill("market-trend-analysis")
        result = mod.analyze(candles)
        assert "pattern" in result
        assert "present" in result["pattern"]
        assert "confidence" in result["pattern"]
        assert result["pattern"]["type"] == "TREND_ANALYSIS"

    def test_present_is_bool(self, candles):
        mod = _load_l2_skill("market-trend-analysis")
        result = mod.analyze(candles)
        assert isinstance(result["pattern"]["present"], bool)

    def test_insufficient_data(self):
        mod = _load_l2_skill("market-trend-analysis")
        result = mod.analyze([], interval="1d", period="1y")
        assert result["pattern"]["present"] is False

    def test_confidence_range(self, candles):
        mod = _load_l2_skill("market-trend-analysis")
        result = mod.analyze(candles)
        pat = result["pattern"]
        assert 1 <= pat["confidence"] <= pat["max_confidence"]


class TestMarketAccumulation:
    def test_analyze_returns_pattern(self, candles):
        mod = _load_l2_skill("market-accumulation")
        result = mod.analyze(candles)
        assert "pattern" in result
        assert "present" in result["pattern"]
        assert "confidence" in result["pattern"]
        assert result["pattern"]["type"] == "ACCUMULATION"
        assert "input_scores" in result

    def test_insufficient_data(self):
        mod = _load_l2_skill("market-accumulation")
        result = mod.analyze([], interval="1d", period="1y")
        assert result["pattern"]["present"] is False

    def test_present_is_bool(self, candles):
        mod = _load_l2_skill("market-accumulation")
        result = mod.analyze(candles)
        assert isinstance(result["pattern"]["present"], bool)


class TestMarketLiquiditySweep:
    def test_analyze_returns_pattern(self, candles):
        mod = _load_l2_skill("market-liquidity-sweep")
        result = mod.analyze(candles)
        assert "pattern" in result
        assert "present" in result["pattern"]
        assert "confidence" in result["pattern"]
        assert result["pattern"]["type"] == "SWEEP"
        assert "input_scores" in result

    def test_insufficient_data(self):
        mod = _load_l2_skill("market-liquidity-sweep")
        result = mod.analyze([], interval="1d", period="1y")
        assert result["pattern"]["present"] is False

    def test_present_is_bool(self, candles):
        mod = _load_l2_skill("market-liquidity-sweep")
        result = mod.analyze(candles)
        assert isinstance(result["pattern"]["present"], bool)
