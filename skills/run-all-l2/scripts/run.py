#!/usr/bin/env python3
"""run-all-l2 — fetch once per ticker, run all L2 skills in-process."""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import parse_cli_error, print_header, render_notes
from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.notes import load_active as load_notes
from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    maybe_render_home_view,
    parse_axi_flags,
    print_axi_usage,
    resolve_fields,
    truncate,
)
from analysis.skill_loader import load_lib_for_script


def _parse_argv(argv):
    tickers = []
    json_mode = False
    source = None
    include_notes = False
    interval = DEFAULT_INTERVAL
    period = DEFAULT_PERIOD
    fired_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            json_mode = True
            i += 1
        elif a in ("--help", "-h"):
            print_axi_usage()
            sys.exit(0)
        elif a == "--source" or a.startswith("--source="):
            if a.startswith("--source="):
                source = a.split("=", 1)[1]
                i += 1
            elif i + 1 < len(argv):
                source = argv[i + 1]
                i += 2
            else:
                raise ValueError("--source requires a value")
        elif a == "--interval" or a.startswith("--interval="):
            if a.startswith("--interval="):
                interval = a.split("=", 1)[1]
                i += 1
            elif i + 1 < len(argv):
                interval = argv[i + 1]
                i += 2
            else:
                raise ValueError("--interval requires a value")
        elif a == "--period" or a.startswith("--period="):
            if a.startswith("--period="):
                period = a.split("=", 1)[1]
                i += 1
            elif i + 1 < len(argv):
                period = argv[i + 1]
                i += 2
            else:
                raise ValueError("--period requires a value")
        elif a == "--include-notes":
            include_notes = True
            i += 1
        elif a == "--fired-only":
            fired_only = True
            i += 1
        elif not a.startswith("--"):
            tickers.append(a)
            i += 1
        else:
            raise ValueError(f"unrecognized flag: {a}")
    validate_timeframe(interval, period)
    return tickers, json_mode, source, interval, period, include_notes, fired_only


def _skill_fired(skill_result: dict) -> bool:
    pat = skill_result.get("pattern") or {}
    return bool(pat.get("present")) and pat.get("classification") is not None


def main():
    fields_arg, full, toon, from_state, ttl, filtered_argv = parse_axi_flags(sys.argv[1:])
    tickers, json_mode, source, interval, period, include_notes, fired_only = _parse_argv(filtered_argv)
    if not tickers:
        if maybe_render_home_view(__file__, None, json_mode):
            return
        print_axi_usage()
        sys.exit(2)

    _lib = load_lib_for_script(__file__)

    if json_mode:
        out = {"interval": interval, "period": period, "tickers": {}}
        total_fired = 0
        errors: list[str] = []
        for t in tickers:
            candles = fetch_ohlc(t, interval=interval, period=period, source=source)
            if not candles:
                out["tickers"][t] = {"error": "no data", "fired_skills": 0, "skill_count": 0}
                errors.append(f"{t}: no data")
                continue
            entry = _lib.analyze(t, candles, interval=interval, period=period)
            skills = entry.get("skills", {})
            tick_fired = 0
            for skill_result in skills.values():
                if _skill_fired(skill_result):
                    tick_fired += 1
            if fired_only:
                skills = {k: v for k, v in skills.items() if _skill_fired(v)}
            entry["skills"] = skills
            entry["fired_skills"] = tick_fired
            entry["skill_count"] = len(skills)
            if "narrative" in entry and isinstance(entry["narrative"], str):
                entry["narrative"] = truncate(entry["narrative"], limit=160)
            if include_notes:
                entry["notes"] = load_notes(t)
            out["tickers"][t] = entry
            total_fired += tick_fired
        out["fired_skills_total"] = total_fired
        out["summary"] = f"{len(tickers)} ticker(s), {total_fired} L2 skill(s) fired"
        cache_run_result(__file__, out)
        fields = resolve_fields(fields_arg, full=full)
        emit_envelope_json(
            out,
            count=len(tickers),
            help=[
                "Pass --fired-only to drop skills that didn't fire",
                "Pass --fields=<csv> to project or --full for the full payload",
            ],
            errors=errors,
            fields=fields,
            toon=toon,
        )
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
