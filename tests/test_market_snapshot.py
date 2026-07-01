"""Tests for market-snapshot L1-cross-skill: supertrend + RSI + MA alignment."""

import importlib.util
import os


def _load_snapshot_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-snapshot", "lib.py")
    spec = importlib.util.spec_from_file_location("market_snapshot_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=250, base=100.0, drift=0.0, seed=42):
    """Build [ts, open, high, low, close, volume] candles."""
    import random

    rng = random.Random(seed)
    price = base
    out = []
    for i in range(n):
        open_p = price
        close_p = price * (1.0 + drift) + rng.uniform(-1.0, 1.0)
        high_p = max(open_p, close_p) + rng.uniform(0.1, 0.5)
        low_p = min(open_p, close_p) - rng.uniform(0.1, 0.5)
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 200_000])
    return out


class TestComputeSupertrend:
    def test_bullish_run_yields_up_direction(self):
        mod = _load_snapshot_lib()
        # Hand-crafted bullish series: monotonic price rise. No noise → deterministic.
        candles = []
        for i in range(120):
            price = 100.0 + i * 0.5  # monotonic rise
            candles.append([i * 86400, price - 0.5, price + 1.0, price - 1.0, price, 200_000])
        from analysis.indicators import extract_ohlcv

        _, highs, lows, closes, _ = extract_ohlcv(candles)
        st_val, st_dir = mod._compute_supertrend(highs, lows, closes, period=10, multiplier=3.0)
        assert st_dir == "up", f"expected up in monotonic bull run, got {st_dir}"
        assert st_val is not None
        assert st_val < closes[-1], "supertrend below price in uptrend"

    def test_bearish_run_yields_down_direction(self):
        mod = _load_snapshot_lib()
        # Hand-crafted bearish series: monotonic price fall. No noise → deterministic.
        candles = []
        for i in range(120):
            price = 200.0 - i * 0.5  # monotonic fall
            candles.append([i * 86400, price + 0.5, price + 1.0, price - 1.0, price, 200_000])
        from analysis.indicators import extract_ohlcv

        _, highs, lows, closes, _ = extract_ohlcv(candles)
        st_val, st_dir = mod._compute_supertrend(highs, lows, closes, period=10, multiplier=3.0)
        assert st_dir == "down", f"expected down in monotonic bear run, got {st_dir}"
        assert st_val is not None
        assert st_val > closes[-1], "supertrend above price in downtrend"

    def test_hl2_offset_uses_period_not_one(self):
        """Regression test for hl2 offset bug.

        Monotonic series hides the case because hl2 is the same regardless of offset
        once bars are in a steady trend. Series with a regime change at bar `period`
        exposes it: with offset=1 the first band uses the pre-regime hl2 (50), with
        offset=period it uses the post-regime hl2 (~100).
        """
        mod = _load_snapshot_lib()
        from analysis.indicators import extract_ohlcv

        candles = []
        for i in range(120):
            if i < 10:
                price = 50.0  # flat regime for first `period` bars
            else:
                price = 100.0 + (i - 10) * 0.5  # new regime starts at bar `period`
            candles.append([i * 86400, price - 0.5, price + 1.0, price - 1.0, price, 200_000])

        _, highs, lows, closes, _ = extract_ohlcv(candles)
        st_val, st_dir = mod._compute_supertrend(highs, lows, closes, period=10, multiplier=3.0)
        assert st_val is not None
        # Sanity: post-regime, supertrend should track the rising hl2 (≈ price - 6).
        # If offset=1 (buggy), the supertrend would be dragged down by pre-regime bars.
        assert st_val > 100.0, f"supertrend {st_val} too low — hl2 offset likely wrong"
        assert st_dir == "up", "rising regime should yield up direction"

    def test_canonical_direction_flip_on_regime_change(self):
        """Regression test for canonical TradingView direction-flip logic.

        The previous impl compared current close against `final_upper[i]`/`final_lower[i]`
        (current bar's band). Canonical TradingView compares against `supertrend[1]`
        (previous bar's supertrend line). On a multi-regime series the difference shows
        up as extra spurious flips. We assert exactly 2 flips occur (one bear→bull entry,
        one bull→bear exit) and the final direction matches canonical.
        """
        mod = _load_snapshot_lib()
        from analysis.indicators import extract_ohlcv

        candles = []
        for i in range(120):
            if i < 60:
                price = 150.0 - i * 1.0  # strong downtrend
            else:
                price = 90.0 + (i - 60) * 1.0  # strong uptrend
            candles.append([i * 86400, price - 0.5, price + 1.0, price - 1.0, price, 200_000])

        _, highs, lows, closes, _ = extract_ohlcv(candles)

        period, multiplier = 10, 3.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr_series = [sum(trs[i - period + 1 : i + 1]) / period for i in range(period - 1, len(trs))]
        hl2 = [(highs[i + period] + lows[i + period]) / 2 for i in range(len(atr_series))]
        raw_upper = [h + multiplier * a for h, a in zip(hl2, atr_series)]
        raw_lower = [h - multiplier * a for h, a in zip(hl2, atr_series)]

        final_upper = [raw_upper[0]]
        final_lower = [raw_lower[0]]
        for i in range(1, len(raw_upper)):
            prev_close = closes[period + i - 1]
            if raw_upper[i] < final_upper[i - 1] or prev_close > final_upper[i - 1]:
                final_upper.append(raw_upper[i])
            else:
                final_upper.append(final_upper[i - 1])
            if raw_lower[i] > final_lower[i - 1] or prev_close < final_lower[i - 1]:
                final_lower.append(raw_lower[i])
            else:
                final_lower.append(final_lower[i - 1])

        # Canonical direction (TradingView): close vs PREVIOUS supertrend line.
        ref_bands = [final_lower[0]]
        ref_dir = [1]
        for i in range(1, len(final_upper)):
            prev_supertrend = ref_bands[i - 1]
            if closes[period + i] > prev_supertrend:
                ref_bands.append(final_lower[i])
                ref_dir.append(1)
            else:
                ref_bands.append(final_upper[i])
                ref_dir.append(-1)

        ref_value = round(ref_bands[-1], 4)
        ref_final_dir = "up" if ref_dir[-1] == 1 else "down"
        ref_flips = sum(1 for i in range(1, len(ref_dir)) if ref_dir[i] != ref_dir[i - 1])

        actual_val, actual_dir = mod._compute_supertrend(highs, lows, closes, period=10, multiplier=3.0)

        assert actual_dir == ref_final_dir, f"final dir mismatch: got {actual_dir}, canonical {ref_final_dir}"
        assert actual_val == ref_value, f"value mismatch: got {actual_val}, canonical {ref_value}"
        assert ref_flips == 2, f"sanity: canonical should produce exactly 2 flips, got {ref_flips}"


