#!/usr/bin/env python3
"""run-all-l2 — fetch once per ticker, run all L2 skills in-process."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, print_header


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("run_all_l2_lib", lib_path)
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
        tickers = ["SPY"]

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

    print_header("RUN ALL L2 SKILLS")
    for t in tickers:
        candles = fetch_ohlc(t, source=source)
        if not candles:
            print(f"  {t}: no data")
            continue
        result = _lib.analyze(t, candles)
        print(f"  {t}")
        for skill_name, skill_result in result["skills"].items():
            pat = skill_result.get("pattern", {})
            if "error" in pat:
                print(f"    {skill_name:<28s}  error: {pat['error']}")
                continue
            present = "YES" if pat.get("present") else "no"
            cls = pat.get("classification") or "n/a"
            conf = pat.get("confidence", 0)
            maxc = pat.get("max_confidence", 5)
            print(f"    {skill_name:<28s}  {present}  ({cls}, {conf}/{maxc})")
        print()


if __name__ == "__main__":
    main()
