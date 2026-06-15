#!/usr/bin/env python3
"""market-s-r — Support and Resistance from swing point clustering."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_s_r_lib", lib_path)
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
        "skill": "market-s-r",
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
    print_header("SUPPORT & RESISTANCE")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print()
    if ind.get("nearest_resistance") is not None:
        r_dist = ind.get("resistance_distance_pct", 0)
        r_touches = ind.get("resistance_touches", 0)
        print(f"    Resistance: {ind['nearest_resistance']:>10,.2f}  ({r_dist:+.2f}%, {r_touches} touches)")
    else:
        print("    Resistance: None")
    if ind.get("nearest_support") is not None:
        s_dist = ind.get("support_distance_pct", 0)
        s_touches = ind.get("support_touches", 0)
        print(f"    Support:    {ind['nearest_support']:>10,.2f}  ({s_dist:+.2f}%, {s_touches} touches)")
    else:
        print("    Support:    None")
    print()
    print(f"    Support count:  {ind.get('support_count', 0)}")
    print(f"    Resistance count: {ind.get('resistance_count', 0)}")
    if ind.get("sits_on_level"):
        print("    \u26a0 Price sits on a detected level")
    print()


if __name__ == "__main__":
    main()
