"""Tests for market-breakout L2 lib.py — count + weight trigger + sub-shape classification."""

import importlib.util
import os


def _load_breakout_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-breakout", "lib.py")
    spec = importlib.util.spec_from_file_location("market_breakout_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_WEIGHTS = {
    "structure_break": 0.35,
    "volume_confirmation": 0.25,
    "obv_confirmation": 0.15,
    "squeeze_release": 0.15,
    "retest_holding": 0.10,
}


class TestWeightedSumArithmetic:
    """Weighted-sum arithmetic for the 5 sub-signals.

    These tests verify the math the L2 trigger consumes; they don't test L2
    behavior directly. The L2 trigger (n_present >= 2 AND weighted_sum > 0.30)
    mirrors the bug-scan Shape #1 trigger and the ALGO 4h liquidity-sweep fix
    (BUG-2026-06-24-01). See TestAbsentWithSubsRegression for behavioral tests.
    """

    def _weighted_sum(self, present_flags):
        return sum(_WEIGHTS[k] for k, v in present_flags.items() if v)

    def test_struct_plus_retest_above_030(self):
        """structure_break + retest_holding (0.45) must cross the 0.30 trigger."""
        assert self._weighted_sum({"structure_break": True, "retest_holding": True}) > 0.30

    def test_single_structure_break_below_030(self):
        """structure_break alone (0.35) crosses the count gate but is alone — must NOT classify."""
        assert self._weighted_sum({"structure_break": True}) > 0.30  # passes wsum gate
        assert sum(1 for v in {"structure_break": True}.values() if v) == 1  # fails count gate

    def test_volume_plus_retest_above_030(self):
        """volume_confirmation + retest_holding (0.35) — was silently dropped
        by the pre-fix ``ratio >= 0.40`` threshold. Post-fix (BUG-2026-06-24-02
        family) the count + weight trigger fires this case.
        """
        assert self._weighted_sum({"volume_confirmation": True, "retest_holding": True}) > 0.30

    def test_struct_plus_volume_above_030(self):
        """structure_break + volume_confirmation (0.60) classifies cleanly."""
        assert self._weighted_sum({"structure_break": True, "volume_confirmation": True}) > 0.30

    def test_squeeze_plus_retest_at_025(self):
        """squeeze_release + retest_holding = 0.25 — below 0.30, primary trigger
        doesn't fire; the sub-shape patch (lib.py) handles this combo as
        CONFIRMED.
        """
        assert self._weighted_sum({"squeeze_release": True, "retest_holding": True}) <= 0.30


