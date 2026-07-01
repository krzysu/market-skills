#!/usr/bin/env python3
"""run-all-l2 — fetch once per ticker, run all L2 skills in-process."""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, parse_cli_error, print_header, render_notes
from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.notes import load_active as load_notes
from analysis.skill_loader import load_lib_for_script


def _parse_argv(argv):
    tickers = []
    json_mode = False
    source = None
    include_notes = False
    interval = DEFAULT_INTERVAL
    period = DEFAULT_PERIOD
    for a in argv:
        if a == "--json":
            json_mode = True
        elif a.startswith("--source="):
            source = a.split("=", 1)[1]
        elif a.startswith("--interval="):
            interval = a.split("=", 1)[1]
        elif a.startswith("--period="):
            period = a.split("=", 1)[1]
        elif a == "--include-notes":
            include_notes = True
        elif not a.startswith("--"):
            tickers.append(a)
    validate_timeframe(interval, period)
    return tickers, json_mode, source, interval, period, include_notes


def main():
    tickers, json_mode, source, interval, period, include_notes = _parse_argv(sys.argv[1:])
    if not tickers:
        if json_mode:
            print('{"error": "at least one ticker required"}')
        else:
            print(
                "usage: run.py TICKER [TICKER ...] [--json] [--source=PROVIDER] "
                "[--interval=INTERVAL] [--period=PERIOD] [--include-notes]"
            )
        sys.exit(2)

    _lib = load_lib_for_script(__file__)

    if json_mode:
        out = {"interval": interval, "period": period, "tickers": {}}
        for t in tickers:
            candles = fetch_ohlc(t, interval=interval, period=period, source=source)
            if not candles:
                out["tickers"][t] = {"error": "no data"}
                continue
            entry = _lib.analyze(t, candles, interval=interval, period=period)
            if include_notes:
                entry["notes"] = load_notes(t)
            out["tickers"][t] = entry
        emit_json(out)
        return

    print_header("RUN ALL L2 SKILLS")
    print(f"  interval={interval} period={period}")
    print()
    for t in tickers:
        candles = fetch_ohlc(t, interval=interval, period=period, source=source)
        if not candles:
            print(f"  {t}: no data")
            continue
        result = _lib.analyze(t, candles, interval=interval, period=period)
        print(f"  {t}")
        for skill_name, skill_result in result["skills"].items():
            if "error" in skill_result:
                print(f"    {skill_name:<28s}  error: {skill_result['error']}")
                continue
            pat = skill_result.get("pattern", {})
            if "error" in pat:
                print(f"    {skill_name:<28s}  error: {pat['error']}")
                continue
            present = "YES" if pat.get("present") else "no"
            cls = pat.get("classification") or "n/a"
            conf = pat.get("confidence", 0)
            maxc = pat.get("max_confidence", 5)
            print(f"    {skill_name:<28s}  {present}  ({cls}, {conf}/{maxc})")
        if include_notes:
            notes = load_notes(t)
            for line in render_notes(notes):
                print(line)
        print()


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(parse_cli_error(e), file=sys.stderr)
        sys.exit(2)
