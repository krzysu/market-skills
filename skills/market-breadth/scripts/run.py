#!/usr/bin/env python3
"""market-breadth — cross-sectional altcoin breadth (% of basket outperforming the benchmark).

Ticker-agnostic rotation read: resolves a `market-watchlist` basket, fetches
daily candles per member plus the benchmark (default BTC), and reports what
share of members outperformed the benchmark over a rolling N-day window.

Usage:
    # JSON to stdout (AXI envelope)
    uv run skills/market-breadth/scripts/run.py --json

    # Human-readable summary
    uv run skills/market-breadth/scripts/run.py

    # Different basket / window / benchmark
    uv run skills/market-breadth/scripts/run.py --basket=crypto_majors --window-days=30 --json
"""

import sys

from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    maybe_render_home_view,
    parse_axi_flags,
    print_envelope,
    resolve_fields,
)
from analysis.skill_loader import load_lib_for_script

_lib = load_lib_for_script(__file__)
analyze = _lib.analyze

DEFAULT_FIELDS = ["basket", "window_days", "members", "pct_beating", "regime", "narrative"]


def _parse_argv(argv):
    json_mode = False
    basket = "crypto_alts"
    window_days = 7
    benchmark = "btc"
    for arg in argv:
        if arg == "--json":
            json_mode = True
        elif arg.startswith("--basket="):
            basket = arg.split("=", 1)[1]
        elif arg.startswith("--window-days="):
            raw = arg.split("=", 1)[1]
            try:
                window_days = int(raw)
            except ValueError:
                print(f"error: --window-days expects an integer, got {raw!r}", file=sys.stderr)
                sys.exit(2)
            if window_days <= 0:
                print(f"error: --window-days must be >= 1, got {window_days}", file=sys.stderr)
                sys.exit(2)
        elif arg.startswith("--benchmark="):
            benchmark = arg.split("=", 1)[1]
        else:
            print(f"error: unknown flag {arg!r}", file=sys.stderr)
            print(
                "usage: run.py [--json] [--basket=NAME] [--window-days=N] [--benchmark=ALIAS] "
                "[--fields=<csv>] [--full] [--toon]",
                file=sys.stderr,
            )
            sys.exit(2)
    return json_mode, basket, window_days, benchmark


def _help_lines() -> list[str]:
    return [
        "Run `uv run skills/market-breadth/scripts/run.py --json --basket=<BASKET>` for another basket",
        "Run `uv run skills/market-breadth/scripts/run.py --json --window-days=30` for a 30-day breadth read",
        "Run `uv run skills/market-watchlist/scripts/run.py list` to see the available baskets",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def _is_empty_state(result: dict) -> bool:
    return result.get("data") is None and "count" in result and "help" in result


def _member_line(rows: list[dict]) -> str:
    return "  ".join(f"{row['ticker']} {row['return_pct']:+.2f}%" for row in rows)


def _print_text(result: dict) -> None:
    effective = result["effective_window_days"]
    window = result["window_days"]
    window_note = f"{window}d (effective {effective}d)" if effective < window else f"{window}d"
    print("\n  market-breadth")
    print(f"  basket:     {result['basket']}")
    print(f"  benchmark:  {result['benchmark']}  {result['btc_return_pct']:+.2f}%")
    print(f"  window:     {window_note}")
    print(f"  members:    {result['members']} measured")
    print(f"  beating:    {result['pct_beating']:.1f}%  -> {result['regime']}")
    print(f"  median:     {result['median_alt_return_pct']:+.2f}%")
    print(f"  leaders:    {_member_line(result['leaders'])}")
    print(f"  laggards:   {_member_line(result['laggards'])}")
    print()
    print(f"  note: {result['narrative']}")
    errors = result.get("errors") or []
    if errors:
        print("  errors:")
        for err in errors:
            print(f"    - {err}")
    print()


def main():
    fields_arg, full, toon, _from_state, _ttl, filtered = parse_axi_flags(sys.argv[1:])
    json_mode, basket, window_days, benchmark = _parse_argv(filtered)
    if len(sys.argv) == 1:
        if maybe_render_home_view(__file__, None, json_mode):
            return

    result = analyze(basket=basket, window_days=window_days, benchmark=benchmark)

    if _is_empty_state(result):
        if json_mode:
            print_envelope(result)
        else:
            for err in result["errors"]:
                print(f"error: {err}", file=sys.stderr)
            for line in result["help"]:
                print(line)
        return

    cache_run_result(__file__, result)

    if json_mode:
        fields = resolve_fields(fields_arg, full=full, default=DEFAULT_FIELDS)
        emit_envelope_json(
            result,
            count=None,
            help=_help_lines(),
            errors=result.get("errors") or [],
            fields=fields,
            toon=toon,
        )
        return

    _print_text(result)


if __name__ == "__main__":
    main()
