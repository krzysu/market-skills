"""market-snapshot — single-call chart sanity check: supertrend + RSI + MA alignment.

When a 1d L3 idea fires but the 4h chart structure disagrees, the agent calls this
skill on the 4h timeframe to get a quick structural read without re-running all
L2/L3 layers.

Outputs a compact dict:
    {
        "ticker": str,
        "interval": str,
        "current_price": float,
        "supertrend": {"value": float, "direction": "up"|"down", "period": 10, "multiplier": 3.0},
        "rsi":        {"value": float, "signal": "OVERSOLD"|"OVERBOUGHT"|"NEUTRAL"|...},
        "ma_alignment": "FULL_BULL"|"PARTIAL_BULL"|"TANGLED"|"PARTIAL_BEAR"|"FULL_BEAR",
        "agrees_with_idea": bool | None,  # True iff all three point the same direction
    }
"""

from analysis.formatting import safe_round
from analysis.indicators import extract_ohlcv
from analysis.skill_loader import load_skill


def _compute_supertrend(highs, lows, closes, period=10, multiplier=3.0):
    """Standard Supertrend (period=10, multiplier=3.0).

    Returns (value, direction) at the most recent bar.
    direction = "up" means price is above supertrend (bullish),
    "down" means price is below (bearish).

    Implements the canonical ratcheting final-band logic (TradingView / stockcharts.com):
      - final_upper[i] can only DECREASE bar-over-bar (resistance that tightens in uptrends)
      - final_lower[i] can only INCREASE bar-over-bar (support that tightens in downtrends)
      - Once in a regime, flip only when price crosses the OPPOSITE band
    Without these rules, supertrend flips on every bar in any low-volatility trend.
    """
    if len(closes) < period + 1:
        return None, None

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None, None

    atr_series = []
    for i in range(period - 1, len(trs)):
        atr_series.append(sum(trs[i - period + 1 : i + 1]) / period)

    # hl2 / final bands are aligned to closes[period:] (offset = period).
    hl2 = [(highs[i + period] + lows[i + period]) / 2 for i in range(len(atr_series))]
    raw_upper = [h + multiplier * a for h, a in zip(hl2, atr_series)]
    raw_lower = [lo - multiplier * a for lo, a in zip(hl2, atr_series)]

    final_upper = [raw_upper[0]]
    final_lower = [raw_lower[0]]
    for i in range(1, len(raw_upper)):
        # Canonical ratchet uses the PREVIOUS bar's close (close[1] in TradingView).
        prev_close = closes[period + i - 1]
        # final_upper can decrease if raw_upper < prev, OR if prev close was already above prev upper.
        if raw_upper[i] < final_upper[i - 1] or prev_close > final_upper[i - 1]:
            final_upper.append(raw_upper[i])
        else:
            final_upper.append(final_upper[i - 1])
        # final_lower can increase if raw_lower > prev, OR if prev close was already below prev lower.
        if raw_lower[i] > final_lower[i - 1] or prev_close < final_lower[i - 1]:
            final_lower.append(raw_lower[i])
        else:
            final_lower.append(final_lower[i - 1])

    # Canonical TradingView direction: current close vs PREVIOUS supertrend line.
    # Flip when close crosses the previous bar's supertrend — uses supertrend_bands[i-1],
    # not the current bar's final band (that produced off-by-one flip points).
    supertrend_bands = [final_lower[0]]
    direction = [1]
    for i in range(1, len(final_upper)):
        prev_supertrend = supertrend_bands[i - 1]
        if closes[period + i] > prev_supertrend:
            supertrend_bands.append(final_lower[i])
            direction.append(1)
        else:
            supertrend_bands.append(final_upper[i])
            direction.append(-1)

    last_val = supertrend_bands[-1]
    last_dir = direction[-1]
    return safe_round(last_val, 4), "up" if last_dir == 1 else "down"


def _consensus_bullish(supertrend_dir, rsi_signal, alignment):
    """All three signals agree on direction. Used to derive `agrees_with_idea`."""
    st_bull = supertrend_dir == "up"
    rsi_bear = rsi_signal == "OVERBOUGHT"
    if alignment in ("FULL_BEAR", "PARTIAL_BEAR"):
        align_bull = False
    elif alignment in ("FULL_BULL", "PARTIAL_BULL"):
        align_bull = True
    else:
        align_bull = None  # tangled — inconclusive

    if align_bull is True and st_bull and not rsi_bear:
        return True
    if align_bull is False and not st_bull and not (rsi_signal == "OVERSOLD"):
        return False
    return None


def analyze(candles, *, ticker, interval="4h", period="6mo"):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"error": f"insufficient data (need 50+ candles, got {cc})", "ticker": ticker, "interval": interval}

    _, highs, lows, closes, _ = extract_ohlcv(candles)
    current_price = closes[-1]

    st_value, st_dir = _compute_supertrend(highs, lows, closes, period=10, multiplier=3.0)

    rsi_mod = load_skill("market-rsi")
    trend_mod = load_skill("market-trend")

    err = {"error": "unavailable"}
    rsi_result = rsi_mod.analyze(candles, interval=interval, period=period) if rsi_mod else err
    trend_result = trend_mod.analyze(candles, interval=interval, period=period) if trend_mod else err

    rsi_value = rsi_result.get("rsi_14") if "error" not in rsi_result else None
    rsi_signal = rsi_result.get("signal") if "error" not in rsi_result else None
    alignment = trend_result.get("alignment") if "error" not in trend_result else None

    consensus = _consensus_bullish(st_dir, rsi_signal, alignment)

    return {
        "ticker": ticker,
        "interval": interval,
        "current_price": safe_round(current_price, 2),
        "supertrend": {
            "value": st_value,
            "direction": st_dir,
            "period": 10,
            "multiplier": 3.0,
        },
        "rsi": {"value": rsi_value, "signal": rsi_signal},
        "ma_alignment": alignment,
        "agrees_with_idea": consensus,
    }
