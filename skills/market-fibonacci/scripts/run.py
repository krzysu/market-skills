#!/usr/bin/env python3
"""market-fibonacci — Fibonacci retracement and extension levels."""

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
    "swing_high",
    "swing_low",
    "current_position",
    "nearest_fib_support",
    "nearest_fib_resistance",
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
        "skill": "market-fibonacci",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "swing_high": result.get("swing_high"),
        "swing_low": result.get("swing_low"),
        "current_position": result.get("current_position"),
        "fib_levels": result.get("fib_levels"),
        "nearest_fib_support": result.get("nearest_fib_support"),
        "nearest_fib_resistance": result.get("nearest_fib_resistance"),
        "nearest_fib_distance_pct": result.get("nearest_fib_distance_pct"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-s-r {ticker} --json` for S/R cluster context",
        f"Run `market-trend-quality {ticker} --json` for the L2 verdict",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def main():
    fields_arg, full, filtered_argv = parse_axi_flags(sys.argv[1:])
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
            fields=fields,
        )
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result
    print_header("FIBONACCI LEVELS")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print()
    print(f"    Swing High:  {ind.get('swing_high', 'N/A'):>10,.2f}")
    print(f"    Swing Low:   {ind.get('swing_low', 'N/A'):>10,.2f}")
    print(f"    Position:    {ind.get('current_position', 'N/A')}")
    print()
    print("    Fibonacci Levels:")
    for key, val in (ind.get("fib_levels") or {}).items():
        marker = ""
        if ind.get("nearest_fib_support") and val == ind["nearest_fib_support"]:
            marker = "  \u2190 support"
        elif ind.get("nearest_fib_resistance") and val == ind["nearest_fib_resistance"]:
            marker = "  \u2190 resistance"
        print(f"      {key:>5}:  {val:>10,.2f}{marker}")
    print()
    if ind.get("nearest_fib_distance_pct") is not None:
        print(f"    Distance to nearest fib level: {ind['nearest_fib_distance_pct']:.2f}%")
    print()


if __name__ == "__main__":
    main()
