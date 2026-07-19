#!/usr/bin/env python3
"""market-squeeze — Bollinger Band / Keltner Channel squeeze momentum."""

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

DEFAULT_FIELDS = ["ticker", "squeeze_on", "momentum", "direction", "signal"]


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
        "skill": "market-squeeze",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "squeeze_on": result.get("squeeze_on"),
        "momentum": result.get("momentum"),
        "direction": result.get("direction"),
        "signal": result.get("signal"),
        "histogram_recent": result.get("histogram_recent"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-volatility {ticker} --json` for realized vol context",
        f"Run `market-breakout {ticker} --json` for breakout confirmation",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def main():
    fields_arg, full, toon, from_state, ttl, filtered_argv = parse_axi_flags(sys.argv[1:])
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
            toon=toon,
        )
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result
    print_header("SQUEEZE MOMENTUM")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print(f"    Squeeze:    {'ON \u2014 compression' if ind.get('squeeze_on') else 'OFF \u2014 released'}")
    print(f"    Momentum:   {ind.get('momentum', 'N/A')} ({ind.get('direction', 'N/A')})")
    print(f"    Signal:     {ind.get('signal', 'N/A')}")
    print()
    print("    Recent momentum:")
    for i, v in enumerate(ind.get("histogram_recent", [])):
        bar = "\u2588" if (v or 0) > 0 else "\u2581"
        print(f"      [{i:2d}] {bar} {v}")
    print()


if __name__ == "__main__":
    main()
