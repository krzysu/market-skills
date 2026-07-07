#!/usr/bin/env python3
"""run-watchlist — bulk-run L2 + L3 across every ticker in a basket.

Driven by `analysis/watchlist.py` (the market-watchlist skill). Notes are
auto-included from `analysis/notes.py` unless `--no-notes` is passed.

Exit codes:
  0 — success (even if some tickers had fetch failures — see output)
  1 — fatal (no tickers resolved, bad watchlist path)

Usage:
  uv run skills/run-watchlist/scripts/run.py                            # all baskets
  uv run skills/run-watchlist/scripts/run.py crypto_majors              # one basket
  uv run skills/run-watchlist/scripts/run.py --tickers BTCUSD ETHUSD    # ad-hoc
  uv run skills/run-watchlist/scripts/run.py crypto_majors --l3-only
  uv run skills/run-watchlist/scripts/run.py crypto_majors --no-notes
  uv run skills/run-watchlist/scripts/run.py --watchlist /path/to/watchlist.json crypto_majors
"""

import argparse
import sys

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, parse_cli_error, print_header, render_notes
from analysis.intervals import validate_timeframe
from analysis.notes import load_active
from analysis.output import cache_run_result, maybe_render_home_view
from analysis.skill_loader import load_lib_for_script
from analysis.watchlist import (
    all_tickers,
    by_category,
    expand_tickers,
    metadata_for,
)


def _resolve_tickers(args: argparse.Namespace, path: str | None) -> tuple[list[str], str]:
    """Resolve CLI args into (canonical_ticker_list, scope_label).

    Precedence: --tickers (raw) > basket positional > all baskets.
    Bare aliases are expanded via the watchlist.
    """
    if args.tickers:
        return expand_tickers(args.tickers, path=path), f"tickers: {','.join(args.tickers)}"

    if args.basket:
        members = by_category(args.basket, path=path)
        if not members:
            print(f"error: basket {args.basket!r} not found or empty", file=sys.stderr)
            sys.exit(1)
        return members, f"basket: {args.basket}"

    all_t = all_tickers(path=path)
    if not all_t:
        print(
            "error: no tickers found. Provide --tickers, a basket name, or create "
            "skills/market-watchlist/data/watchlist.json",
            file=sys.stderr,
        )
        sys.exit(1)
    return all_t, "all baskets"


def main():
    p = argparse.ArgumentParser(
        prog="run-watchlist",
        description="Bulk-run L2 + L3 skills across every ticker in a basket. Driven by market-watchlist.",
    )
    p.add_argument("basket", nargs="?", help="Basket name (e.g. crypto_majors). Omit to scan all baskets.")
    p.add_argument("--tickers", nargs="+", help="Ad-hoc ticker list (bare aliases supported via watchlist)")
    p.add_argument("--watchlist", help="Path to watchlist.json (default: skills/market-watchlist/data/watchlist.json)")
    p.add_argument("--l2-only", action="store_true", help="Skip L3 strategies")
    p.add_argument("--l3-only", action="store_true", help="Skip L2 patterns")
    p.add_argument("--no-notes", action="store_true", help="Skip loading active notes")
    p.add_argument("--interval", default="1d", help="Candle interval (default: 1d)")
    p.add_argument("--period", default="1y", help="Candle period (default: 1y)")
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    p.add_argument("--source", help="Data source override (e.g. yfinance, kraken)")
    args = p.parse_args()

    if args.l2_only and args.l3_only:
        print("error: --l2-only and --l3-only are mutually exclusive", file=sys.stderr)
        sys.exit(2)

    if not args.basket and not args.tickers:
        if maybe_render_home_view(__file__, None, args.json):
            return

    validate_timeframe(args.interval, args.period)

    wl_path = args.watchlist

    include_l2 = not args.l3_only
    include_l3 = not args.l2_only
    include_notes = not args.no_notes

    tickers, scope_label = _resolve_tickers(args, wl_path)
    if not tickers:
        print("error: resolved ticker list is empty", file=sys.stderr)
        sys.exit(1)

    lib = load_lib_for_script(__file__)

    if args.json:
        out: dict = {
            "scope": scope_label,
            "interval": args.interval,
            "period": args.period,
            "tickers": {},
        }
        for t in tickers:
            candles = fetch_ohlc(t, interval=args.interval, period=args.period, source=args.source)
            if not candles:
                out["tickers"][t] = {"error": "no data"}
                continue
            meta = metadata_for(t, path=wl_path)
            out["tickers"][t] = lib.analyze_ticker(
                t,
                candles,
                metadata=meta,
                include_l2=include_l2,
                include_l3=include_l3,
                include_notes=include_notes,
                notes_loader=load_active,
                interval=args.interval,
                period=args.period,
            )
        out["summary"] = f"{scope_label}: {len(tickers)} ticker(s)"
        cache_run_result(__file__, out)
        emit_json(out)
        return

    print_header(f"RUN WATCHLIST ({scope_label})")
    print(f"  {len(tickers)} ticker(s) | interval={args.interval} period={args.period}")
    print(f"  include_l2={include_l2} include_l3={include_l3} include_notes={include_notes}")
    print()
    for t in tickers:
        candles = fetch_ohlc(t, interval=args.interval, period=args.period, source=args.source)
        if not candles:
            print(f"  {t}: no data")
            continue
        meta = metadata_for(t, path=wl_path)
        result = lib.analyze_ticker(
            t,
            candles,
            metadata=meta,
            include_l2=include_l2,
            include_l3=include_l3,
            include_notes=include_notes,
            notes_loader=load_active,
            interval=args.interval,
            period=args.period,
        )
        print(f"  {t}")
        if meta:
            bits = []
            if meta.get("tier") is not None:
                bits.append(f"tier={meta['tier']}")
            if meta.get("tracking_only"):
                bits.append("tracking-only")
            if bits:
                print(f"    meta: {' '.join(bits)}")
        if include_l2:
            for skill_name, skill_result in (result.get("l2") or {}).items():
                if "error" in skill_result:
                    print(f"    L2 {skill_name:<26}  error: {skill_result['error']}")
                    continue
                pat = skill_result.get("pattern", {})
                present = "YES" if pat.get("present") else "no"
                cls = pat.get("classification") or "n/a"
                conf = pat.get("confidence", 0)
                maxc = pat.get("max_confidence", 5)
                print(f"    L2 {skill_name:<26}  {present}  ({cls}, {conf}/{maxc})")
        if include_l3:
            for strat_name, strat_result in (result.get("l3") or {}).items():
                ideas = strat_result.get("ideas", [])
                if not ideas:
                    narr = strat_result.get("narrative", "?")[:60]
                    print(f"    L3 {strat_name:<26}  no ideas — {narr}")
                    continue
                dirs = ", ".join(i["direction"] for i in ideas)
                best = max(ideas, key=lambda i: i.get("conviction", 0))
                print(
                    f"    L3 {strat_name:<26}  {len(ideas)} idea(s) ({dirs}) — best conviction {best['conviction']}/5"
                )
        if include_notes:
            notes = result.get("notes", [])
            for line in render_notes(notes):
                print(line)
        print()


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(parse_cli_error(e), file=sys.stderr)
        sys.exit(2)
