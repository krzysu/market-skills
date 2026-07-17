"""Cross-strategy fixture: every L3 strategy emits ``rr_to_tp`` on every idea.

The ``rr_to_tp`` field is precomputed at L3 emit time so any consumer
(swing-scan, position-watchdog, paper-trader, LLM agent brain) can read a
canonical R:R to each TP level without reimplementing the
direction-asymmetric formula. This test pins that contract across all 6
L3 strategies by patching the L2 sub-skill calls so the strategy branch
fires deterministically, then asserting the rr_to_tp values match the
ladder multipliers the strategy is supposed to emit.

Canonical ladders (entry → stop = risk):

  - trend-follow       long/short: 1.5R / 2.5R / 4R
  - breakout-confirm   long/short: 1.5R / 2.5R / 4R
  - accumulation-swing long:        2R   / 3R   / 5R
  - liquidity-sweep    long:        2R   / 3R   / 4R
  - mean-reversion     long/short: 1R   / 2R   / 3R (or full reversion)
  - exhaustion-fade    long/short: 1R   / 2R   / 3R
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


def _make_candles(n=250, base=100.0, half_range=1.5, lock_close=None, seed=42):
    """Build [ts, open, high, low, close, volume] candles.

    ``half_range`` of 1.5 gives ATR(14) ≈ 3.0, so the strategy's
    ``stop = entry - atr*2`` lands ~6% from entry — well clear of the 2%
    L3 swing stop guard. ``lock_close`` pins the final close so the
    strategy's signal branch fires deterministically.
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


@pytest.fixture
def _patched_skill_loader(monkeypatch):
    """Patch analysis.skill_loader.load_skill to return canned responses.

    Each test fills the dict with the L2 sub-skill mocks it needs. Strategies
    that try to load a sub-skill not in the dict will get None and short-circuit
    (the strategy code already handles that).
    """
    canned: dict = {}
    import analysis.skill_loader as sl

    monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))
    return canned


def _assert_rr_matches(idea: dict, expected: list[float], tol: float = 0.01) -> None:
    """Assert idea.rr_to_tp matches the expected ladder within tolerance.

    0.01 tolerance accommodates the 2dp ``stop_loss`` display rounding:
    ``take_profit_ideal`` is built from the unrounded risk (= entry - stop_unrounded)
    while ``compute_rr_to_tp`` reads the rounded ``stop_loss`` field. The gap
    is ~0.06% of risk (e.g. a 3-cent stop-rounding on a 5-unit risk), which
    projects to ~0.002 on a 3.0 rr value. That's well below any consumer gate
    (swing-scan floors are 3:1 / 4:1, position-watchdog is 1:1) so the field
    is consumer-accurate even on sub-$5 setups.
    """
    assert "rr_to_tp" in idea, (
        f"strategy must emit rr_to_tp on every idea — missing on "
        f"direction={idea.get('direction')}, pair={idea.get('pair')}. "
        f"Post-build loop must call compute_rr_to_tp(idea) before validate_l3_tp_ladder."
    )
    rr = idea["rr_to_tp"]
    assert len(rr) == 3, f"rr_to_tp must be 3-element, got {len(rr)}: {rr}"
    for actual, want in zip(rr, expected, strict=False):
        assert abs(actual - want) <= tol, (
            f"rr_to_tp drift > tolerance {tol}: expected {expected}, got {rr} "
            f"(entry={idea.get('entry_price')}, stop={idea.get('stop_loss')}, "
            f"tps={idea.get('take_profit_ideal') or idea.get('take_profit')})"
        )


# --- strategy-trend-follow ----------------------------------------------------


