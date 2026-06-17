#!/usr/bin/env python3
"""market-macd — MACD momentum indicator."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_macd_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, parse_args, print_header, require_ticker


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
        "skill": "market-macd",
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
    ticker, json_mode, source = parse_args(sys.argv[1:])
    require_ticker(ticker, json_mode)
    result = analyze(ticker, source=source)

    if json_mode:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result["indicators"]
    print_header("MACD MOMENTUM")
    print(f"  {ticker}")
    print()
    print(f"    MACD Line:      {ind.get('macd_line', 'N/A'):>10.4f}")
    print(f"    Signal Line:    {ind.get('signal_line', 'N/A'):>10.4f}")
    print(f"    Histogram:      {ind.get('histogram', 'N/A'):>10.4f}  ({ind.get('histogram_direction', 'N/A')})")
    if ind.get("histogram_flip"):
        print(f"    Histogram Flip: {ind['histogram_flip']}  \u26a0")
    print(f"    Signal:         {result.get('signal', 'N/A')}  (score: {result.get('score', 'N/A')})")
    print(f"    Zone:           {result.get('zone', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
