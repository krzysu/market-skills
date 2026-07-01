#!/usr/bin/env python3
"""market-volatility — Realized volatility analysis, percentile rank, regime."""

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
        "skill": "market-volatility",
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
