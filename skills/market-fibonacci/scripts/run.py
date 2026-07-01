#!/usr/bin/env python3
"""market-fibonacci — Fibonacci retracement and extension levels."""

import sys
from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, print_header, require_ticker, safe_parse_args
from analysis.skill_loader import load_lib_for_script


def analyze(ticker, *, source=None, interval="1d", period="1y"):
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = load_lib_for_script(__file__)
    result = _lib.analyze(candles, interval=interval, period=period)
    if "error" in result:
        return {"ticker": ticker, **result}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    return {
        "skill": "market-fibonacci",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "indicators": result,
        "score": None,
        "signal": None,
        "zone": None,
    }


def main():
    ticker, json_mode, source, interval, period = safe_parse_args(sys.argv[1:])
    require_ticker(ticker, json_mode)
    result = analyze(ticker, source=source, interval=interval, period=period)

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
