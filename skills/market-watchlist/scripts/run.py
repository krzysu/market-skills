#!/usr/bin/env python3
"""market-watchlist — asset registry CLI.

Manages a JSON watchlist of named baskets of tickers, used by `run-watchlist`
and other batch skills to drive bulk analysis.

Exit codes:
  0 — success
  1 — fatal (bad file, schema error)
  2 — invalid usage (missing args, unknown subcommand)

CLI:
  uv run skills/market-watchlist/scripts/run.py list                       # all baskets
  uv run skills/market-watchlist/scripts/run.py show <basket>             # full basket
  uv run skills/market-watchlist/scripts/run.py tickers [<basket>]        # flat ticker list
  uv run skills/market-watchlist/scripts/run.py resolve <alias>           # alias → ticker
  uv run skills/market-watchlist/scripts/run.py validate                  # schema check
"""

import argparse
import json
import sys

from analysis.output import emit_envelope_json, empty_state, print_envelope
from analysis.watchlist import (
    all_tickers,
    basket,
    by_category,
    categories,
    load_raw,
    metadata_for,
    provider_for,
    resolve,
    validate_storage,
)


def _cmd_list(args: argparse.Namespace) -> int:
    cats = categories(args.config)
    if args.json:
        baskets_data = {
            c: {"members": list(basket(c, args.config).keys()), "count": len(basket(c, args.config))} for c in cats
        }
        emit_envelope_json(
            {"baskets": baskets_data, "total_tickers": len(all_tickers(args.config))},
            count=len(cats),
            help=[
                "Run `market-watchlist show <basket> --json` to see one basket's metadata",
                "Run `market-watchlist tickers --json` for a flat ticker list",
            ],
        )
        return 0
    if not cats:
        print("(no baskets defined)")
        return 0
    print(f"{'Basket':<22} {'Count':>6}  Members")
    print(f"{'------':<22} {'-----':>6}  -------")
    for c in cats:
        members = basket(c, args.config)
        preview = ", ".join(list(members.keys())[:5])
        more = f" (+{len(members) - 5} more)" if len(members) > 5 else ""
        print(f"{c:<22} {len(members):>6}  {preview}{more}")
    print(f"\nTotal: {len(all_tickers(args.config))} ticker(s) across {len(cats)} basket(s)")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    b = basket(args.basket, args.config)
    if not b:
        if args.json:
            print_envelope(
                empty_state(
                    errors=[f"basket {args.basket!r} not found"],
                    help=[
                        "Run `market-watchlist list --json` to see available baskets",
                    ],
                )
            )
        else:
            print(f"error: basket {args.basket!r} not found", file=sys.stderr)
        return 1
    if args.json:
        emit_envelope_json(
            {args.basket: b},
            count=len(b),
            help=[
                f"Run `run-watchlist {args.basket} --json` to scan this basket",
            ],
        )
        return 0
    print(f"Basket: {args.basket} ({len(b)} members)")
    print()
    for ticker, meta in b.items():
        bits = []
        if meta.get("tier") is not None:
            bits.append(f"tier={meta['tier']}")
        if meta.get("source"):
            bits.append(f"source={meta['source']}")
        if meta.get("tracking_only"):
            bits.append("tracking-only")
        if meta.get("label"):
            bits.append(f"label={meta['label']!r}")
        print(f"  {ticker:<14} {' '.join(bits)}")
        if meta.get("comment"):
            print(f"      {meta['comment']}")
    return 0


def _cmd_tickers(args: argparse.Namespace) -> int:
    if args.basket:
        out = by_category(args.basket, args.config)
        if not out:
            print(f"error: basket {args.basket!r} not found or empty", file=sys.stderr)
            return 1
    else:
        out = all_tickers(args.config)
    if args.json:
        emit_envelope_json(
            out,
            count=len(out),
            help=[
                "Pipe into `run-watchlist` or `run-all-l2` for the bulk analysis",
            ],
        )
    else:
        for t in out:
            print(t)
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    try:
        result = resolve(args.alias, args.config)
    except ValueError as e:
        if args.json:
            print_envelope(
                empty_state(
                    errors=[str(e)],
                    help=[
                        "Run `market-watchlist tickers --json` for the full ticker list",
                    ],
                )
            )
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    if result is None:
        if args.json:
            print_envelope({"data": {"alias": args.alias, "resolved": None}, "count": 0, "errors": [], "help": []})
        else:
            print(f"(no match for {args.alias!r})")
        return 0
    if args.json:
        meta = metadata_for(result, args.config)
        prov = provider_for(result, args.config)
        emit_envelope_json(
            {"alias": args.alias, "resolved": result, "metadata": meta, "provider": prov},
            count=1,
            help=[f"Run `run-watchlist {prov or 'yf'}:{result} --json` to scan it"],
        )
    else:
        print(result)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    raw = load_raw(args.config)
    errors = validate_storage(raw)
    cats = categories(args.config)
    total = len(all_tickers(args.config))
    if args.json:
        print(json.dumps({"errors": errors, "baskets": cats, "total_tickers": total}))
    else:
        if errors:
            print("VALIDATION ERRORS:")
            for e in errors:
                print(f"  {e}")
            return 1
        print(f"OK — {len(cats)} basket(s), {total} ticker(s)")
    return 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market-watchlist",
        description="Asset registry: named baskets of tickers with metadata.",
    )
    p.add_argument("--config", help="Path to watchlist.json (default: skills/market-watchlist/data/watchlist.json)")
    p.add_argument(
        "--watchlist",
        help="Alias for --config (used by run-watchlist / position-watchdog for consistency)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all baskets with member counts").set_defaults(func=_cmd_list)

    sp = sub.add_parser("show", help="Show full basket config")
    sp.add_argument("basket")
    sp.set_defaults(func=_cmd_show)

    tp = sub.add_parser("tickers", help="Flat list of tickers (optionally one basket)")
    tp.add_argument("basket", nargs="?")
    tp.set_defaults(func=_cmd_tickers)

    rp = sub.add_parser("resolve", help="Resolve bare alias (btc, eth, xle) to canonical ticker")
    rp.add_argument("alias")
    rp.set_defaults(func=_cmd_resolve)

    sub.add_parser("validate", help="Validate the storage file").set_defaults(func=_cmd_validate)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # --watchlist is an alias for --config (used by run-watchlist and position-watchdog
    # for consistency: any tool that points at a market-watchlist file uses --watchlist)
    if getattr(args, "watchlist", None):
        args.config = args.watchlist
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