class TestStrategyTrendFollow:
    """trend-follow ladder: 1.5R / 2.5R / 4R (long and short)."""

    def test_long_rr_to_tp(self, _patched_skill_loader):
        tq_pattern = {
            "present": True,
            "confidence": 3,
            "classification": "HEALTHY_UPTREND",
            "max_confidence": 5,
            "type": "trend",
        }
        bo_pattern = {"present": False}
        _patched_skill_loader["market-trend-quality"] = type(
            "TQ", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": tq_pattern, "input_scores": {}})}
        )()
        _patched_skill_loader["market-breakout"] = type(
            "BO", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": bo_pattern})}
        )()

        mod = _load_strat_lib("trend-follow")
        candles = _make_candles(n=250, base=100.0, lock_close=100.5)
        result = mod.analyze(candles, ticker="TREND", interval="1d", period="1y")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected at least one long idea on HEALTHY_UPTREND"
        for idea in long_ideas:
            _assert_rr_matches(idea, [1.5, 2.5, 4.0])

    def test_short_rr_to_tp(self, _patched_skill_loader):
        tq_pattern = {
            "present": True,
            "confidence": 3,
            "classification": "HEALTHY_DOWNTREND",
            "max_confidence": 5,
            "type": "trend",
        }
        bo_pattern = {"present": False}
        _patched_skill_loader["market-trend-quality"] = type(
            "TQ", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": tq_pattern, "input_scores": {}})}
        )()
        _patched_skill_loader["market-breakout"] = type(
            "BO", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": bo_pattern})}
        )()

        mod = _load_strat_lib("trend-follow")
        candles = _make_candles(n=250, base=100.0, lock_close=99.5)
        result = mod.analyze(candles, ticker="TREND", interval="1d", period="1y")
        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas, "expected at least one short idea on HEALTHY_DOWNTREND"
        for idea in short_ideas:
            _assert_rr_matches(idea, [1.5, 2.5, 4.0])


# --- strategy-breakout-confirm -----------------------------------------------


class TestStrategyBreakoutConfirm:
    """breakout-confirm ladder: 1.5R / 2.5R / 4R (long and short)."""

    def test_long_rr_to_tp(self, _patched_skill_loader):
        bo_pattern = {"present": True, "confidence": 4, "classification": "BULLISH_BREAKOUT", "direction": "bull"}
        sqz_signal = {"signal": "BULLISH"}
        vol = {"volume_ratio": 1.5, "obv_trend": "rising"}
        _patched_skill_loader["market-breakout"] = type(
            "BO", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": bo_pattern})}
        )()
        _patched_skill_loader["market-squeeze"] = type(
            "SQ", (), {"analyze": staticmethod(lambda c, **_kw: sqz_signal)}
        )()
        _patched_skill_loader["market-volume"] = type("V", (), {"analyze": staticmethod(lambda c, **_kw: vol)})()

        mod = _load_strat_lib("breakout-confirm")
        candles = _make_candles(n=250, base=100.0, lock_close=100.5)
        result = mod.analyze(candles, ticker="BREAK", interval="1d", period="1y")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected a long idea on bullish breakout + vol + squeeze"
        for idea in long_ideas:
            _assert_rr_matches(idea, [1.5, 2.5, 4.0])

    def test_short_rr_to_tp(self, _patched_skill_loader):
        bo_pattern = {"present": True, "confidence": 4, "classification": "BEARISH_BREAKDOWN", "direction": "bear"}
        sqz_signal = {"signal": "BEARISH"}
        vol = {"volume_ratio": 1.5, "obv_trend": "falling"}
        _patched_skill_loader["market-breakout"] = type(
            "BO", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": bo_pattern})}
        )()
        _patched_skill_loader["market-squeeze"] = type(
            "SQ", (), {"analyze": staticmethod(lambda c, **_kw: sqz_signal)}
        )()
        _patched_skill_loader["market-volume"] = type("V", (), {"analyze": staticmethod(lambda c, **_kw: vol)})()

        mod = _load_strat_lib("breakout-confirm")
        candles = _make_candles(n=250, base=100.0, lock_close=99.5)
        result = mod.analyze(candles, ticker="BREAK", interval="1d", period="1y")
        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas, "expected a short idea on bearish breakdown + vol + squeeze"
        for idea in short_ideas:
            _assert_rr_matches(idea, [1.5, 2.5, 4.0])


# --- strategy-accumulation-swing ---------------------------------------------


class TestStrategyAccumulationSwing:
    """accumulation-swing ladder: 2R / 3R / 5R (long only)."""

    def test_long_rr_to_tp(self, _patched_skill_loader):
        accum_pattern = {"present": True, "confidence": 3, "classification": "SPRING"}
        tq_pattern = {
            "present": True,
            "confidence": 4,
            "classification": "HEALTHY_UPTREND",
            "max_confidence": 5,
            "type": "trend",
        }
        _patched_skill_loader["market-accumulation"] = type(
            "A", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": accum_pattern})}
        )()
        _patched_skill_loader["market-trend-quality"] = type(
            "TQ", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": tq_pattern, "input_scores": {}})}
        )()

        mod = _load_strat_lib("accumulation-swing")
        candles = _make_candles(n=250, base=100.0, lock_close=100.5)
        result = mod.analyze(candles, ticker="ACCUM", interval="1d", period="1y")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected a long idea on SPRING + HEALTHY_UPTREND"
        for idea in long_ideas:
            _assert_rr_matches(idea, [2.0, 3.0, 5.0])


