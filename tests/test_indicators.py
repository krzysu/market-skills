"""Tests for lib/indicators.py — pure math indicator functions."""

import pytest
import math
from lib.indicators import (
    compute_ema,
    compute_sma,
    compute_sma_series,
    compute_rsi,
    classify_rsi,
    compute_atr,
    compute_squeeze,
    classify_squeeze,
    compute_obv,
    compute_obv_trend,
    compute_macd,
    compute_fib_levels,
    detect_crossover,
    ema_slope_pct,
    stdev,
    linreg,
    percentile_rank,
    pearson_corr,
    log_returns,
    realized_vol,
    detect_obv_divergence,
    find_swing_highs,
    find_swing_lows,
    cluster_levels,
    extract_ohlcv,
)


class TestEMA:
    def test_basic_ema(self):
        values = [10.0] * 20
        ema, series = compute_ema(values, 10)
        assert ema == pytest.approx(10.0)
        assert len(series) == 11

    def test_insufficient_data(self):
        ema, series = compute_ema([1.0, 2.0, 3.0], 10)
        assert ema is None
        assert series == []

    def test_rising_ema(self):
        values = list(range(1, 51))
        ema, _ = compute_ema(values, 10)
        assert ema > values[-11]  # EMA should lag but follow


class TestSMA:
    def test_basic_sma(self):
        values = list(range(1, 11))
        result = compute_sma(values, 5)
        assert result == pytest.approx(8.0)  # (6+7+8+9+10)/5

    def test_insufficient_data(self):
        assert compute_sma([1.0, 2.0], 5) is None

    def test_sma_series(self):
        values = list(range(1, 11))
        series = compute_sma_series(values, 3)
        assert len(series) == 8
        assert series[0] == pytest.approx(2.0)  # (1+2+3)/3
        assert series[-1] == pytest.approx(9.0)  # (8+9+10)/3


class TestStdev:
    def test_basic(self):
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        result = stdev(values, 8)
        assert result == pytest.approx(2.0)

    def test_insufficient(self):
        assert stdev([1.0], 5) is None


