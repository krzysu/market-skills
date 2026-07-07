"""Tests for strategy-mean-reversion's CAPE-valuation soft-veto hook.

The strategy reads ``market-valuation`` and attaches a ``veto_reasons``
tag when SP500 CAPE z-score disagrees with the trade direction. The
tag is informational only — the LLM agent brain decides whether to
act.
"""

import importlib.util
import os

import pytest


def _load_strat_lib():
    lib_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "skills",
        "strategy-mean-reversion",
        "lib.py",
    )
    spec = importlib.util.spec_from_file_location("strategy_mean_reversion_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=250, base=100.0, half_range=1.5, lock_close=None, seed=42):
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
    canned: dict = {}
    import analysis.skill_loader as sl

    monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))
    return canned


def _valuation_skill(zscore: float | None, regime: str | None) -> object:
    """Build a fake market-valuation skill returning a canned regime."""

    def _analyze():
        return {
            "timestamp": "2026-07-07T00:00:00+00:00",
            "inputs": {"sp500": 5400.0, "cape": 41.0, "cape_mean_50y": 21.0, "cape_std_50y": 9.0},
            "regime": {"cape_zscore": zscore, "regime": regime},
            "errors": [],
            "incomplete": False,
            "regime_note": "stub",
        }

    return type("V", (), {"analyze": staticmethod(_analyze)})()


class TestLongValuationTag:
    """Long mean-reversion + CAPE OVEREXTENDED → veto_reasons tag."""

    def test_long_tagged_when_cape_overextended(self, _patched_skill_loader):
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": None})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()
        _patched_skill_loader["market-valuation"] = _valuation_skill(zscore=2.33, regime="OVEREXTENDED")

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=99.5, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected a long idea on RSI<=30 near support"
        idea = long_ideas[0]
        assert "veto_reasons" in idea, "expected veto_reasons tag when CAPE OVEREXTENDED"
        assert any(t.startswith("sp500_cape_overextended_z") for t in idea["veto_reasons"]), (
            f"Expected sp500_cape_overextended_z tag, got {idea['veto_reasons']}"
        )
        # Tag should include the z-score value.
        assert any("2.33" in t for t in idea["veto_reasons"]), (
            f"Expected z-score value in tag, got {idea['veto_reasons']}"
        )

    def test_long_not_tagged_when_cape_fair(self, _patched_skill_loader):
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": None})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()
        _patched_skill_loader["market-valuation"] = _valuation_skill(zscore=0.5, regime="FAIR")

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=99.5, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas
        # No CAPE tag when regime isn't OVEREXTENDED.
        assert not any("sp500_cape" in t for t in long_ideas[0].get("veto_reasons", [])), (
            f"Did not expect CAPE tag when regime=FAIR, got {long_ideas[0].get('veto_reasons')}"
        )

    def test_long_not_tagged_when_cape_oversold(self, _patched_skill_loader):
        """Long + CAPE OVERSOLD: regime supports the trade direction — no tag."""
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": None})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()
        _patched_skill_loader["market-valuation"] = _valuation_skill(zscore=-2.5, regime="OVERSOLD")

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=99.5, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas
        assert not any("sp500_cape" in t for t in long_ideas[0].get("veto_reasons", []))


class TestShortValuationTag:
    """Short mean-reversion + CAPE OVERSOLD → veto_reasons tag."""

    def test_short_tagged_when_cape_oversold(self, _patched_skill_loader):
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 75})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": None, "nearest_resistance": 100.5})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()
        _patched_skill_loader["market-valuation"] = _valuation_skill(zscore=-2.5, regime="OVERSOLD")

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=100.5, lock_close=100.3, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas
        idea = short_ideas[0]
        assert any(t.startswith("sp500_cape_oversold_z") for t in idea.get("veto_reasons", [])), (
            f"Expected sp500_cape_oversold_z tag, got {idea.get('veto_reasons')}"
        )

    def test_short_not_tagged_when_cape_overextended(self, _patched_skill_loader):
        """Short + CAPE OVEREXTENDED: regime supports the trade — no tag."""
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 75})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": None, "nearest_resistance": 100.5})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()
        _patched_skill_loader["market-valuation"] = _valuation_skill(zscore=2.5, regime="OVEREXTENDED")

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=100.5, lock_close=100.3, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas
        assert not any("sp500_cape" in t for t in short_ideas[0].get("veto_reasons", []))


class TestValuationUnavailable:
    """No market-valuation skill or None result → no CAPE tag, no crash."""

    def test_no_skill_returns_no_tag(self, _patched_skill_loader):
        # market-valuation not loaded — strategy should treat as no view.
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": None})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=99.5, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas
        assert not any("sp500_cape" in t for t in long_ideas[0].get("veto_reasons", []))

    def test_skill_returns_unknown_regime_no_tag(self, _patched_skill_loader):
        _patched_skill_loader["market-rsi"] = type(
            "R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})}
        )()
        _patched_skill_loader["market-s-r"] = type(
            "S", (), {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": None})}
        )()
        _patched_skill_loader["market-volatility"] = type(
            "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
        )()
        # UNKNOWN regime — sources down, regime downgrade from incomplete signal.
        _patched_skill_loader["market-valuation"] = _valuation_skill(zscore=None, regime="UNKNOWN")

        mod = _load_strat_lib()
        candles = _make_candles(n=250, base=99.5, lock_close=99.7, half_range=1.5)
        result = mod.analyze(candles, ticker="MR", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas
        assert not any("sp500_cape" in t for t in long_ideas[0].get("veto_reasons", []))
