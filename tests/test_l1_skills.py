"""Tests for all 9 L1 skill lib.py files."""

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


def _load_skill(skill_name):
    """Load a skill's lib.py dynamically."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", skill_name, "lib.py")
    spec = importlib.util.spec_from_file_location(f"{skill_name}_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMarketEMA:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-ema")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "ema_21" in result
        assert "ema_50" in result
        assert "alignment" in result
        assert "score" in result
        assert "signal" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-ema")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_score_range(self, candles):
        mod = _load_skill("market-ema")
        result = mod.analyze(candles)
        assert -2 <= result["score"] <= 2


class TestMarketRSI:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-rsi")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "rsi_14" in result
        assert "signal" in result
        assert "score" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-rsi")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_rsi_range(self, candles):
        mod = _load_skill("market-rsi")
        result = mod.analyze(candles)
        if "error" not in result:
            assert 0 <= result["rsi_14"] <= 100

    def test_score_signal_consistency(self, candles):
        mod = _load_skill("market-rsi")
        result = mod.analyze(candles)
        if "error" not in result:
            score = result["score"]
            signal = result["signal"]
            if score > 0:
                assert "OVER" in signal or "OVERSOLD" in signal
            elif score < 0:
                assert "OVER" in signal


class TestMarketSqueeze:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-squeeze")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "squeeze_on" in result
        assert "momentum" in result
        assert "direction" in result
        assert "signal" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-squeeze")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result


class TestMarketVolume:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-volume")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "volume_ratio" in result
        assert "obv_trend" in result
        assert "regime" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-volume")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_volume_ratio_positive(self, candles):
        mod = _load_skill("market-volume")
        result = mod.analyze(candles)
        if "error" not in result:
            assert result["volume_ratio"] >= 0


class TestMarketVolatility:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-volatility")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "realized_vol_7d" in result
        assert "regime" in result
        assert "trend" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-volatility")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_volatility_non_negative(self, candles):
        mod = _load_skill("market-volatility")
        result = mod.analyze(candles)
        if "error" not in result:
            assert result["realized_vol_7d"] >= 0


class TestMarketMACD:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-macd")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "macd_line" in result
        assert "signal_line" in result
        assert "histogram" in result
        assert "signal" in result
        assert "score" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-macd")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_score_range(self, candles):
        mod = _load_skill("market-macd")
        result = mod.analyze(candles)
        if "error" not in result:
            assert -2 <= result["score"] <= 2


class TestMarketFibonacci:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-fibonacci")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "swing_high" in result
        assert "swing_low" in result
        assert "current_position" in result
        assert "fib_levels" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-fibonacci")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_fib_levels_dict(self, candles):
        mod = _load_skill("market-fibonacci")
        result = mod.analyze(candles)
        if "error" not in result:
            assert isinstance(result["fib_levels"], dict)
            assert len(result["fib_levels"]) > 0


class TestMarketSR:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-s-r")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "nearest_support" in result
        assert "nearest_resistance" in result
        assert "clustered_levels" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-s-r")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_support_below_resistance(self, candles):
        mod = _load_skill("market-s-r")
        result = mod.analyze(candles)
        if "error" not in result:
            assert result["nearest_support"] < result["nearest_resistance"]

    def test_no_nearby_level_field_present(self, candles):
        """market-s-r surfaces no_nearby_level so L3 callers
        can flag open-air setups (<PRIVATE_PERP>-style) where stop sits 15%+ from entry."""
        mod = _load_skill("market-s-r")
        result = mod.analyze(candles)
        assert "no_nearby_level" in result
        assert isinstance(result["no_nearby_level"], bool)


class TestMarketTrend:
    def test_analyze_returns_dict(self, candles):
        mod = _load_skill("market-trend")
        result = mod.analyze(candles)
        assert "error" not in result
        assert "ema_21" in result
        assert "ema_50" in result
        assert "alignment" in result
        assert "score" in result
        assert "signal" in result

    def test_insufficient_data(self):
        mod = _load_skill("market-trend")
        result = mod.analyze([], interval="1d", period="1y")
        assert "error" in result

    def test_score_range(self, candles):
        mod = _load_skill("market-trend")
        result = mod.analyze(candles)
        if "error" not in result:
            assert -4 <= result["score"] <= 4
