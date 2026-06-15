#!/usr/bin/env python3
"""market-squeeze — Bollinger Band / Keltner Channel squeeze momentum."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_squeeze_lib", lib_path)
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
        "skill": "market-squeeze",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": "1d",
        "period": "1y",
        "candles_used": len(candles),
        "indicators": result,
        "score": None,
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
    print_header("SQUEEZE MOMENTUM")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print(f"    Squeeze:    {'ON \u2014 compression' if ind.get('squeeze_on') else 'OFF \u2014 released'}")
    print(f"    Momentum:   {ind.get('momentum', 'N/A')} ({ind.get('direction', 'N/A')})")
    print(f"    Signal:     {ind.get('signal', 'N/A')}")
    print()
    print("    Recent momentum:")
    for i, v in enumerate(ind.get("histogram_recent", [])):
        bar = "\u2588" if (v or 0) > 0 else "\u2581"
        print(f"      [{i:2d}] {bar} {v}")
    print()


if __name__ == "__main__":
    main()
