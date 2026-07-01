"""Tests for market-trend L1 lib.py — swing window, multi-swing HH/HL detection."""

import importlib.util
import os

from analysis.indicators import find_swing_highs, find_swing_lows, swing_window_for_interval


def _load_trend_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-trend", "lib.py")
    spec = importlib.util.spec_from_file_location("market_trend_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSwingWindowForInterval:
    def test_minute_intervals(self):
        for iv in ("1m", "5m", "15m", "30m"):
            assert swing_window_for_interval(iv) == 20, f"expected 20 for {iv}"

    def test_one_hour(self):
        assert swing_window_for_interval("1h") == 12

    def test_four_hour(self):
        assert swing_window_for_interval("4h") == 8

    def test_daily(self):
        assert swing_window_for_interval("1d") == 5

    def test_weekly(self):
        for iv in ("1wk", "1w"):
            assert swing_window_for_interval(iv) == 4, f"expected 4 for {iv}"

    def test_default_for_unknown(self):
        assert swing_window_for_interval("99y") == 5

    def test_case_insensitive(self):
        assert swing_window_for_interval("4H") == 8
        assert swing_window_for_interval("1D") == 5


# Three series, each with explicit local peaks spaced far enough apart for the
# swing-window=2 detector in find_swing_highs. Peaks are chosen so the detector
# returns exactly [130, 150, 170] (rising) or [170, 150, 130] (falling).
_RISING_HIGHS = [
    100,
    110,
    120,
    130,
    120,
    110,  # peak 130 at i=3
    130,
    140,
    150,
    140,
    130,  # peak 150 at i=8
    150,
    160,
    170,
    160,
    150,  # peak 170 at i=13
    170,
    180,
    190,
]
# Decreasing peaks at i=3 (170), i=7 (150), i=11 (130). Each peak is the strict
# max of highs[i-2..i+2], and ≥5 indices separate each pair.
_FALLING_HIGHS = [
    100,
    110,
    130,
    170,
    120,
    110,  # peak 170 at i=3
    130,
    150,
    120,
    110,
    110,  # peak 150 at i=7
    130,
    100,
    90,  # peak 130 at i=11
]
# Strictly monotonic series — find_swing_highs returns [] (no local maxima).
_STRICTLY_RISING_HIGHS = [100, 105, 110, 115, 120, 125, 130, 135, 140]


class TestDetectMajorityHHHL:
    """Test the multi-swing HH/HL detection helper against find_swing_highs output."""

    def _helper(self, highs, lows, window=2):
        mod = _load_trend_lib()
        swing_highs = find_swing_highs(highs, window)
        swing_lows = find_swing_lows(lows, window)
        return mod._detect_majority_hh_hl(highs, lows, window), swing_highs, swing_lows

    def test_majority_hh_true_on_rising_peak_sequence(self):
        """Peak sequence 130 → 150 → 170 with newest beating all priors → True."""
        highs = _RISING_HIGHS
        lows = [h - 5.0 for h in highs]
        (hh, hl), swing_highs, _ = self._helper(highs, lows, window=2)
        assert swing_highs == [130, 150, 170], f"unexpected swing detection: {swing_highs}"
        assert hh is True
        assert hl is True

    def test_majority_hh_false_on_falling_peak_sequence(self):
        """Peak sequence 170 → 150 → 130 with newest losing to all priors → False."""
        highs = _FALLING_HIGHS
        lows = [h - 5.0 for h in highs]
        (hh, hl), swing_highs, _ = self._helper(highs, lows, window=2)
        assert swing_highs == [170, 150, 130], f"unexpected swing detection: {swing_highs}"
        assert hh is False
        assert hl is False

    def test_insufficient_swing_points(self):
        """Strictly monotonic series yields 0 swings → None on both axes."""
        highs = _STRICTLY_RISING_HIGHS
        lows = [h - 1.0 for h in highs]
        (hh, hl), swing_highs, _ = self._helper(highs, lows, window=2)
        assert swing_highs == [], f"expected no swings in monotonic series, got {swing_highs}"
        assert hh is None
        assert hl is None


def _make_smooth_uptrend_candles(n=250, base=100.0, drift=0.002, vol=1.0, seed=42):
    """Build candles with a deterministic gentle uptrend and modest volatility."""
    import random

    rng = random.Random(seed)
    prices = []
    price = base
    for _ in range(n):
        price = price * (1.0 + drift) + rng.uniform(-vol, vol)
        prices.append(price)
    candles = []
    for i, p in enumerate(prices):
        candles.append(
            [
                i * 86400,
                p,
                p + rng.uniform(0, 0.5),
                p - rng.uniform(0, 0.5),
                p,
                rng.randint(100000, 500000),
            ]
        )
    return candles


class TestMarketTrendScoring:
    """Verify the trend score composes EMA alignment + HH/HL (no slope bonus)."""

    def test_uptrend_score_is_positive(self):
        mod = _load_trend_lib()
        candles = _make_smooth_uptrend_candles(n=250, drift=0.002)
        result = mod.analyze(candles, interval="1d", period="1y")
        assert result["score"] > 0, f"uptrend should produce positive score, got {result['score']}"

    def test_downtrend_score_is_negative(self):
        mod = _load_trend_lib()
        candles = _make_smooth_uptrend_candles(n=250, drift=-0.002)
        result = mod.analyze(candles, interval="1d", period="1y")
        assert result["score"] < 0, f"downtrend should produce negative score, got {result['score']}"
