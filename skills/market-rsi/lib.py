"""market-rsi — L1 indicator: RSI momentum oscillator."""

from lib.formatting import safe_round
from lib.indicators import compute_rsi, extract_ohlcv


def analyze(candles, interval="1d", period="1y"):
    if not candles or len(candles) < 30:
        return {"error": f"insufficient data (need 30+ candles, got {len(candles) if candles else 0})"}

    _, _, _, closes, _ = extract_ohlcv(candles)
    current_price = closes[-1]

    rsi = compute_rsi(closes, 14)
    if rsi is None:
        return {"error": "not enough data for RSI"}

    rsi_prev = compute_rsi(closes[:-7], 14) if len(closes) > 21 else None
    rsi_delta = round(rsi - rsi_prev, 2) if rsi_prev is not None else None

    if rsi < 30:
        signal = "OVERSOLD"
        score = 2
    elif rsi < 40:
        signal = "APPROACHING OVERSOLD"
        score = 1
    elif rsi <= 60:
        signal = "NEUTRAL"
        score = 0
    elif rsi <= 70:
        signal = "APPROACHING OVERBOUGHT"
        score = -1
    else:
        signal = "OVERBOUGHT"
        score = -2

    if rsi_delta is not None:
        if rsi_delta < -10:
            trend = "falling fast"
        elif rsi_delta < -3:
            trend = "falling"
        elif rsi_delta > 10:
            trend = "rising fast"
        elif rsi_delta > 3:
            trend = "rising"
        else:
            trend = "stable"
    else:
        trend = None

    return {
        "current_price": safe_round(current_price, 2),
        "rsi_14": safe_round(rsi),
        "rsi_7d_ago": safe_round(rsi_prev) if rsi_prev else None,
        "rsi_delta_7d": rsi_delta,
        "signal": signal,
        "score": score,
        "trend": trend,
    }
