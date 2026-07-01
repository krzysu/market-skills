#!/usr/bin/env python3
"""run-all-l3 — fetch once per ticker, run all L3 strategies in-process."""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, parse_cli_error, print_header, render_notes
from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.macro import fetch_regime
from analysis.notes import load_active as load_notes
from analysis.skill_loader import load_lib_for_script
from analysis.watchlist import metadata_for


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
        out["macro"] = fetch_regime()
        for t in tickers:
            candles = fetch_ohlc(t, interval=interval, period=period, source=source)
            if not candles:
                out["tickers"][t] = {"error": "no data"}
                continue
            meta = metadata_for(t)
            ac = meta.get("asset_class")
            entry = _lib.analyze(t, candles, interval=interval, period=period, asset_class=ac)
            if include_notes:
                entry["notes"] = load_notes(t)
            out["tickers"][t] = entry
        emit_json(out)
        return

    print_header("RUN ALL L3 STRATEGIES")
    print(f"  interval={interval} period={period}")
    print()
    macro = fetch_regime()
    regime = macro.get("regime", {})
    print(
        f"  macro:  "
        f"{regime.get('risk_appetite', '?')} / "
        f"{regime.get('liquidity', '?')} / "
        f"{regime.get('sentiment', '?')}"
    )
    print(f"  note:   {macro.get('regime_note', '')}")
    if macro.get("incomplete"):
        errs = macro.get("errors", [])
        print(f"  [REGIME INCOMPLETE — {len(errs)} input(s) missing] — risks treated as UNKNOWN")
        for e in errs:
            print(f"    - {e}")
    print()
    for t in tickers:
        candles = fetch_ohlc(t, interval=interval, period=period, source=source)
        if not candles:
            print(f"  {t}: no data")
            continue
        meta = metadata_for(t)
        ac = meta.get("asset_class")
        result = _lib.analyze(t, candles, interval=interval, period=period, asset_class=ac)
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
