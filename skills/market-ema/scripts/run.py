#!/usr/bin/env python3
"""market-ema — EMA filter and trend structure analysis."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from lib.data import fetch_ohlc
from lib.indicators import compute_ema, detect_crossover, ema_slope_pct
from lib.formatting import emit_json, print_header, parse_args, safe_round


def analyze(ticker):
    candles = fetch_ohlc(ticker)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    if len(candles) < 220:
        return {"ticker": ticker, "error": f"insufficient data (need 220+ days for EMA 200, got {len(candles)})"}

    closes = [float(c[4]) for c in candles]
    current_price = closes[-1]

    ema_21, ema_21_series = compute_ema(closes, 21)
    ema_50, ema_50_series = compute_ema(closes, 50)
    ema_100, ema_100_series = compute_ema(closes, 100)
    ema_200, ema_200_series = compute_ema(closes, 200)

    emas = [ema_21, ema_50, ema_100, ema_200]
    if any(e is None for e in emas):
        alignment = "UNKNOWN"
    elif ema_21 > ema_50 > ema_100 > ema_200:
        alignment = "FULL_BULL"
    elif ema_200 > ema_100 > ema_50 > ema_21:
        alignment = "FULL_BEAR"
    elif ema_21 > ema_50:
        alignment = "PARTIAL_BULL"
    elif ema_50 > ema_21:
        alignment = "PARTIAL_BEAR"
    else:
        alignment = "TANGLED"

    above_count = sum(1 for e in emas if e is not None and current_price > e)

    slope_21 = ema_slope_pct(ema_21_series)
    slope_50 = ema_slope_pct(ema_50_series)

    crossover = detect_crossover(ema_21_series, ema_50_series, lookback=5)

    if alignment == "FULL_BULL" and above_count == 4:
        signal = "STRONG UPTREND"
        score = 2
    elif alignment in ("FULL_BULL", "PARTIAL_BULL") and above_count >= 3:
        signal = "UPTREND"
        score = 1
    elif alignment in ("FULL_BEAR", "PARTIAL_BEAR") and above_count <= 1:
        signal = "DOWNTREND"
        score = -1
    elif alignment == "FULL_BEAR" and above_count == 0:
        signal = "STRONG DOWNTREND"
        score = -2
    else:
        signal = "TRANSITION"
        score = 0

    if crossover == "golden_cross" and score < 1:
        score = 1
    elif crossover == "death_cross" and score > -1:
        score = -1

    return {
        "ticker": ticker,
        "price": safe_round(current_price, 2),
        "ema_21": safe_round(ema_21, 2),
        "ema_50": safe_round(ema_50, 2),
        "ema_100": safe_round(ema_100, 2),
        "ema_200": safe_round(ema_200, 2),
        "alignment": alignment,
        "price_above_emas": above_count,
        "slope_21_pct": safe_round(slope_21, 3),
        "slope_50_pct": safe_round(slope_50, 3),
        "crossover": crossover,
        "signal": signal,
        "score": score,
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

    print_header("EMA TREND STRUCTURE")
    print(f"  {ticker}  (price: {result['price']:,.2f})")
    print()
    labels = [("EMA 21", result["ema_21"]), ("EMA 50", result["ema_50"]),
              ("EMA 100", result["ema_100"]), ("EMA 200", result["ema_200"])]
    for label, val in labels:
        if val:
            pos = "▲" if result["price"] > val else "▼"
            pct = (result["price"] - val) / val * 100
            print(f"    {label}:  {val:,.2f}  ({pct:+.1f}%) {pos}")
    print()
    print(f"    Alignment:  {result['alignment']} (price above {result['price_above_emas']}/4 EMAs)")
    if result["slope_21_pct"] is not None:
        print(f"    Slope 21:   {result['slope_21_pct']:+.3f}%/5d")
    if result["slope_50_pct"] is not None:
        print(f"    Slope 50:   {result['slope_50_pct']:+.3f}%/5d")
    if result["crossover"]:
        note = "bullish reversal" if result["crossover"] == "golden_cross" else "bearish reversal"
        print(f"    Crossover:  {result['crossover']} — {note}")
    print(f"    Signal:     {result['signal']} (score: {result['score']})")
    print()


if __name__ == "__main__":
    main()
