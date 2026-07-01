#!/usr/bin/env python3
"""market-snapshot — single-call chart sanity check (supertrend + RSI + MA alignment).

Use on a lower timeframe (e.g. 4h) to validate a higher-TF L3 idea before entry.

Examples:
    uv run skills/market-snapshot/scripts/run.py VVVUSD --interval=4h --period=6mo
    uv run skills/market-snapshot/scripts/run.py HYPEUSD --interval=4h --period=6mo --json
"""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, print_header, require_ticker, safe_parse_args
from analysis.skill_loader import load_lib_for_script


def analyze(ticker, *, source=None, interval="4h", period="6mo"):
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "interval": interval, "error": "no data"}

    lib = load_lib_for_script(__file__)
    return lib.analyze(candles, ticker=ticker, interval=interval, period=period)


def _format_consensus(consensus):
    if consensus is True:
        return "BULLISH consensus"
    if consensus is False:
        return "BEARISH consensus"
    return "MIXED / inconclusive"


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

    print_header("MARKET SNAPSHOT")
    print(f"  {ticker}  ({result['interval']}, price {result['current_price']})")
    st = result["supertrend"]
    arrow = "\u2191" if st["direction"] == "up" else "\u2193"
    print(f"  Supertrend(10,3): {arrow} {st['direction']:5s}  value={st['value']}")
    rsi = result["rsi"]
    print(f"  RSI(14):          {rsi['value']}  ({rsi['signal']})")
    print(f"  MA alignment:     {result['ma_alignment']}")
    print(f"  Consensus:        {_format_consensus(result['agrees_with_idea'])}")
    print()


if __name__ == "__main__":
    main()
