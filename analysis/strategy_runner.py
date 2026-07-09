"""Shared CLI runner for the six L3 strategy skills.

All six ``skills/strategy-*/scripts/run.py`` files were byte-identical
modulo a one-word strategy title and a docstring. That boilerplate
(``main()`` arg parsing, AXI envelope emit, idea-projection, pretty
header rendering) lives here; each skill's ``scripts/run.py`` is now a
~12-line shim that imports :func:`run_strategy_cli` with its name.
"""

from __future__ import annotations

import sys
from typing import Any

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

DEFAULT_IDEA_FIELDS = ["pair", "direction", "conviction", "version", "entry_price", "stop_loss"]


def _parse_asset_class(argv: list[str]) -> str | None:
    """Extract ``--asset-class=CLASS`` from argv (post-AXI-strip)."""
    for arg in argv:
        if arg.startswith("--asset-class="):
            return arg.split("=", 1)[1]
    return None


def _enrich_ideas(ideas: list[dict]) -> list[dict]:
    enriched = []
    for idea in ideas:
        idea = dict(idea)
        if idea.get("reasoning"):
            idea["reasoning"] = truncate(idea["reasoning"], limit=IDEA_FIELDS_LIMIT)
        enriched.append(idea)
    return enriched


def _help_lines(ticker: str | None, has_ideas: bool) -> list[str]:
    lines = [
        "Run `risk-engine --intent <file> --portfolio spot --json` to vet the top idea",
        f"Run `run-all-l3 {ticker or 'TICKER'} --json` for the full strategy batch",
    ]
    if not has_ideas:
        lines.insert(0, f"No ideas emitted for {ticker or 'TICKER'} — narrative explains why")
    return lines


def _strategy_analyze(
    script_file: str,
    ticker: str,
    *,
    source: str | None = None,
    interval: str = "1d",
    period: str = "1y",
    asset_class: str | None = None,
) -> dict[str, Any]:
    """Fetch candles, run the strategy lib, return the canonical result dict."""
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "ideas": [], "narrative": "no data", "error": "no data"}

    resolved_ac = asset_class if asset_class is not None else metadata_for(ticker).get("asset_class")
    _lib = load_lib_for_script(script_file)
    result = _lib.analyze(
        candles,
        ticker=ticker,
        interval=interval,
        period=period,
        asset_class=resolved_ac,
    )
    return {
        "ticker": ticker,
        "ideas": _enrich_ideas(result.get("ideas", [])),
        "narrative": truncate(result.get("narrative", ""), limit=NARRATIVE_LIMIT),
    }


def _render_human(strategy_title: str, ticker: str, result: dict[str, Any]) -> None:
    print_header(f"STRATEGY: {strategy_title} — {ticker}")
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


def _emit_json(
    strategy_title: str,
    ticker: str,
    result: dict[str, Any],
    *,
    fields_arg: Any,
    full: bool,
    toon: bool,
) -> None:
    ideas = result.get("ideas", [])
    if "error" in result and not ideas:
        print_envelope(empty_state(errors=[result["error"]], help=_help_lines(ticker, False)))
        return
    fields = resolve_fields(fields_arg, full=full, default=DEFAULT_IDEA_FIELDS)
    projected = [project_fields(idea, fields) for idea in ideas]
    emit_envelope_json(
        {"ticker": ticker, "ideas": projected, "narrative": result.get("narrative", "")},
        count=len(projected),
        help=_help_lines(ticker, bool(projected)),
        toon=toon,
    )


def run_strategy_cli(strategy_title: str, script_file: str) -> None:
    """Entry point shared by every strategy ``scripts/run.py``.

    ``strategy_title`` is the human label for the header (e.g. ``"TREND FOLLOW"``).
    ``script_file`` is ``__file__`` from the calling script — used for
    ``cache_run_result`` and ``maybe_render_home_view`` so the home-view
    state lives in the per-skill namespace.
    """
    fields_arg, full, toon, filtered_argv = parse_axi_flags(sys.argv[1:])
    ticker, json_mode, source, interval, period = safe_parse_args(filtered_argv)
    if maybe_render_home_view(script_file, ticker, json_mode):
        return
    override = _parse_asset_class(filtered_argv)
    result = _strategy_analyze(
        script_file,
        ticker,
        source=source,
        interval=interval,
        period=period,
        asset_class=override,
    )
    cache_run_result(script_file, result)

    if json_mode:
        _emit_json(strategy_title, ticker or "TICKER", result, fields_arg=fields_arg, full=full, toon=toon)
        return
    _render_human(strategy_title, ticker or "TICKER", result)


__all__ = [
    "IDEA_FIELDS_LIMIT",
    "NARRATIVE_LIMIT",
    "run_strategy_cli",
]
