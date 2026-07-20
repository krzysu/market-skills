"""Tests for strategy-funding-carry L3 — perp funding rate carry trade."""

import importlib.util
import math
import os
import random


def _load_strat_lib():
    lib_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "skills",
        "strategy-funding-carry",
        "lib.py",
    )
    spec = importlib.util.spec_from_file_location("strategy_funding_carry_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(base_price=100.0, n=250, trend="uptrend", drift=0.002, seed=42):
    """Generate deterministic synthetic candles with optional drift direction."""
    rng = random.Random(seed)
    vals = []
    price = base_price
    for _ in range(n):
        delta = drift if trend == "uptrend" else (-drift if trend == "downtrend" else 0.0)
        price = price * (1.0 + delta) + rng.uniform(-1.0, 1.0)
        vals.append(
            [
                int(vals[-1][0] + 86400) if vals else 0,
                price,
                price + rng.uniform(0, 1),
                price - rng.uniform(0, 1),
                price,
                rng.randint(100000, 500000),
            ]
        )
    return vals


def _make_flat_candles(n=250, base=100.0, half_range=5.0):
    """Build candles where every bar has a constant true range of 2*half_range.

    With a constant per-bar range, ATR(14) = 2*half_range exactly, giving a
    deterministic stop distance for the 2×ATR carry stop. The flat close
    (every bar ends at ``base``) pins the last close to ``base`` so
    ``entry = closes[-1] = base``.
    """
    out = []
    for i in range(n):
        out.append(
            [
                i * 86400,
                base,
                base + half_range,
                base - half_range,
                base,
                200_000,
            ]
        )
    return out


def _patch_funding(monkeypatch, mod, rate):
    """Patch the strategy module's ``fetch_funding_rate`` binding.

    The lib imports ``fetch_funding_rate`` by name at module load, so the
    binding lives on the strategy module — patching ``analysis.data`` after
    load would not reach it. ``rate=None`` simulates a fetch failure.
    """
    if rate is None:
        monkeypatch.setattr(mod, "fetch_funding_rate", lambda ticker: None)
    else:
        monkeypatch.setattr(mod, "fetch_funding_rate", lambda ticker: {"funding_rate": rate})


class TestStrategyFundingCarry:
    def test_analyze_returns_ideas_list(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_candles(n=250, trend="uptrend")
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert "ideas" in result
        assert isinstance(result["ideas"], list)
        assert "narrative" in result

    def test_ideas_have_required_keys(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_candles(n=250, trend="uptrend")
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        for idea in result["ideas"]:
            assert "direction" in idea
            assert "conviction" in idea
            assert "entry_type" in idea
            assert "entry_price" in idea
            assert "stop_loss" in idea
            assert "take_profit" in idea
            assert "reasoning" in idea
            assert "source_skills" in idea

    def test_insufficient_data(self):
        mod = _load_strat_lib()
        result = mod.analyze([], ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"] == []

    def test_prefixed_ticker_routed_to_fetch_funding_rate(self, monkeypatch):
        from unittest.mock import Mock

        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        prefixed = "hl:HYPE"
        fetch_mock = Mock(return_value={"funding_rate": -0.0015})
        monkeypatch.setattr(mod, "fetch_funding_rate", fetch_mock)

        result = mod.analyze(candles, ticker=prefixed, interval="1d", period="1y")
        fetch_mock.assert_called_once_with(prefixed)
        assert result["ideas"], f"expected an idea for prefixed ticker, got {result}"

        fetch_mock_bare = Mock(return_value={"funding_rate": -0.0015})
        monkeypatch.setattr(mod, "fetch_funding_rate", fetch_mock_bare)
        result_bare = mod.analyze(candles, ticker="HYPE", interval="1d", period="1y")

        assert result_bare["ideas"], f"expected an idea for bare ticker, got {result_bare}"
        idea_p = result["ideas"][0]
        idea_b = result_bare["ideas"][0]
        assert idea_p["pair"] == prefixed
        assert idea_b["pair"] == "HYPE"
        assert idea_p["direction"] == idea_b["direction"]
        assert idea_p["conviction"] == idea_b["conviction"]
        assert idea_p["entry_price"] == idea_b["entry_price"]
        assert idea_p["stop_loss"] == idea_b["stop_loss"]
        assert idea_p["take_profit"] == idea_b["take_profit"]


class TestFundingConviction:
    def test_extreme_positive_funding_produces_short(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected a short idea on extreme positive funding, got {result}"
        idea = result["ideas"][0]
        assert idea["direction"] == "short"
        assert idea["conviction"] >= 3

    def test_extreme_negative_funding_produces_long(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected a long idea on extreme negative funding, got {result}"
        idea = result["ideas"][0]
        assert idea["direction"] == "long"
        assert idea["conviction"] >= 3

    def test_moderate_funding_lower_conviction(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.0003)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected an idea on moderate funding, got {result}"
        idea = result["ideas"][0]
        assert idea["conviction"] >= 2

    def test_neutral_funding_no_ideas(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.00001)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"] == []

    def test_funding_unavailable_no_ideas(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, None)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"] == []

    def test_boundary_0001_conviction_2(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.0001)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected an idea at the 0.0001 boundary, got {result}"
        assert result["ideas"][0]["conviction"] >= 2

    def test_boundary_00009_neutral(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.00009)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"] == []

    def test_boundary_0005_conviction_3(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.0005)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected an idea at the 0.0005 boundary, got {result}"
        assert result["ideas"][0]["conviction"] >= 3

    def test_boundary_001_conviction_4(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.001)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected an idea at the 0.001 boundary, got {result}"
        assert result["ideas"][0]["conviction"] >= 4


class TestFundingCarryStopATR:
    def test_long_stop_is_entry_minus_2_atr(self, monkeypatch):
        mod = _load_strat_lib()
        from analysis.indicators import compute_atr_from_candles

        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        atr = compute_atr_from_candles(candles, period=14)
        assert atr is not None
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"]
        idea = result["ideas"][0]
        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        expected = entry - 2 * atr
        assert math.isclose(stop, expected, abs_tol=1e-6), f"long stop should be entry - 2*ATR = {expected}, got {stop}"

    def test_short_stop_is_entry_plus_2_atr(self, monkeypatch):
        mod = _load_strat_lib()
        from analysis.indicators import compute_atr_from_candles

        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        atr = compute_atr_from_candles(candles, period=14)
        assert atr is not None
        _patch_funding(monkeypatch, mod, 0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"]
        idea = result["ideas"][0]
        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        expected = entry + 2 * atr
        assert math.isclose(stop, expected, abs_tol=1e-6), (
            f"short stop should be entry + 2*ATR = {expected}, got {stop}"
        )

    def test_stop_too_tight_filtered(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=0.01)
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"] == [], f"expected idea to be filtered for too-tight stop, got {result['ideas']}"
        assert "noise risk" in result["narrative"], (
            f"narrative should mention the stop-distance rejection, got {result['narrative']!r}"
        )


class TestFundingCarryTpLadder:
    def test_long_tp_ladder_ascending_three_targets(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"]
        idea = result["ideas"][0]
        assert idea["direction"] == "long"
        tps = idea["take_profit"]
        assert len(tps) == 3, f"expected 3 TP targets, got {tps}"
        assert tps == sorted(tps), f"long TPs must be ascending, got {tps}"

    def test_short_tp_ladder_descending_three_targets(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"]
        idea = result["ideas"][0]
        assert idea["direction"] == "short"
        tps = idea["take_profit"]
        assert len(tps) == 3, f"expected 3 TP targets, got {tps}"
        assert tps == sorted(tps, reverse=True), f"short TPs must be descending, got {tps}"

    def test_long_tp1_at_1_5r(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"]
        idea = result["ideas"][0]
        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        risk = entry - stop
        tp1_ideal = idea["take_profit_ideal"][0]
        rr = (tp1_ideal - entry) / risk
        assert math.isclose(rr, 1.5, abs_tol=1e-6), (
            f"long TP1 should be 1.5R, got rr={rr} (tp1={tp1_ideal}, entry={entry}, risk={risk})"
        )

    def test_short_tp1_at_1_5r(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=5.0)
        _patch_funding(monkeypatch, mod, 0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"]
        idea = result["ideas"][0]
        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        risk = stop - entry
        tp1_ideal = idea["take_profit_ideal"][0]
        rr = (entry - tp1_ideal) / risk
        assert math.isclose(rr, 1.5, abs_tol=1e-6), (
            f"short TP1 should be 1.5R, got rr={rr} (tp1={tp1_ideal}, entry={entry}, risk={risk})"
        )

    def test_low_atr_clamps_tp3_to_5pct_floor(self, monkeypatch):
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=0.2)
        monkeypatch.setattr(mod, "enforce_min_stop_distance", lambda idea: (True, ""))

        _patch_funding(monkeypatch, mod, -0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected a long idea with clamped TP3, got {result}"
        idea = result["ideas"][0]
        assert idea["direction"] == "long"
        entry = idea["entry_price"]
        tp3 = idea["take_profit"][2]
        assert tp3 >= entry * 1.05, f"long TP3 should be clamped to >= entry*1.05, got tp3={tp3}, entry={entry}"

        _patch_funding(monkeypatch, mod, 0.0015)
        result = mod.analyze(candles, ticker="BTC/USDT", interval="1d", period="1y")
        assert result["ideas"], f"expected a short idea with clamped TP3, got {result}"
        idea = result["ideas"][0]
        assert idea["direction"] == "short"
        entry = idea["entry_price"]
        tp3 = idea["take_profit"][2]
        assert tp3 <= entry * 0.95, f"short TP3 should be clamped to <= entry*0.95, got tp3={tp3}, entry={entry}"
