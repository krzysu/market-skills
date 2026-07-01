#!/usr/bin/env python3
"""market-macd — MACD momentum indicator."""

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
        "skill": "market-macd",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "indicators": result,
        "score": result.get("score"),
        "signal": result.get("signal"),
        "zone": result.get("zone"),
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
