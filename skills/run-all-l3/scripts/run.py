#!/usr/bin/env python3
"""run-all-l3 — fetch once per ticker, run all L3 strategies in-process."""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import parse_cli_error, print_header, render_notes
from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.macro import fetch_regime
from analysis.notes import load_active as load_notes
from analysis.output import (
    emit_envelope_json,
    empty_state,
    parse_axi_flags,
    print_envelope,
    project_fields,
    resolve_fields,
    truncate,
)
from analysis.skill_loader import load_lib_for_script
from analysis.watchlist import metadata_for


def _parse_argv(argv):
    tickers = []
    json_mode = False
    source = None
    include_notes = False
    interval = DEFAULT_INTERVAL
    period = DEFAULT_PERIOD
    top = None
    fired_only = False
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
        elif a.startswith("--top="):
            top = int(a.split("=", 1)[1])
        elif a == "--fired-only":
            fired_only = True
        elif not a.startswith("--"):
            tickers.append(a)
    validate_timeframe(interval, period)
    return tickers, json_mode, source, interval, period, include_notes, top, fired_only


def _idea_score(idea: dict) -> int:
    return idea.get("conviction", 0) or 0


def _cap_ideas(strategy_result: dict, top: int | None) -> dict:
    if not top:
        return strategy_result
    ideas = strategy_result.get("ideas", [])
    if not ideas:
        return strategy_result
    ranked = sorted(ideas, key=_idea_score, reverse=True)
    capped = dict(strategy_result)
    capped["ideas"] = ranked[:top]
    capped["total_ideas"] = len(ideas)
    return capped


def _strip_when_fired_only(strategy_result: dict) -> dict | None:
    if not strategy_result.get("ideas"):
        return None
    return strategy_result


def main():
    fields_arg, full, filtered_argv = parse_axi_flags(sys.argv[1:])
    tickers, json_mode, source, interval, period, include_notes, top, fired_only = _parse_argv(filtered_argv)
    if not tickers:
        if json_mode:
            print_envelope(
                empty_state(
                    errors=["at least one ticker required"],
                    help=["Run `run-all-l3 HYPEUSD SOLUSD --json` to scan a pair"],
                )
            )
        else:
            print(
                "usage: run.py TICKER [TICKER ...] [--json] [--source=PROVIDER] "
                "[--interval=INTERVAL] [--period=PERIOD] [--include-notes] "
                "[--top=N] [--fired-only] [--fields=<csv>] [--full]"
            )
        sys.exit(2)

    _lib = load_lib_for_script(__file__)

    if json_mode:
        out = {"interval": interval, "period": period, "tickers": {}, "strategies": []}
        macro = fetch_regime()
        out["macro"] = macro
        total_ideas = 0
        total_fired_strategies = 0
        errors: list[str] = []
        idea_fields = resolve_fields(
            fields_arg,
            full=full,
            default=["pair", "direction", "conviction", "version", "entry_price", "stop_loss"],
        )
        for t in tickers:
            candles = fetch_ohlc(t, interval=interval, period=period, source=source)
            if not candles:
                out["tickers"][t] = {"error": "no data", "ideas_count": 0, "fired_strategies": 0}
                errors.append(f"{t}: no data")
                continue
            meta = metadata_for(t)
            ac = meta.get("asset_class")
            entry = _lib.analyze(t, candles, interval=interval, period=period, asset_class=ac)
            strat_results = entry.get("strategies", {})
            tick_ideas = 0
            tick_fired = 0
            cap_results: dict[str, dict] = {}
            for strat_name, strat_result in strat_results.items():
                capped = _cap_ideas(strat_result, top)
                if fired_only:
                    kept = _strip_when_fired_only(capped)
                    if kept is None:
                        continue
                else:
                    kept = capped
                if kept.get("ideas"):
                    kept["ideas"] = [project_fields(i, idea_fields) for i in kept["ideas"]]
                cap_results[strat_name] = kept
                if kept.get("ideas"):
                    tick_fired += 1
                    tick_ideas += len(kept["ideas"])
            entry["strategies"] = cap_results
            entry["ideas_count"] = tick_ideas
            entry["fired_strategies"] = tick_fired
            if include_notes:
                entry["notes"] = load_notes(t)
            if "narrative" in entry and isinstance(entry["narrative"], str):
                entry["narrative"] = truncate(entry["narrative"], limit=160)
            out["tickers"][t] = entry
            total_ideas += tick_ideas
            total_fired_strategies += tick_fired
        out["ideas_count"] = total_ideas
        out["fired_strategies"] = total_fired_strategies
        help_lines = [
            "Pass --top=N to cap ideas per ticker (sorted by conviction)",
            "Pass --fired-only to drop strategies that emitted no ideas",
            "Pass --fields=<csv> to project per-idea fields or --full for the full payload",
        ]
        if top is None:
            help_lines.insert(0, "Tip: --top=3 returns the highest-conviction ideas per ticker")
        emit_envelope_json(
            out,
            count=len(tickers),
            help=help_lines,
            errors=errors,
        )
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
            best = max(ideas, key=_idea_score)
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
