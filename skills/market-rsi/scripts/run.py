#!/usr/bin/env python3
"""market-rsi — RSI momentum oscillator."""

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
        "skill": "market-rsi",
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
