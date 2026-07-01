#!/usr/bin/env python3
"""market-squeeze — Bollinger Band / Keltner Channel squeeze momentum."""

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
        "skill": "market-squeeze",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "indicators": result,
        "score": None,
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