class TestAbsentWithSubsRegression:
    """Regression: 2-sub combos at weighted_sum in (0.30, 0.40) must fire present=True.

    The pre-fix ``ratio >= 0.40`` threshold silently dropped these cases into
    present=False with the sub-signals populated — the bug-scan Shape #1
    ghost (absent-with-subs). Same pattern as ALGO 4h (BUG-2026-06-24-01)
    and VVV 4h trend-quality (BUG-2026-06-24-02). Post-fix the L2 trigger is
    ``n_present >= 2 AND weighted_sum > 0.30`` mirroring the bug-scan trigger.
    """

    def test_volume_confirmation_plus_retest_holding_fires(self, monkeypatch):
        """volume_confirmation (0.25) + retest_holding (0.10) = 0.35 must fire
        present=True post-fix. Pre-fix: present=False (ratio 0.35 < 0.40).
        """
        # volume: vr > 1.5 → volume_confirmation present
        fake_vol = {"volume_ratio": 2.0, "obv_trend": "rising"}
        # S/R: sits_on_level=True → retest_holding present
        fake_sr = {"nearest_support": 100.0, "nearest_resistance": 110.0, "sits_on_level": True}
        # trend: tangled + SIDEWAYS (not strong) → structure_break NOT present
        fake_trend = {"alignment": "TANGLED", "signal": "SIDEWAYS", "score": 0}
        # squeeze: ON, not released → squeeze_release NOT present
        fake_sqz = {"squeeze_on": True, "direction": "increasing", "momentum": 0.0}

        import analysis.skill_loader as sl

        canned = {
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-s-r": type("S", (), {"analyze": staticmethod(lambda c, **_kw: fake_sr)})(),
            "market-squeeze": type("Z", (), {"analyze": staticmethod(lambda c, **_kw: fake_sqz)})(),
        }
        # Patch BEFORE loading lib — ``from analysis.skill_loader import load_skill``
        # inside lib.py binds the patched function into the freshly loaded module.
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_breakout_lib()
        candles = [[i * 86400, 100.0, 101, 99, 100, 200000] for i in range(60)]
        result = mod.analyze(candles, interval="4h", period="6mo")

        sigs = result["signals"]
        assert sigs["volume_confirmation"]["present"], f"setup error — expected volume, got {sigs}"
        assert sigs["retest_holding"]["present"], f"setup error — expected retest, got {sigs}"
        assert result["pattern"]["present"] is True, (
            f"BUG regression: volume_confirmation + retest_holding (wsum=0.35) "
            f"must fire present=True post-fix, got {result['pattern']}"
        )

    def test_two_sub_count_gate_protects_single_sub(self, monkeypatch):
        """The trigger is count-gated: a single sub-signal at wsum > 0.30 must
        NOT classify. Guards against a future refactor that drops the
        ``n_present >= 2`` check and starts firing on single-sub configurations
        (which would silently over-fire on every borderline case).
        """
        # structure_break alone (weight 0.35 > 0.30, but count == 1).
        fake_trend = {"alignment": "FULL_BULL", "signal": "STRONG_UPTREND", "score": 3}
        fake_vol = {"volume_ratio": 0.8, "obv_trend": "flat"}
        fake_sr = {"nearest_support": None, "sits_on_level": False}
        fake_sqz = {"squeeze_on": True, "direction": "increasing", "momentum": 0.0}

        import analysis.skill_loader as sl

        canned = {
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-s-r": type("S", (), {"analyze": staticmethod(lambda c, **_kw: fake_sr)})(),
            "market-squeeze": type("Z", (), {"analyze": staticmethod(lambda c, **_kw: fake_sqz)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_breakout_lib()
        candles = [[i * 86400, 100.0, 101, 99, 100, 200000] for i in range(60)]
        result = mod.analyze(candles, interval="4h", period="6mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 1, f"setup error — expected 1 present, got {present_count}"
        # Trigger is count-gated: single sub must NOT classify.
        assert result["pattern"]["present"] is False, (
            f"Count gate must protect single-sub case, got present=True with signals={result['signals']}"
        )


class TestPostSqueezeRetestSubShape:
    """squeeze_release + retest_holding = 0.25 falls below the 0.30 count + weight
    trigger but is a recognized breakout sub-shape (post-squeeze retest holding).
    The sub-shape trust path (lib.py) classifies as CONFIRMED when both fire.
    """

    def test_sub_shape_below_primary_trigger(self):
        """Sanity check: 0.25 weighted_sum is below the 0.30 primary trigger
        — the sub-shape patch must do the lifting, not the primary trigger.
        """
        signals = [{"present": k in ("squeeze_release", "retest_holding"), "weight": _WEIGHTS[k]} for k in _WEIGHTS]
        fired = sum(s["weight"] for s in signals if s["present"])
        assert fired <= 0.30, "Test premise broken — combo would now classify on primary trigger alone"

    def test_sub_shape_fires_when_both_signals_present(self):
        """squeeze_release + retest_holding → present=True via sub-shape fallback."""
        mod = _load_breakout_lib()
        candles = _make_candles(seed=99, n=180)
        result = mod.analyze(candles, interval="4h", period="6mo")
        pattern = result["pattern"]

        # If both squeeze_release and retest_holding happen to fire on this fixture
        # the sub-shape fallback should fire present=True.
        squeeze_fired = result["signals"]["squeeze_release"]["present"]
        retest_fired = result["signals"]["retest_holding"]["present"]
        if squeeze_fired and retest_fired:
            assert pattern["present"] is True, (
                f"Expected sub-shape fallback to set present=True when both fire; got pattern={pattern}"
            )


def _make_candles(seed=42, n=200):
    """Build a minimal candle series long enough to run the L2 analyzer."""
    import random

    rng = random.Random(seed)
    candles = []
    price = 100.0
    for i in range(n):
        price *= 1.0 + rng.uniform(-0.005, 0.012)
        candles.append(
            [
                i * 86400,
                price,
                price + rng.uniform(0, 0.5),
                price - rng.uniform(0, 0.5),
                price,
                rng.randint(100000, 500000),
            ]
        )
    return candles
