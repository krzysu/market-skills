#!/usr/bin/env python3
"""market-trend — Trend structure analysis: EMA alignment, HH/HL, slope."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_trend_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, parse_args, print_header


def analyze(ticker, source=None):
    candles = fetch_ohlc(ticker, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = _load_lib()
    result = _lib.analyze(candles)
    if "error" in result:
        return {"ticker": ticker, **result}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    return {
        "skill": "market-trend",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": "1d",
        "period": "1y",
        "candles_used": len(candles),
        "indicators": result,
        "score": result.get("score"),
        "signal": result.get("signal"),
        "zone": result.get("zone"),
    }


def main():
    ticker, json_mode, source = parse_args(sys.argv[1:], default_ticker="SPY")
    result = analyze(ticker, source=source)

    if json_mode:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result["indicators"]
    print_header("TREND STRUCTURE")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print()
    print(f"    Alignment:     {ind.get('alignment', 'N/A')}  (price above {ind.get('price_above_emas', 0)}/4 EMAs)")
    emas = [
        ("21", ind.get("ema_21")),
        ("50", ind.get("ema_50")),
        ("100", ind.get("ema_100")),
        ("200", ind.get("ema_200")),
    ]
    for label, val in emas:
        if val is not None:
            pos = "\u25b2" if ind.get("current_price", 0) > val else "\u25bc"
            pct = (ind.get("current_price", 0) - val) / val * 100
            print(f"      EMA {label}:  {val:>10,.2f}  ({pct:+.1f}%) {pos}")
    print()
    print(f"    HH Structure:  {ind.get('higher_high', 'N/A')}")
    print(f"    HL Structure:  {ind.get('higher_low', 'N/A')}")
    if ind.get("slope_21_pct") is not None:
        print(f"    Slope 21:      {ind['slope_21_pct']:+.3f}%/5d")
    if ind.get("slope_50_pct") is not None:
        print(f"    Slope 50:      {ind['slope_50_pct']:+.3f}%/5d")
    if ind.get("crossover"):
        note = "bullish reversal" if ind["crossover"] == "golden_cross" else "bearish reversal"
        print(f"    Crossover:     {ind['crossover']} \u2014 {note}")
    print(f"    Signal:        {result.get('signal', 'N/A')}  (score: {result.get('score', 'N/A')})")
    print(f"    Zone:          {result.get('zone', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
