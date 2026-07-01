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

    def test_sub_signal_weights_sum_to_one(self, candles):
        """Sub-signal weights must sum to 1.0 — confidence is the absolute
        |weighted_sum| on this scale (see market-exhaustion/lib.py:50).
        """
        mod = _load_l2_skill("market-exhaustion")
        result = mod.analyze(candles)
        signals = result.get("signals", {})
        assert signals, "expected non-empty signals dict"
        total = sum(s["weight"] for s in signals.values() if isinstance(s, dict))
        assert abs(total - 1.0) < 1e-3, f"sub-signal weights must sum to 1.0, got {total} (signals={signals})"

    def test_two_sub_above_030_fires_present(self, monkeypatch):
        """Regression for BUG-2026-06-24-02 family (same shape as ALGO 4h
        liquidity-sweep, BUG-2026-06-24-01): 2 sub-signals present at
        weighted_sum in (0.30, 0.50) must fire present=True.

        Setup: rsi_extreme (0.2778) + momentum_divergence (0.1667) = 0.4445.
        Pre-fix: ratio 0.4445 < 0.5 → present=False with 2 subs populated
        (Pattern B Shape #1 ghost). Post-fix: n_present >= 2 AND wsum > 0.30
        → present=True with classification IMPULSE_EXHAUSTION.
        """
        import analysis.skill_loader as sl

        # rsi_14=75 → rsi_extreme present (0.2778)
        # histogram_flip present → momentum_divergence present (0.1667)
        # volume and volatility deliberately neutral → those subs absent.
        fake_vol = {"volume_ratio": 0.8, "obv_trend": "flat", "regime": "NORMAL"}
        fake_volty = {"regime": "NORMAL"}
        fake_macd = {"histogram_flip": "positive_to_negative"}
        fake_rsi = {"rsi_14": 75}
        canned = {
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-volatility": type("Y", (), {"analyze": staticmethod(lambda c, **_kw: fake_volty)})(),
            "market-macd": type("M", (), {"analyze": staticmethod(lambda c, **_kw: fake_macd)})(),
            "market-rsi": type("R", (), {"analyze": staticmethod(lambda c, **_kw: fake_rsi)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_l2_skill("market-exhaustion")
        candles = [[i * 86400, 100.0, 101, 99, 100, 200000] for i in range(60)]
        result = mod.analyze(candles, interval="4h", period="6mo")

        sigs = result["signals"]
        present_count = sum(1 for s in sigs.values() if s.get("present"))
        weighted_sum = sum(s["weight"] for s in sigs.values() if s.get("present"))
        assert present_count == 2, f"setup error — expected 2 present, got {present_count}: {sigs}"
        assert 0.30 < weighted_sum < 0.50, (
            f"setup error — expected weighted_sum in (0.30, 0.50), got {weighted_sum:.4f}"
        )
        assert result["pattern"]["present"] is True, (
            f"BUG regression: rsi_extreme + momentum_divergence (wsum={weighted_sum:.4f}) "
            f"must fire present=True post-fix, got {result['pattern']}"
        )
        assert result["pattern"]["classification"] == "IMPULSE_EXHAUSTION"

    def test_two_sub_count_gate_protects_single_sub(self, monkeypatch):
        """The trigger is count-gated: a single sub-signal at wsum > 0.30 must
        NOT classify. Guards against a future refactor that drops the
        ``n_present >= 2`` check.
        """
        import analysis.skill_loader as sl

        # rsi_14=75 alone → only rsi_extreme present (0.2778 — actually below 0.30).
        # Use volume climax (0.3333) as the lone sub: weight > 0.30, count == 1.
        fake_vol = {"volume_ratio": 3.0, "obv_trend": "rising", "regime": "CLIMAX"}
        fake_volty = {"regime": "NORMAL"}
        fake_macd = {"histogram_flip": None}
        fake_rsi = {"rsi_14": 50}  # neutral, rsi_extreme absent
        canned = {
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-volatility": type("Y", (), {"analyze": staticmethod(lambda c, **_kw: fake_volty)})(),
            "market-macd": type("M", (), {"analyze": staticmethod(lambda c, **_kw: fake_macd)})(),
            "market-rsi": type("R", (), {"analyze": staticmethod(lambda c, **_kw: fake_rsi)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_l2_skill("market-exhaustion")
        candles = [[i * 86400, 100.0, 101, 99, 100, 200000] for i in range(60)]
        result = mod.analyze(candles, interval="4h", period="6mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 1, f"setup error — expected 1 present, got {present_count}"
        assert result["pattern"]["present"] is False, (
            f"Count gate must protect single-sub case, got present=True with signals={result['signals']}"
        )


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

    def test_two_sub_signals_can_fire_pattern(self, monkeypatch):
        """spring_shakeout + low_vol_after_distribution (combined weight 0.45)
        must fire present=True (threshold 0.4). Two corroborating L1s crossing
        the threshold is meaningful even with three others absent.
        """
        import analysis.skill_loader as sl

        fake_sr = {
            "nearest_support": 99.0,
            "sits_on_level": True,
        }
        fake_vol = {"volume_ratio": 1.2, "obv_trend": "rising"}
        fake_volty = {"regime": "LOW", "trend": "compressing"}
        fake_trend = {"score": 0, "alignment": "UNKNOWN", "price_above_emas": None}

        # For spring: recent lows dipped below 99 then reclaimed. Build candles where
        # the last 5 lows go 98.5, 98.8, 99.0, 99.2, 99.5 and closes are 99.5+.
        candles = []
        for i in range(60):
            price = 100.0 if i < 55 else (98.5 if i == 55 else (99.5 if i >= 58 else 99.0))
            candles.append([i * 86400, price, price + 0.3, price - 0.3, price, 200000])

        canned = {
            "market-s-r": type("S", (), {"analyze": staticmethod(lambda c, **_kw: fake_sr)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-volatility": type("Y", (), {"analyze": staticmethod(lambda c, **_kw: fake_volty)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_l2_skill("market-accumulation")
        result = mod.analyze(candles, interval="1h", period="1mo")
        sigs = result["signals"]
        spring_fired = sigs["spring_shakeout"]["present"]
        low_vol_fired = sigs["low_vol_after_distribution"]["present"]
        assert spring_fired and low_vol_fired, f"test setup error — expected both spring + low_vol to fire, got {sigs}"
        assert result["pattern"]["present"] is True, (
            f"spring_shakeout + low_vol_after_distribution should fire present=True "
            f"at threshold 0.4 (weight 0.45). Got present={result['pattern']['present']}"
        )

    def test_reaccum_plus_low_vol_sub_shape_fires(self, monkeypatch):
        """reaccumulation + low_vol_after_distribution both firing (combined
        weight 0.30, below the 0.40 threshold) must still classify as
        REACCUMULATION via the recognized sub-shape path. Two corroborating L1s
        are meaningful even when neither crosses the threshold alone.
        """
        import analysis.skill_loader as sl

        # S/R not on level — spring should NOT fire.
        fake_sr = {"nearest_support": 95.0, "sits_on_level": False}
        # Volume not pumping — absorption / sos should NOT fire.
        fake_vol = {"volume_ratio": 1.0, "obv_trend": "flat"}
        # Volatility regime: LOW + compressing — low_vol_after_distribution SHOULD fire.
        fake_volty = {"regime": "LOW", "trend": "compressing"}
        # Partial bull trend with 3 of N EMAs above — reaccumulation SHOULD fire.
        fake_trend = {"score": 1, "alignment": "PARTIAL_BULL", "price_above_emas": 3}

        candles = []
        for i in range(60):
            price = 100.0 + (i % 5) * 0.05
            candles.append([i * 86400, price, price + 0.3, price - 0.3, price, 200000])

        canned = {
            "market-s-r": type("S", (), {"analyze": staticmethod(lambda c, **_kw: fake_sr)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-volatility": type("Y", (), {"analyze": staticmethod(lambda c, **_kw: fake_volty)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_l2_skill("market-accumulation")
        result = mod.analyze(candles, interval="1h", period="1mo")
        sigs = result["signals"]
        assert sigs["reaccumulation"]["present"], f"test setup error — expected reaccumulation, got {sigs}"
        assert sigs["low_vol_after_distribution"]["present"], f"test setup error — expected low_vol, got {sigs}"
        assert result["pattern"]["present"] is True, (
            f"reaccumulation + low_vol sub-shape should fire present=True. Got {result['pattern']}"
        )
        assert result["pattern"]["classification"] == "REACCUMULATION", (
            f"Sub-shape must classify as REACCUMULATION, got {result['pattern']['classification']}"
        )

    def test_two_sub_above_030_fires_present(self, monkeypatch):
        """Regression for BUG-2026-06-24-02 family (same shape as ALGO 4h
        liquidity-sweep, BUG-2026-06-24-01): 2 sub-signals present at
        weighted_sum in (0.30, 0.40) must fire present=True.

        Setup: absorption (0.20) + low_vol_after_distribution (0.15) = 0.35.
        Pre-fix: ratio 0.35 < 0.40 → present=False with 2 subs populated
        (Pattern B Shape #1 ghost). Post-fix: n_present >= 2 AND wsum > 0.30
        → present=True with classification UTAD (low_vol alone).
        """
        import analysis.skill_loader as sl

        # S/R not on level — spring should NOT fire.
        fake_sr = {"nearest_support": None, "sits_on_level": False}
        # vr > 1.5 + LOW volatility regime → absorption SHOULD fire (0.20)
        fake_vol = {"volume_ratio": 2.0, "obv_trend": "rising"}
        fake_volty = {"regime": "LOW", "trend": "compressing"}
        # Bearish trend — sign_of_strength NOT firing (trend_score < 0);
        # reaccumulation NOT firing (alignment != PARTIAL_BULL).
        fake_trend = {"score": -3, "alignment": "FULL_BEAR", "price_above_emas": 0}
        canned = {
            "market-s-r": type("S", (), {"analyze": staticmethod(lambda c, **_kw: fake_sr)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-volatility": type("Y", (), {"analyze": staticmethod(lambda c, **_kw: fake_volty)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_l2_skill("market-accumulation")
        candles = [[i * 86400, 100.0, 101, 99, 100, 200000] for i in range(60)]
        result = mod.analyze(candles, interval="4h", period="6mo")

        sigs = result["signals"]
        present_count = sum(1 for s in sigs.values() if s.get("present"))
        weighted_sum = sum(s["weight"] for s in sigs.values() if s.get("present"))
        assert present_count == 2, f"setup error — expected 2 present, got {present_count}: {sigs}"
        assert 0.30 < weighted_sum < 0.40, (
            f"setup error — expected weighted_sum in (0.30, 0.40), got {weighted_sum:.4f}"
        )
        assert result["pattern"]["present"] is True, (
            f"BUG regression: absorption + low_vol (wsum={weighted_sum:.4f}) "
            f"must fire present=True post-fix, got {result['pattern']}"
        )

    def test_two_sub_count_gate_protects_single_sub(self, monkeypatch):
        """The trigger is count-gated: a single sub-signal at wsum > 0.30 must
        NOT classify. Guards against a future refactor that drops the
        ``n_present >= 2`` check.
        """
        import analysis.skill_loader as sl

        # spring_shakeout (0.30) alone — wsum just over the 0.30 threshold, but
        # count == 1. The count gate must keep present=False.
        fake_sr = {"nearest_support": 99.0, "sits_on_level": True}
        # vol/vola/trend set up so the other four subs are absent.
        fake_vol = {"volume_ratio": 0.8, "obv_trend": "flat"}
        fake_volty = {"regime": "NORMAL"}
        fake_trend = {"score": -3, "alignment": "FULL_BEAR", "price_above_emas": 0}
        canned = {
            "market-s-r": type("S", (), {"analyze": staticmethod(lambda c, **_kw: fake_sr)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-volatility": type("Y", (), {"analyze": staticmethod(lambda c, **_kw: fake_volty)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        # Build candles where the last 5 lows dip below 99 then reclaim.
        candles = []
        for i in range(60):
            price = 100.0 if i < 55 else (98.5 if i == 55 else (99.5 if i >= 58 else 99.0))
            candles.append([i * 86400, price, price + 0.3, price - 0.3, price, 200000])

        mod = _load_l2_skill("market-accumulation")
        result = mod.analyze(candles, interval="1h", period="1mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 1, f"setup error — expected 1 present, got {present_count}"
        assert result["pattern"]["present"] is False, (
            f"Count gate must protect single-sub case, got present=True with signals={result['signals']}"
        )


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
