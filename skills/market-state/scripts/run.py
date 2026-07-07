#!/usr/bin/env python3
"""market-state — cross-skill session-start dashboard.

Reads the per-skill state caches written by phase 3 home views
(macro / valuation / movers / watchlist / conviction / notes) and
emits a single AXI envelope so the LLM can read the state of the
world in one call at session start.

No I/O at runtime — every field is a read from a JSON cache. If
the underlying skills haven't been run yet, the corresponding
source is `null` and the freshness map says "no cache".

Usage:
    uv run skills/market-state/scripts/run.py            # home view
    uv run skills/market-state/scripts/run.py --json     # full envelope
    uv run skills/market-state/scripts/run.py --fields=sources,freshness
    uv run skills/market-state/scripts/run.py --full     # full payload
"""

import sys

from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    maybe_render_home_view,
    parse_axi_flags,
    resolve_fields,
)
from analysis.skill_loader import load_lib_for_script

DEFAULT_FIELDS = ["summary", "freshness", "sources_cached", "sources_total"]


def _help_lines() -> list[str]:
    return [
        "Refresh stale sources with `<skill> --json` before relying on them",
        "Run `market-state --json --full` to see the full dashboard with slim source payloads",
        "Pass --fields=<csv> to project or --full for the complete payload",
    ]


def main():
    fields_arg, full, toon, filtered_argv = parse_axi_flags(sys.argv[1:])

    if len(sys.argv) == 1:
        if maybe_render_home_view(__file__, None, False):
            return

    json_mode = "--json" in filtered_argv
    _lib = load_lib_for_script(__file__)
    state = _lib.compose_state()

    if not json_mode:
        print(f"market-state — {state['summary']}")
        print()
        for label, age in state["freshness"].items():
            print(f"  {label:<14s}  {age}")
        print()
        for line in _help_lines():
            print(f"  · {line}")
        print()
        return

    fields = resolve_fields(fields_arg, full=full, default=DEFAULT_FIELDS)
    cache_run_result(__file__, state)
    emit_envelope_json(
        state,
        count=state["sources_cached"],
        help=_help_lines(),
        errors=[
            f"missing cache: {label}"
            for label, age in state["freshness"].items()
            if age == "no cache"
        ],
        fields=fields, toon=toon,)


if __name__ == "__main__":
    main()