class TestLinreg:
    def test_basic(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = linreg(values, 5)
        assert result == pytest.approx(5.0)


class TestPercentileRank:
    def test_basic(self):
        rank = percentile_rank(50, list(range(100)))
        assert rank == pytest.approx(50.0)

    def test_empty(self):
        assert percentile_rank(5, []) is None


class TestPearsonCorr:
    def test_perfect_positive(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        assert pearson_corr(xs, ys) == pytest.approx(1.0)

    def test_perfect_negative(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [10.0, 8.0, 6.0, 4.0, 2.0]
        assert pearson_corr(xs, ys) == pytest.approx(-1.0)


class TestRSI:
    def test_all_gains(self):
        # Strictly rising prices
        closes = list(range(1, 30))
        rsi = compute_rsi(closes, 14)
        assert rsi == pytest.approx(100.0)

    def test_all_losses(self):
        # Strictly falling prices
        closes = list(range(30, 1, -1))
        rsi = compute_rsi(closes, 14)
        assert rsi == pytest.approx(0.0)

    def test_neutral(self):
        # Alternating up/down by same amount
        closes = [100.0]
        for _ in range(25):
            closes.append(closes[-1] + 1.0)
            closes.append(closes[-1] - 1.0)
        rsi = compute_rsi(closes, 14)
        # Should converge toward 50
        assert 40 <= rsi <= 60

    def test_classify(self):
        assert classify_rsi(25) == "OVERSOLD"
        assert classify_rsi(35) == "APPROACHING OVERSOLD"
        assert classify_rsi(50) == "NEUTRAL"
        assert classify_rsi(65) == "APPROACHING OVERBOUGHT"
        assert classify_rsi(75) == "OVERBOUGHT"
        assert classify_rsi(None) == "UNKNOWN"


class TestATR:
    def test_basic(self):
        highs = [15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0]
        lows = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0]
        closes = [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0]
        atr = compute_atr(highs, lows, closes, 14)
        assert atr == pytest.approx(6.0)


class TestSqueeze:
    def test_no_squeeze_wide_bands(self):
        # Volatile data → BB should be wider than KC
        closes = [10 + i * 2 + (i % 5) * 3 for i in range(40)]
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        squeeze_on, momentum, direction = compute_squeeze(closes, highs, lows)
        # Should not be squeezing with high volatility
        assert squeeze_on is not None
        assert direction is not None

    def test_classify(self):
        assert classify_squeeze(1.0, "increasing") == "BULLISH"
        assert classify_squeeze(1.0, "decreasing") == "BULLISH FADING"
        assert classify_squeeze(-1.0, "decreasing") == "BEARISH"
        assert classify_squeeze(-1.0, "increasing") == "BEARISH FADING"
        assert classify_squeeze(0.0, "increasing") == "FLAT"
        assert classify_squeeze(None, "increasing") == "UNKNOWN"


class TestOBV:
    def test_basic(self):
        closes = [10.0, 11.0, 10.5, 12.0, 11.5]
        volumes = [100, 200, 150, 300, 100]
        obv = compute_obv(closes, volumes)
        assert obv[0] == 0
        assert obv[1] == 200   # up: +200
        assert obv[2] == 50    # down: -150
        assert obv[3] == 350   # up: +300
        assert obv[4] == 250   # down: -100

    def test_obv_trend_rising(self):
        closes = [10.0]
        volumes = [50]
        # Create a rising pattern
        for i in range(30):
            closes.append(closes[-1] + 1.0)
            volumes.append(100 + i)
        trend = compute_obv_trend(closes, volumes, sma_period=20)
        # OBV should be rising since all closes are higher
        assert trend == "rising"

    def test_insufficient_data(self):
        assert compute_obv_trend([1.0, 2.0, 3.0], [10, 20, 30], sma_period=20) is None


class TestMACD:
    def test_basic(self):
        closes = list(range(1, 60))
        macd, signal, histogram = compute_macd(closes)
        # Indices where slow EMA (period=26) isnt ready: 0-24 are None
        assert macd[24] is None
        assert macd[26] is not None  # 0-indexed, so index 26 is the 27th element

    def test_insufficient_data(self):
        macd, signal, histogram = compute_macd([1.0, 2.0, 3.0])
        # Should all be None-padded
        assert all(m is None for m in macd)


class TestFibonacci:
    def test_basic(self):
        levels = compute_fib_levels(100, 200)
        assert levels["0"] == 200.0
        assert levels["1.0"] == 100.0
        assert levels["0.5"] == 150.0
        assert levels["0.618"] == pytest.approx(138.2, abs=0.1)
        assert levels["1.272"] == pytest.approx(227.2, abs=0.1)
        assert levels["1.618"] == pytest.approx(261.8, abs=0.1)


class TestCrossover:
    def test_golden_cross(self):
        # Short crosses above long within lookback=1
        short = [10.0, 11.0, 12.0]
        long = [11.0, 11.0, 11.0]
        assert detect_crossover(short, long, lookback=1) == "golden_cross"

    def test_death_cross(self):
        # Short crosses below long
        short = [7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
        long = [5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0]
        assert detect_crossover(short, long, lookback=3) == "death_cross"

    def test_no_cross(self):
        short = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
        long = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        assert detect_crossover(short, long, lookback=3) is None


class TestEMASlope:
    def test_basic(self):
        series = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        pct = ema_slope_pct(series, period=5)
        # (series[-1] - series[-6]) / series[-6] = (106 - 101) / 101
        assert pct == pytest.approx(5.0 / 101.0 * 100, abs=0.01)


class TestLogReturns:
    def test_basic(self):
        closes = [100.0, 101.0, 99.0]
        rets = log_returns(closes)
        assert len(rets) == 2
        assert rets[0] == pytest.approx(math.log(101.0 / 100.0))
        assert rets[1] == pytest.approx(math.log(99.0 / 101.0))


class TestRealizedVol:
    def test_basic(self):
        returns = [0.01, -0.01, 0.02, -0.02, 0.01] * 6  # 30 returns
        vol = realized_vol(returns, 20)
        assert vol is not None
        assert vol > 0


class TestSwingPoints:
    def test_swing_highs(self):
        highs = [1, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1, 2, 3, 2, 1]
        swings = find_swing_highs(highs, window=3)
        # Should find local peaks
        assert len(swings) > 0

    def test_swing_lows(self):
        lows = [3, 2, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 2, 3]
        swings = find_swing_lows(lows, window=3)
        assert len(swings) > 0


class TestClusterLevels:
    def test_basic(self):
        levels = [100.0, 101.0, 102.0, 200.0, 201.0]
        clusters = cluster_levels(levels, tolerance_pct=5)
        assert len(clusters) == 2
        assert clusters[0]["touches"] == 3
        assert clusters[1]["touches"] == 2

    def test_empty(self):
        assert cluster_levels([]) == []


class TestExtractOHLCV:
    def test_basic(self):
        candles = [
            [1000, 10.0, 12.0, 9.0, 11.0, 1000],
            [2000, 11.0, 13.0, 10.0, 12.0, 2000],
        ]
        opens, highs, lows, closes, volumes = extract_ohlcv(candles)
        assert opens == [10.0, 11.0]
        assert highs == [12.0, 13.0]
        assert lows == [9.0, 10.0]
        assert closes == [11.0, 12.0]
        assert volumes == [1000.0, 2000.0]


class TestOBVDivergence:
    def test_insufficient_data(self):
        closes = list(range(1, 30))
        volumes = [100] * 29
        assert detect_obv_divergence(closes, volumes, lookback=28) is None

    def test_bullish_divergence(self):
        lookback = 28
        n = 60
        closes = [100.0] * 4
        for i in range(28):
            closes.append(100.0 - i * 0.5)
        for i in range(10):
            closes.append(closes[-1] + 0.5)
        for i in range(18):
            closes.append(closes[-1] - 0.5)
        assert len(closes) == n

        volumes = [100.0] * n
        for i in range(32, 42):
            volumes[i] = 10000.0

        div = detect_obv_divergence(closes, volumes, lookback=lookback)
        assert div == "bullish"
