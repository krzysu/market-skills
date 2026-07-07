#!/usr/bin/env python3
"""market-s-r — Support and Resistance from swing point clustering."""

import sys
from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import print_header, safe_parse_args
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

DEFAULT_FIELDS = [
    "ticker",
    "nearest_support",
    "nearest_resistance",
    "sits_on_level",
    "support_count",
    "resistance_count",
]


def analyze(ticker, *, source=None, interval="1d", period="1y"):
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = load_lib_for_script(__file__)
    result = _lib.analyze(candles, interval=interval, period=period)
    if "error" in result:
        return {"ticker": ticker, **result}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    return {
        "skill": "market-s-r",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "nearest_support": result.get("nearest_support"),
        "nearest_resistance": result.get("nearest_resistance"),
        "support_distance_pct": result.get("support_distance_pct"),
        "resistance_distance_pct": result.get("resistance_distance_pct"),
        "support_touches": result.get("support_touches"),
        "resistance_touches": result.get("resistance_touches"),
        "support_count": result.get("support_count"),
        "resistance_count": result.get("resistance_count"),
        "clustered_levels": result.get("clustered_levels"),
        "sits_on_level": result.get("sits_on_level"),
        "no_nearby_level": result.get("no_nearby_level"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-fibonacci {ticker} --json` for retracement levels",
        f"Run `market-accumulation {ticker} --json` for Wyckoff context",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def main():
    fields_arg, full, toon, filtered_argv = parse_axi_flags(sys.argv[1:])
    ticker, json_mode, source, interval, period = safe_parse_args(filtered_argv)
    if maybe_render_home_view(__file__, ticker, json_mode):
        return
    result = analyze(ticker, source=source, interval=interval, period=period)
    cache_run_result(__file__, result)

    if json_mode:
        if "error" in result:
            print_envelope(empty_state(errors=[result["error"]], help=_help_lines(ticker or "TICKER")))
            return
        fields = resolve_fields(fields_arg, full=full, default=DEFAULT_FIELDS)
        emit_envelope_json(
            result,
            count=1,
            help=_help_lines(ticker),
            fields=fields, toon=toon,)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result
    print_header("SUPPORT & RESISTANCE")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print()
    if ind.get("nearest_resistance") is not None:
        r_dist = ind.get("resistance_distance_pct", 0)
        r_touches = ind.get("resistance_touches", 0)
        print(f"    Resistance: {ind['nearest_resistance']:>10,.2f}  ({r_dist:+.2f}%, {r_touches} touches)")
    else:
        print("    Resistance: None")
    if ind.get("nearest_support") is not None:
        s_dist = ind.get("support_distance_pct", 0)
        s_touches = ind.get("support_touches", 0)
        print(f"    Support:    {ind['nearest_support']:>10,.2f}  ({s_dist:+.2f}%, {s_touches} touches)")
    else:
        print("    Support:    None")
    print()
    print(f"    Support count:  {ind.get('support_count', 0)}")
    print(f"    Resistance count: {ind.get('resistance_count', 0)}")
    if ind.get("sits_on_level"):
        print("    \u26a0 Price sits on a detected level")
    print()


if __name__ == "__main__":
    main()
