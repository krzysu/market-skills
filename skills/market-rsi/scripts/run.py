#!/usr/bin/env python3
"""market-rsi — RSI momentum oscillator."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from lib.data import fetch_ohlc
from lib.indicators import compute_rsi
from lib.formatting import emit_json, print_header, parse_args, safe_round


def analyze(ticker):
    candles = fetch_ohlc(ticker)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    if len(candles) < 30:
        return {"ticker": ticker, "error": f"insufficient data (need 30+ days, got {len(candles)})"}

    closes = [float(c[4]) for c in candles]
    current_price = closes[-1]

    rsi = compute_rsi(closes, 14)
    if rsi is None:
        return {"ticker": ticker, "error": "not enough data for RSI"}

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
        "ticker": ticker,
        "price": safe_round(current_price, 2),
        "rsi_14": safe_round(rsi),
        "rsi_7d_ago": safe_round(rsi_prev) if rsi_prev else None,
        "rsi_delta_7d": rsi_delta,
        "signal": signal,
        "score": score,
        "trend": trend,
    }


def main():
    ticker, json_mode, _ = parse_args(sys.argv[1:], default_ticker="SPY")
    result = analyze(ticker)

    if json_mode:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    print_header("RSI MOMENTUM")
    bar_pos = max(0, min(40, round(result["rsi_14"] / 100 * 40)))
    bar = "░" * bar_pos + "█" + "░" * (40 - bar_pos)
    os_marker = " " * 12 + "↑30"
    ob_marker = " " * 28 + "↑70"

    print(f"  {ticker}  (price: {result['price']:,.2f})")
    print(f"    RSI(14):   {result['rsi_14']}")
    if result.get("rsi_delta_7d") is not None:
        print(f"    7d change: {result['rsi_delta_7d']:+.2f} ({result['trend']})")
    print(f"    Position:  [{bar}]")
    print(f"               {os_marker}    {ob_marker}")
    print(f"    Signal:    {result['signal']}")
    print()


if __name__ == "__main__":
    main()
