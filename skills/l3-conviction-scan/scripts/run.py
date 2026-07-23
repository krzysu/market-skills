#!/usr/bin/env python3
"""l3-conviction-scan — flatten + rank L3 ideas across watchlist baskets.

In-process runner: imports ``skills.run-all-l3.lib.analyze`` via
``analysis.skill_loader.load_lib_for_script`` and re-uses the same
``analysis.watchlist`` resolver the other batch runners do. No subprocess,
no host-specific paths.

Usage:
  uv run skills/l3-conviction-scan/scripts/run.py crypto_majors crypto_alts
  uv run skills/l3-conviction-scan/scripts/run.py crypto_alts --interval 4h --period 3mo --top 10
  uv run skills/l3-conviction-scan/scripts/run.py crypto_majors crypto_alts --json
  uv run skills/l3-conviction-scan/scripts/run.py crypto_majors crypto_alts --narrative
"""

import argparse
import sys

from analysis.formatting import parse_cli_error
from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    empty_state,
    maybe_render_home_view,
    parse_axi_flags,
    print_envelope,
    resolve_fields,
)
from analysis.skill_loader import load_lib_for_script


def main() -> int:
    p = argparse.ArgumentParser(
        prog="l3-conviction-scan",
        description=(
            "Conviction-ranked L3 cross-ticker view. Fetches candles once per "
            "ticker, runs all L3 strategies in-process, ranks ideas by conviction."
        ),
    )
    p.add_argument("baskets", nargs="+", help="Watchlist basket names (e.g. crypto_majors crypto_alts)")
    p.add_argument("--interval", default=DEFAULT_INTERVAL, help="Candle interval (default: 1d)")
    p.add_argument("--period", default=DEFAULT_PERIOD, help="Candle lookback (default: 1y)")
    p.add_argument("--source", help="Data provider override (e.g. yfinance, kraken, hl)")
    p.add_argument(
        "--watchlist",
        help="Path to watchlist.json (default: skills/market-watchlist/data/watchlist.json)",
    )
    p.add_argument("--top", type=int, default=None, help="Cap output rows to top N by conviction")
    p.add_argument("--narrative", action="store_true", help="Append top-5 strategy narratives after the table")
    p.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")

    fields_arg, full, toon, from_state, ttl, filtered_argv = parse_axi_flags(sys.argv[1:])
    if len(sys.argv) == 1:
        if maybe_render_home_view(__file__, None, False):
            return 0
    args = p.parse_args(filtered_argv)

    try:
        validate_timeframe(args.interval, args.period)
    except ValueError as e:
        print(parse_cli_error(e), file=sys.stderr)
        return 2

    lib = load_lib_for_script(__file__)

    try:
        rows = lib.scan(
            args.baskets,
            interval=args.interval,
            period=args.period,
            source=args.source,
            watchlist_path=args.watchlist,
        )
    except ValueError as e:
        print(parse_cli_error(e), file=sys.stderr)
        return 2

    if args.json:
        payload = lib.render_json(
            rows,
            baskets=args.baskets,
            interval=args.interval,
            period=args.period,
            top=args.top,
        )
        ideas = payload.get("ideas") or []
        if not rows:
            print_envelope(
                empty_state(
                    help=[
                        "Run with explicit tickers or check `market-watchlist list --json` for baskets",
                        "Pass --top=N to cap the result",
                    ],
                )
            )
            return 0
        cache_run_result(__file__, payload)
        emit_envelope_json(
            payload,
            count=len(ideas),
            help=[
                "Pass --top=N to cap the result by conviction",
                "Pass --full for the full payload or --fields=<csv> to project",
            ],
            fields=resolve_fields(
                fields_arg,
                full=full,
                default=["ideas", "baskets", "interval", "period", "total"],
            ),
            toon=toon,
        )
        return 0

    print(lib.render_text(rows, top=args.top, tf=args.interval))
    if args.narrative:
        top_rows = lib.rank_ideas(rows, top=5)
        print("\n--- TOP NARRATIVES ---")
        for r in top_rows:
            print(f"[{r.get('_tf', '?')}] {r['ticker']} {r['strategy']} conv={r['conviction']}: {r['narrative']}")
    total = len(lib.rank_ideas(rows))
    if args.top is None or args.top >= total:
        print(f"\nTotal ideas surfaced: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
