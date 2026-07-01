#!/usr/bin/env python3
"""strategy-exhaustion-fade — L3 exhaustion fade strategy."""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, print_header, require_ticker, safe_parse_args
from analysis.skill_loader import load_lib_for_script


def main():
    ticker, json_mode, source, interval, period = safe_parse_args(sys.argv[1:])
    require_ticker(ticker, json_mode)
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        print("no data" if not json_mode else '{"error": "no data"}')
        return

    _lib = load_lib_for_script(__file__)
    result = _lib.analyze(candles, ticker=ticker, interval=interval, period=period)

    if json_mode:
        emit_json(result)
        return

    print_header(f"STRATEGY: EXHAUSTION FADE — {ticker}")
    ideas = result.get("ideas", [])
    if not ideas:
        print(f"  {result['narrative']}")
        return

    for i, idea in enumerate(ideas, 1):
        stars = "\u2605" * idea["conviction"] + "\u2606" * (5 - idea["conviction"])
        print(f"  Idea {i}: {idea['direction'].upper()}  (conviction: {idea['conviction']}/5 {stars})")
        print(f"    Entry:      ${idea['entry_price']} ({idea['entry_type']})")
        if idea.get("entry_range"):
            print(f"    Range:      ${idea['entry_range'][0]} – ${idea['entry_range'][1]}")
        print(f"    Stop:       ${idea['stop_loss']}")
        tps = " \u2192 ".join(f"${tp}" for tp in idea["take_profit"])
        print(f"    Targets:    {tps}")
        print(f"    Reasoning:  {idea['reasoning']}")
        print(f"    Sources:    {', '.join(idea['source_skills'])}")
        print()


if __name__ == "__main__":
    main()