# --- strategy-liquidity-sweep ------------------------------------------------


class TestStrategyLiquiditySweep:
    """liquidity-sweep ladder: 2R / 3R / 4R (long only)."""

    def test_long_rr_to_tp(self, _patched_skill_loader):
        sweep_pattern = {"present": True, "confidence": 4, "classification": "SUPPORT_SWEEP"}
        accum_pattern = {"present": True, "confidence": 4, "classification": "SPRING"}
        vol = {"volume_ratio": 1.8, "obv_trend": "rising"}
        _patched_skill_loader["market-liquidity-sweep"] = type(
            "L", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": sweep_pattern})}
        )()
        _patched_skill_loader["market-accumulation"] = type(
            "A", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": accum_pattern})}
        )()
        _patched_skill_loader["market-volume"] = type("V", (), {"analyze": staticmethod(lambda c, **_kw: vol)})()

        mod = _load_strat_lib("liquidity-sweep")
        candles = _make_candles(n=250, base=100.0, lock_close=100.5)
        result = mod.analyze(candles, ticker="SWEEP", interval="1d", period="1y")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected a long idea on sweep + accumulation + vol"
        for idea in long_ideas:
            _assert_rr_matches(idea, [2.0, 3.0, 4.0])


# --- strategy-mean-reversion -------------------------------------------------


class TestStrategyMeanReversion:
    """mean-reversion ladder: 1R / 2R / 3R (long and short) when the
    resistance/support fall-back fires (i.e. no valid S/R for TP3)."""

    def test_long_rr_to_tp(self, _patched_skill_loader):
        # No resistance → far_target falls back to entry + 3R per audit 2026-06-21 #5.
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": None})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()

        mod = _load_strat_lib("mean-reversion")
        # Lock close just above support (within 2% band) so the long branch fires.
        candles = _make_candles(n=250, base=99.5, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected a long idea on RSI<=30 near support"
        for idea in long_ideas:
            _assert_rr_matches(idea, [1.0, 2.0, 3.0])

    def test_short_rr_to_tp(self, _patched_skill_loader):
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 75})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": None, "nearest_resistance": 100.5})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()

        mod = _load_strat_lib("mean-reversion")
        candles = _make_candles(n=250, base=100.5, lock_close=100.3, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas, "expected a short idea on RSI>=70 near resistance"
        for idea in short_ideas:
            _assert_rr_matches(idea, [1.0, 2.0, 3.0])


# --- strategy-exhaustion-fade ------------------------------------------------


class TestStrategyExhaustionFade:
    """exhaustion-fade ladder: 1R / 2R / 3R (long and short)."""

    def test_short_rr_to_tp(self, _patched_skill_loader):
        exh_pattern = {"present": True, "confidence": 4, "classification": "BLOWOFF"}
        _patched_skill_loader["market-exhaustion"] = type(
            "E", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": exh_pattern})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 95.0, "nearest_resistance": 100.5})}
        )()
        _patched_skill_loader["market-trend"] = type(
            "T", (), {"analyze": staticmethod(lambda c, **_kw: {"score": 2})}
        )()

        mod = _load_strat_lib("exhaustion-fade")
        # Close at resistance (100.5) so the short branch fires.
        candles = _make_candles(n=250, base=100.5, lock_close=100.5, half_range=1.5)
        result = mod.analyze(candles, ticker="EXH", interval="1d", period="1y")
        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas, "expected a short idea on BLOWOFF at resistance with uptrend"
        for idea in short_ideas:
            _assert_rr_matches(idea, [1.0, 2.0, 3.0])

    def test_long_rr_to_tp(self, _patched_skill_loader):
        exh_pattern = {"present": True, "confidence": 4, "classification": "CAPITULATION"}
        _patched_skill_loader["market-exhaustion"] = type(
            "E", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": exh_pattern})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": 110.0})}
        )()
        _patched_skill_loader["market-trend"] = type(
            "T", (), {"analyze": staticmethod(lambda c, **_kw: {"score": -2})}
        )()

        mod = _load_strat_lib("exhaustion-fade")
        # Close at support (99.5) so the long branch fires.
        candles = _make_candles(n=250, base=99.5, lock_close=99.5, half_range=1.5)
        result = mod.analyze(candles, ticker="EXH", interval="1d", period="1y")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected a long idea on CAPITULATION at support with downtrend"
        for idea in long_ideas:
            _assert_rr_matches(idea, [1.0, 2.0, 3.0])
