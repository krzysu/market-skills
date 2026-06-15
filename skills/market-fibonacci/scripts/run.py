#!/usr/bin/env python3
"""market-fibonacci — Fibonacci retracement and extension levels."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_fibonacci_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from datetime import UTC, datetime

from lib.data import fetch_ohlc
from lib.formatting import emit_json, parse_args, print_header


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
        "skill": "market-fibonacci",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": "1d",
        "period": "1y",
        "candles_used": len(candles),
        "indicators": result,
        "score": None,
        "signal": None,
        "zone": None,
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
    print_header("FIBONACCI LEVELS")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print()
    print(f"    Swing High:  {ind.get('swing_high', 'N/A'):>10,.2f}")
    print(f"    Swing Low:   {ind.get('swing_low', 'N/A'):>10,.2f}")
    print(f"    Position:    {ind.get('current_position', 'N/A')}")
    print()
    print("    Fibonacci Levels:")
    for key, val in ind.get("fib_levels", {}).items():
        marker = ""
        if ind.get("nearest_fib_support") and val == ind["nearest_fib_support"]:
            marker = "  \u2190 support"
        elif ind.get("nearest_fib_resistance") and val == ind["nearest_fib_resistance"]:
            marker = "  \u2190 resistance"
        print(f"      {key:>5}:  {val:>10,.2f}{marker}")
    print()
    if ind.get("nearest_fib_distance_pct") is not None:
        print(f"    Distance to nearest fib level: {ind['nearest_fib_distance_pct']:.2f}%")
    print()


if __name__ == "__main__":
    main()