class TestAnalyze:
    def test_returns_envelope(self, monkeypatch):
        """analyze returns ticker/interval/price + supertrend/rsi/ma_alignment."""
        import analysis.skill_loader as sl

        # Patch BEFORE loading the lib so the from-import binds to our mock.
        rsi_response = {"rsi_14": 50, "signal": "NEUTRAL"}
        trend_response = {"alignment": "FULL_BULL"}
        canned = {
            "market-rsi": type("R", (), {"analyze": staticmethod(lambda c, **_kw: rsi_response)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: trend_response)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_snapshot_lib()
        candles = _make_candles(n=120, drift=0.005, seed=3)
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")

        assert result["ticker"] == "TEST"
        assert result["interval"] == "4h"
        assert "current_price" in result
        assert "supertrend" in result
        assert "rsi" in result
        assert "ma_alignment" in result
        assert "agrees_with_idea" in result

    def test_insufficient_data(self):
        mod = _load_snapshot_lib()
        result = mod.analyze([], ticker="TEST", interval="4h", period="6mo")
        assert "error" in result

    def test_agrees_with_idea_true_for_bullish_consensus(self, monkeypatch):
        import analysis.skill_loader as sl

        # Monotonic bull run → supertrend="up" deterministically. RSI NEUTRAL + FULL_BULL
        # → consensus True.
        rsi_response = {"rsi_14": 55, "signal": "NEUTRAL"}
        trend_response = {"alignment": "FULL_BULL"}
        canned = {
            "market-rsi": type("R", (), {"analyze": staticmethod(lambda c, **_kw: rsi_response)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: trend_response)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_snapshot_lib()
        candles = []
        for i in range(120):
            price = 100.0 + i * 0.5
            candles.append([i * 86400, price - 0.5, price + 1.0, price - 1.0, price, 200_000])
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert result["supertrend"]["direction"] == "up"
        assert result["agrees_with_idea"] is True

    def test_agrees_with_idea_false_for_bearish_consensus(self, monkeypatch):
        import analysis.skill_loader as sl

        # Monotonic bear run → supertrend="down" deterministically. RSI NEUTRAL + FULL_BEAR
        # → consensus False.
        rsi_response = {"rsi_14": 35, "signal": "NEUTRAL"}
        trend_response = {"alignment": "FULL_BEAR"}
        canned = {
            "market-rsi": type("R", (), {"analyze": staticmethod(lambda c, **_kw: rsi_response)})(),
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: trend_response)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_snapshot_lib()
        candles = []
        for i in range(120):
            price = 200.0 - i * 0.5
            candles.append([i * 86400, price + 0.5, price + 1.0, price - 1.0, price, 200_000])
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert result["supertrend"]["direction"] == "down"
        assert result["agrees_with_idea"] is False
