#!/usr/bin/env python3
"""market-rsi — RSI momentum oscillator."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_rsi_lib", lib_path)
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
        "skill": "market-rsi",
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
    print_header("RSI MOMENTUM")
    bar_pos = max(0, min(40, round(ind.get("rsi_14", 50) / 100 * 40)))
    bar = "\u2591" * bar_pos + "\u2588" + "\u2591" * (40 - bar_pos)
    os_marker = " " * 12 + "\u219130"
    ob_marker = " " * 28 + "\u219170"

    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print(f"    RSI(14):   {ind.get('rsi_14', 'N/A')}")
    if ind.get("rsi_delta_7d") is not None:
        print(f"    7d change: {ind['rsi_delta_7d']:+.2f} ({ind.get('trend', 'N/A')})")
    print(f"    Position:  [{bar}]")
    print(f"               {os_marker}    {ob_marker}")
    print(f"    Signal:    {ind.get('signal', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
