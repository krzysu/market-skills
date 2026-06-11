#!/usr/bin/env python3
"""market-squeeze — Bollinger Band / Keltner Channel squeeze momentum."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from lib.data import fetch_ohlc
from lib.indicators import compute_squeeze, classify_squeeze, compute_ema, compute_sma, stdev, true_range, linreg
from lib.formatting import emit_json, print_header, parse_args, safe_round


def analyze(ticker):
    candles = fetch_ohlc(ticker)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    bb_length = 20
    kc_length = 20
    needed = max(bb_length, kc_length) + 20

    if len(candles) < needed:
        return {"ticker": ticker, "error": f"insufficient data (need {needed}+ days, got {len(candles)})"}

    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    current_price = closes[-1]

    trs = true_range(candles)

    # Compute squeeze history (last 30 bars)
    history_len = 30
    mom_vals = []
    squeeze_states = []

    for i in range(len(closes) - history_len, len(closes)):
        if i < bb_length:
            mom_vals.append(0.0)
            squeeze_states.append(False)
            continue

        window_closes = closes[: i + 1]
        window_highs = highs[: i + 1]
        window_lows = lows[: i + 1]

        bb_slice = window_closes[-bb_length:]
        bb_mean = sum(bb_slice) / bb_length
        bb_std = (sum((c - bb_mean) ** 2 for c in bb_slice) / bb_length) ** 0.5
        bb_upper = bb_mean + 2.0 * bb_std
        bb_lower = bb_mean - 2.0 * bb_std

        window_trs = trs[:i][-kc_length:]
        if len(window_trs) < kc_length:
            mom_vals.append(0.0)
            squeeze_states.append(False)
            continue

        kc_atr = sum(window_trs) / kc_length
        kc_upper = bb_mean + 1.5 * kc_atr
        kc_lower = bb_mean - 1.5 * kc_atr

        sqz = bb_lower > kc_lower and bb_upper < kc_upper

        mid_hl = (max(window_highs[-bb_length:]) + min(window_lows[-bb_length:])) / 2
        mid_val = (mid_hl + bb_mean) / 2
        mom = window_closes[-1] - mid_val

        mom_vals.append(mom)
        squeeze_states.append(sqz)

    # Current state
    squeeze_on, momentum, direction = compute_squeeze(closes, highs, lows)
    signal = classify_squeeze(momentum, direction)

    # Momentum histogram values (last 5 bars for context)
    histogram = [safe_round(v, 4) if v is not None else None for v in mom_vals[-10:]]

    return {
        "ticker": ticker,
        "price": safe_round(current_price, 2),
        "squeeze_on": squeeze_states[-1] if squeeze_states else False,
        "momentum": safe_round(momentum, 4) if momentum is not None else None,
        "direction": direction,
        "signal": signal,
        "histogram_recent": histogram,
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

    print_header("SQUEEZE MOMENTUM")
    print(f"  {ticker}  (price: {result['price']:,.2f})")
    print(f"    Squeeze:    {'ON — compression' if result['squeeze_on'] else 'OFF — released'}")
    print(f"    Momentum:   {result['momentum']} ({result['direction']})")
    print(f"    Signal:     {result['signal']}")
    print()
    print("    Recent momentum:")
    for i, v in enumerate(result.get("histogram_recent", [])):
        bar = "█" if (v or 0) > 0 else "▁"
        print(f"      [{i:2d}] {bar} {v}")
    print()


if __name__ == "__main__":
    main()
