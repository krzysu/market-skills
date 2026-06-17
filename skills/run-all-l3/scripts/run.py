#!/usr/bin/env python3
"""run-all-l3 — fetch once per ticker, run all L3 strategies in-process."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, print_header


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("run_all_l3_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_argv(argv):
    tickers = []
    json_mode = False
    source = None
    for a in argv:
        if a == "--json":
            json_mode = True
        elif a.startswith("--source="):
            source = a.split("=", 1)[1]
        elif not a.startswith("--"):
            tickers.append(a)
    return tickers, json_mode, source


def main():
    tickers, json_mode, source = _parse_argv(sys.argv[1:])
    if not tickers:
        if json_mode:
            print('{"error": "at least one ticker required"}')
        else:
            print("usage: run.py TICKER [TICKER ...] [--json] [--source=PROVIDER]")
        sys.exit(2)

    _lib = _load_lib()

    if json_mode:
        out = {"tickers": {}}
        for t in tickers:
            candles = fetch_ohlc(t, source=source)
            if not candles:
                out["tickers"][t] = {"error": "no data"}
                continue
            out["tickers"][t] = _lib.analyze(t, candles)
        emit_json(out)
        return

    print_header("RUN ALL L3 STRATEGIES")
    for t in tickers:
        candles = fetch_ohlc(t, source=source)
        if not candles:
            print(f"  {t}: no data")
            continue
        result = _lib.analyze(t, candles)
        print(f"  {t}")
        for strat_name, strat_result in result["strategies"].items():
            ideas = strat_result.get("ideas", [])
            if not ideas:
                narr = strat_result.get("narrative", "?")[:60]
                print(f"    {strat_name:<30s}  no ideas — {narr}")
                continue
            dirs = ", ".join(i["direction"] for i in ideas)
            best = max(ideas, key=lambda i: i.get("conviction", 0))
            print(f"    {strat_name:<30s}  {len(ideas)} idea(s) ({dirs}) — best conviction {best['conviction']}/5")
        print()


if __name__ == "__main__":
    main()
