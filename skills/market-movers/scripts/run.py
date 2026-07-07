#!/usr/bin/env python3
"""market-movers — fetch CoinGecko gainers/losers/trending/categories with retry/backoff.

Pulls four panels for the morning brief:

  - gainers:    top 7  by 24h % change (CoinGecko /coins/markets desc)
  - losers:     top 7  by 24h % change (asc)
  - trending:   top 7  from /search/trending (separate quota tier)
  - categories: top N from /coins/categories ordered by 24h market-cap change

Optional Kraken tradability cross-reference (``tradable_on`` per entry)
runs once per process via ``kraken pairs -o json`` with an in-process
TTL cache. Disable with ``--no-tradable-filter`` (CI / log-only jobs).

On final 429 from the gainers/losers endpoint, the panel is dropped and
the payload carries an explicit
``[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]``
marker for the brief to surface. Same degrade shape for the categories
panel. Trending is preserved when only gainers/losers rate-limit.

If ``tradable_on`` is enabled and the ``kraken`` CLI is absent, every
entry's ``tradable_on`` field is ``null`` and the payload's ``note``
carries the explicit
``[MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]``
marker.

Each 429 incident writes one line to
``$XDG_DATA_HOME/market-skills/coingecko-rate-limit.log`` so repeated
throttling is auditable without scraping stdout.

Usage:
    # JSON to stdout (LLM tool-use + morning brief)
    uv run skills/market-movers/scripts/run.py --json

    # Human-readable summary
    uv run skills/market-movers/scripts/run.py

    # Tune retry / top-N
    uv run skills/market-movers/scripts/run.py --top-n=10 --retries=5 --json

    # Disable Kraken tradability cross-reference (CI without `kraken` CLI)
    uv run skills/market-movers/scripts/run.py --no-tradable-filter --json
"""

import argparse
import json
import sys

from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    maybe_render_home_view,
    parse_axi_flags,
    resolve_fields,
)
from analysis.skill_loader import load_lib_for_script


def _emit_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _format_pct(value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _format_price(value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.6f}"


def _format_tradable(entry: dict) -> str:
    val = entry.get("tradable_on", _SENTINEL_UNSET)
    if val is _SENTINEL_UNSET:
        return ""
    if val is None:
        return "(kraken ?)"
    if isinstance(val, dict):
        if val.get("kraken") is True:
            alt = val.get("altname") or "?"
            return f"(kraken ✓ {alt})"
        return "(kraken ✗)"
    return ""


_SENTINEL_UNSET = object()


def _print_panel(title: str, emoji: str, entries: list[dict], *, show_tradable: bool) -> None:
    print(f"  {emoji} {title}:")
    if not entries:
        print("    (empty)")
        return
    sym_w = max(len(str(e.get("symbol") or "?")) for e in entries)
    for e in entries:
        sym = (e.get("symbol") or "?")[:8]
        pct = _format_pct(e.get("pct_24h"))
        price = _format_price(e.get("price_usd"))
        rank = e.get("market_cap_rank")
        rank_s = f"#{rank}" if rank else ""
        tradable_s = _format_tradable(e) if show_tradable else ""
        line = f"    {sym:<{sym_w}}  {pct:>10}  {price:>14}  {rank_s}"
        if tradable_s:
            line = f"{line}  {tradable_s}"
        print(line)


def _print_categories(entries: list[dict]) -> None:
    print("  🗂  CATEGORIES (24h rotation):")
    if not entries:
        print("    (empty)")
        return
    name_w = max(len(str(e.get("name") or "?")) for e in entries)
    for e in entries:
        name = (e.get("name") or "?")[:32]
        pct = _format_pct(e.get("pct_24h"))
        mcap = e.get("market_cap_usd")
        mcap_s = f"${mcap / 1e9:,.1f}B" if isinstance(mcap, (int, float)) else "—"
        top3 = e.get("top_3_coins_id") or []
        top3_s = ",".join(top3[:3]) if top3 else "—"
        print(f"    {name:<{name_w}}  {pct:>10}  {mcap_s:>10}  top: {top3_s}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="market-movers",
        description="CoinGecko gainers/losers/trending/categories with optional Kraken tradability",
    )
    parser.add_argument("--top-n", type=int, default=7, help="Entries per coin panel (default 7)")
    parser.add_argument(
        "--categories-top-n",
        type=int,
        default=10,
        help="Categories panel size (default 10)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries per panel before rate-limit escalation (default 3)",
    )
    parser.add_argument(
        "--no-tradable-filter",
        action="store_true",
        help="Skip Kraken tradability cross-reference (e.g. CI without `kraken` CLI)",
    )
    parser.add_argument(
        "--kraken-pairs-ttl-s",
        type=int,
        default=600,
        help="In-process TTL for the Kraken AssetPairs cache (default 600s)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        if maybe_render_home_view(__file__, None, args.json):
            return 0

    fields_arg, full, _ = parse_axi_flags(sys.argv[1:])

    lib = load_lib_for_script(__file__)
    payload = lib.fetch_movers(
        top_n=args.top_n,
        retries=args.retries,
        tradable_filter=not args.no_tradable_filter,
        categories_top_n=args.categories_top_n,
        kraken_pairs_ttl_s=float(args.kraken_pairs_ttl_s),
    )

    if args.json:
        panel_count = sum(1 for k in ("gainers", "losers", "trending", "categories") if payload.get(k))
        fields = resolve_fields(
            fields_arg,
            full=full,
            default=["gainers", "losers", "trending", "fetched_at"],
        )
        payload = {**payload, "summary": f"{panel_count} panels fetched"}
        cache_run_result(__file__, payload)
        emit_envelope_json(
            payload,
            count=panel_count,
            help=[
                "Pass --top-n=N to cap each panel",
                "Pass --full for the full payload or --fields=<csv> to project",
            ],
            fields=fields,
        )
        return 0

    print("MARKET MOVERS")
    print(f"  fetched_at: {payload['fetched_at']}")
    if payload.get("note"):
        print(f"  {payload['note']}")
    print()
    show_tradable = bool(payload.get("tradable_filter"))
    _print_panel("GAINERS (24h)", "📈", payload["gainers"], show_tradable=show_tradable)
    print()
    _print_panel("LOSERS (24h)", "📉", payload["losers"], show_tradable=show_tradable)
    print()
    _print_panel("TRENDING", "🔥", payload["trending"], show_tradable=show_tradable)
    print()
    _print_categories(payload.get("categories", []))
    if payload.get("rate_limited"):
        print()
        print(f"  attempts: {payload['attempts']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
