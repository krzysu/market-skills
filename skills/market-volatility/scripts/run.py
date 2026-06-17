#!/usr/bin/env python3
"""market-volatility — Realized volatility analysis, percentile rank, regime."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_volatility_lib", lib_path)
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
        "skill": "market-volatility",
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
    print_header("VOLATILITY ANALYSIS")
    print(f"  {ticker}")
    print()
    print(f"    Realized Vol 7d:   {ind.get('realized_vol_7d', 'N/A'):>8}%")
    print(f"    Realized Vol 30d:  {ind.get('realized_vol_30d', 'N/A'):>8}%")
    print(f"    Percentile Rank:   {ind.get('percentile_rank_30d', 'N/A'):>8}")
    print(f"    Regime:            {ind.get('regime', 'N/A'):>8}")
    print(f"    Trend:             {ind.get('trend', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
