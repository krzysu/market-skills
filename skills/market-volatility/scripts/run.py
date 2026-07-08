#!/usr/bin/env python3
"""market-volatility — Realized volatility analysis, percentile rank, regime."""

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

DEFAULT_FIELDS = ["ticker", "realized_vol_30d", "percentile_rank_30d", "regime"]


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
        "skill": "market-volatility",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "realized_vol_7d": result.get("realized_vol_7d"),
        "realized_vol_30d": result.get("realized_vol_30d"),
        "percentile_rank_30d": result.get("percentile_rank_30d"),
        "regime": result.get("regime"),
        "trend": result.get("trend"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-squeeze {ticker} --json` for squeeze + momentum context",
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
            fields=fields,
            toon=toon,
        )
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result
    print_header("VOLATILITY ANALYSIS")
    print(f"  {ticker}")
    print()
    print(f"    Realized Vol 7d:   {ind.get('realized_vol_7d', 'N/A'):>8}%")
    print(f"    Realized Vol 30d:  {ind.get('realized_vol_30d', 'N/A'):>8}%")
    print(f"    Percentile Rank:   {ind.get('percentile_rank_30d', 'N/A'):>8}")
    print(f"    Regime:            {ind.get('regime', 'N/A'):>8}")
    print(f"    Trend:             {ind.get('trend', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
