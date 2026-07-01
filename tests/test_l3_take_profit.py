"""Tests for L3 strategies — 3-element take_profit ladder (L3Idea contract).

The L3Idea contract requires `take_profit: list[float]` with exactly 3 entries.
strategy-mean-reversion and strategy-liquidity-sweep previously returned 2-element
ladders, violating the contract on SOL 1h mean-reversion and SOL 4h liquidity-sweep.
This test pins the 3-element contract via monkeypatched sub-skill responses.
"""

import importlib.util
import os

import pytest


def _load_strat_lib(name: str):
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", f"strategy-{name}", "lib.py")
    spec = importlib.util.spec_from_file_location(f"strategy_{name}_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _patched_skill_loader(monkeypatch):
    """Patch analysis.skill_loader.load_skill to return canned responses."""
    canned = {}
    import analysis.skill_loader as sl

    def fake_load_skill(name):
        return canned.get(name)

    monkeypatch.setattr(sl, "load_skill", fake_load_skill)
    return canned


def _make_candles(n=250, base=100.0, drift=0.0, seed=42, lock_close=None, half_range=0.25):
    """Build [ts, open, high, low, close, volume] candles.

    ``lock_close`` overrides the final close to a specific value (and rewrites the
    last candle's open/high/low around it) so tests can pin the price to a known
    band without depending on the random walk. ``half_range`` controls the
    per-bar wick on each side of close (default 0.25, i.e. 0.5 total range).
    Tests for the 2% L3 stop-distance guard need a larger range so ATR-derived
    stops clear the 2% floor — pass ``half_range=1.5`` or similar.
    """
    import random

    rng = random.Random(seed)
    price = base
    out = []
    for i in range(n):
        open_p = price
        close_p = price * (1.0 + drift) + rng.uniform(-1.0, 1.0)
        high_p = max(open_p, close_p) + half_range
        low_p = min(open_p, close_p) - half_range
        price = close_p
        out.append(
            [
                i * 86400,
                open_p,
                high_p,
                low_p,
                close_p,
                200_000,
            ]
        )
    if lock_close is not None:
        prev_close = out[-2][4]
        out[-1] = [
            (n - 1) * 86400,
            prev_close,
            max(prev_close, lock_close) + 0.1,
            min(prev_close, lock_close) - 0.1,
            lock_close,
            200_000,
        ]
    return out


class TestStrategyMeanReversion:
    def test_long_take_profit_has_three_entries(self, monkeypatch, _patched_skill_loader):
        """Long idea when RSI ≤ 30 AND price near support must produce 3 TPs."""
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        # support=99.5, resistance=110. Anchor base at 99.5 so close lands within 2% of support.
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": 110.0})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()

        mod = _load_strat_lib("mean-reversion")
        # Lock the final close just above support (within 2% band) so the long branch fires.
        # half_range=1.5 → ATR(14) ≈ 3.0, stop = 99.5 - 3.0 = 96.5, distance ≈ 3.2% ≥ 2% guard floor.
        candles = _make_candles(n=250, base=99.5, drift=0.0, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert len(result["ideas"]) == 1, f"expected 1 long idea, got {result['ideas']}"
        idea = result["ideas"][0]
        assert idea["direction"] == "long"
        assert len(idea["take_profit"]) == 3, (
            f"L3Idea contract requires 3-element take_profit, got {len(idea['take_profit'])}: {idea['take_profit']}"
        )
        assert idea["take_profit"][0] < idea["take_profit"][1] <= idea["take_profit"][2]

    def test_short_take_profit_has_three_entries(self, monkeypatch, _patched_skill_loader):
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 75})}
        )()
        # resistance=100.5, support=85. Anchor base at 100.5 so close lands within 2% of resistance.
        # support=85.0 is well below entry*0.95 (95.3) so the TP3 ladder stays strictly
        # descending under the bumped half_range (risk=3.2 → TP2=93.9, TP3=85.0).
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 85.0, "nearest_resistance": 100.5})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "HIGH"})}
        )()

        mod = _load_strat_lib("mean-reversion")
        # Lock the final close just below resistance (within 2% band) so the short branch fires.
        # half_range=1.5 → ATR(14) ≈ 3.0, stop = 100.5 + 3.0 = 103.5, distance ≈ 3.2% ≥ 2% guard floor.
        candles = _make_candles(n=250, base=100.5, drift=0.0, lock_close=100.3, half_range=1.5)
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert len(result["ideas"]) == 1, f"expected 1 short idea, got {result['ideas']}"
        idea = result["ideas"][0]
        assert idea["direction"] == "short"
        assert len(idea["take_profit"]) == 3, (
            f"L3Idea contract requires 3-element take_profit, got {len(idea['take_profit'])}: {idea['take_profit']}"
        )
        assert idea["take_profit"][0] > idea["take_profit"][1] >= idea["take_profit"][2]


class TestStrategyLiquiditySweep:
    def test_full_setup_take_profit_has_three_entries(self, monkeypatch, _patched_skill_loader):
        """Sweep + accumulation + volume → 3-TP ladder."""
        sweep_pattern = {"present": True, "confidence": 4, "classification": "SUPPORT_SWEEP"}
        accum_pattern = {"present": True, "confidence": 4, "classification": "SPRING"}
        vol_response = {"volume_ratio": 1.8, "obv_trend": "rising"}
        _patched_skill_loader["market-liquidity-sweep"] = type(
            "L", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": sweep_pattern})}
        )()
        _patched_skill_loader["market-accumulation"] = type(
            "A", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": accum_pattern})}
        )()
        _patched_skill_loader["market-volume"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: vol_response)}
        )()

        mod = _load_strat_lib("liquidity-sweep")
        # half_range=1.5 → ATR(14) ≈ 3.0, stop = entry - 1.5*ATR ≈ entry - 4.5, distance ≈ 4.5% ≥ 2% guard floor.
        candles = _make_candles(n=250, base=100.0, half_range=1.5)
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert len(result["ideas"]) == 1
        idea = result["ideas"][0]
        assert len(idea["take_profit"]) == 3, (
            f"L3Idea contract requires 3-element take_profit, got {len(idea['take_profit'])}: {idea['take_profit']}"
        )
        assert idea["take_profit"][0] < idea["take_profit"][1] < idea["take_profit"][2]

    def test_sweep_only_setup_take_profit_has_three_entries(self, monkeypatch, _patched_skill_loader):
        """Sweep + volume (no accumulation) → 3-TP ladder."""
        sweep_pattern = {"present": True, "confidence": 3, "classification": "SUPPORT_SWEEP"}
        accum_pattern = {"present": False, "classification": None}
        vol_response = {"volume_ratio": 1.5, "obv_trend": "rising"}
        _patched_skill_loader["market-liquidity-sweep"] = type(
            "L", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": sweep_pattern})}
        )()
        _patched_skill_loader["market-accumulation"] = type(
            "A", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": accum_pattern})}
        )()
        _patched_skill_loader["market-volume"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: vol_response)}
        )()

        mod = _load_strat_lib("liquidity-sweep")
        # half_range=1.5 → ATR(14) ≈ 3.0, stop = entry - 1.5*ATR ≈ entry - 4.5, distance ≈ 4.5% ≥ 2% guard floor.
        candles = _make_candles(n=250, base=100.0, half_range=1.5)
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert len(result["ideas"]) == 1
        idea = result["ideas"][0]
        assert len(idea["take_profit"]) == 3, (
            f"L3Idea contract requires 3-element take_profit, got {len(idea['take_profit'])}: {idea['take_profit']}"
        )
