#!/usr/bin/env python3
"""strategy-liquidity-sweep — L3 liquidity sweep reversal strategy."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util

from lib.data import fetch_ohlc
from lib.formatting import emit_json, parse_args, print_header


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_liquidity_sweep_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ticker, json_mode, source = parse_args(sys.argv[1:], default_ticker="BTC-USD")
    candles = fetch_ohlc(ticker, source=source)
    if not candles:
        print("no data" if not json_mode else '{"error": "no data"}')
        return

    _lib = _load_lib()
    result = _lib.analyze(candles)
    for idea in result.get("ideas", []):
        idea["pair"] = ticker

    if json_mode:
        emit_json(result)
        return

    print_header(f"STRATEGY: LIQUIDITY SWEEP — {ticker}")
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
