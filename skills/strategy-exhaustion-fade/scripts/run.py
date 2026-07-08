#!/usr/bin/env python3
"""strategy-exhaustion-fade — L3 exhaustion fade strategy."""

import sys

from analysis.data import fetch_ohlc
from analysis.formatting import print_header, safe_parse_args
from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    empty_state,
    maybe_render_home_view,
    parse_axi_flags,
    print_envelope,
    project_fields,
    resolve_fields,
    truncate,
)
from analysis.skill_loader import load_lib_for_script
from analysis.watchlist import metadata_for

NARRATIVE_LIMIT = 160
IDEA_FIELDS_LIMIT = 80


def _parse_asset_class(argv):
    """Extract ``--asset-class=CLASS`` from argv."""
    for arg in argv:
        if arg.startswith("--asset-class="):
            return arg.split("=", 1)[1]
    return None


def analyze(ticker, *, source=None, interval="1d", period="1y", asset_class=None):
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "ideas": [], "narrative": "no data", "error": "no data"}

    override = asset_class
    resolved_ac = override if override is not None else metadata_for(ticker).get("asset_class")

    _lib = load_lib_for_script(__file__)
    result = _lib.analyze(
        candles,
        ticker=ticker,
        interval=interval,
        period=period,
        asset_class=resolved_ac,
    )
    ideas = result.get("ideas", [])
    enriched = []
    for idea in ideas:
        idea = dict(idea)
        if idea.get("reasoning"):
            idea["reasoning"] = truncate(idea["reasoning"], limit=IDEA_FIELDS_LIMIT)
        enriched.append(idea)
    return {
        "ticker": ticker,
        "ideas": enriched,
        "narrative": truncate(result.get("narrative", ""), limit=NARRATIVE_LIMIT),
    }


def _help_lines(ticker: str, has_ideas: bool) -> list[str]:
    lines = [
        "Run `risk-engine --intent <file> --portfolio spot --json` to vet the top idea",
        f"Run `run-all-l3 {ticker} --json` for the full strategy batch",
    ]
    if not has_ideas:
        lines.insert(0, f"No ideas emitted for {ticker} — narrative explains why")
    return lines


def main():
    fields_arg, full, toon, filtered_argv = parse_axi_flags(sys.argv[1:])
    ticker, json_mode, source, interval, period = safe_parse_args(filtered_argv)
    if maybe_render_home_view(__file__, ticker, json_mode):
        return
    override = _parse_asset_class(filtered_argv)
    result = analyze(
        ticker,
        source=source,
        interval=interval,
        period=period,
        asset_class=override,
    )
    cache_run_result(__file__, result)

    if json_mode:
        ideas = result.get("ideas", [])
        if "error" in result and not ideas:
            print_envelope(empty_state(errors=[result["error"]], help=_help_lines(ticker or "TICKER", False)))
            return
        fields = resolve_fields(
            fields_arg,
            full=full,
            default=["pair", "direction", "conviction", "version", "entry_price", "stop_loss"],
        )
        projected_ideas = [project_fields(idea, fields) for idea in ideas]
        emit_envelope_json(
            {"ticker": ticker, "ideas": projected_ideas, "narrative": result.get("narrative", "")},
            count=len(projected_ideas),
            help=_help_lines(ticker, bool(projected_ideas)),
            toon=toon,
        )
        return

    print_header(f"STRATEGY: EXHAUSTION FADE — {ticker}")
    ideas = result.get("ideas", [])
    if not ideas:
        print(f"  {result.get('narrative', '')}")
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
        print(f"    Reasoning:  {idea.get('reasoning', '')}")
        print(f"    Sources:    {', '.join(idea.get('source_skills', []))}")
        print()


if __name__ == "__main__":
    main()
