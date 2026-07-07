#!/usr/bin/env python3
"""market-macd — MACD momentum indicator."""

import sys
from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import print_header, require_ticker, safe_parse_args
from analysis.output import (
    emit_envelope_json,
    empty_state,
    parse_axi_flags,
    print_envelope,
    resolve_fields,
)
from analysis.skill_loader import load_lib_for_script

DEFAULT_FIELDS = ["ticker", "macd_line", "signal_line", "histogram", "signal", "score"]


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
        "skill": "market-macd",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "macd_line": result.get("macd_line"),
        "signal_line": result.get("signal_line"),
        "histogram": result.get("histogram"),
        "histogram_direction": result.get("histogram_direction"),
        "histogram_flip": result.get("histogram_flip"),
        "score": result.get("score"),
        "signal": result.get("signal"),
        "zone": result.get("zone"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-rsi {ticker} --json` for momentum confirmation",
        f"Run `market-trend-quality {ticker} --json` for the L2 verdict",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def main():
    fields_arg, full, filtered_argv = parse_axi_flags(sys.argv[1:])
    ticker, json_mode, source, interval, period = safe_parse_args(filtered_argv)
    require_ticker(ticker, json_mode)
    result = analyze(ticker, source=source, interval=interval, period=period)

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
    print_header("MACD MOMENTUM")
    print(f"  {ticker}")
    print()
    print(f"    MACD Line:      {ind.get('macd_line', 'N/A'):>10.4f}")
    print(f"    Signal Line:    {ind.get('signal_line', 'N/A'):>10.4f}")
    print(f"    Histogram:      {ind.get('histogram', 'N/A'):>10.4f}  ({ind.get('histogram_direction', 'N/A')})")
    if ind.get("histogram_flip"):
        print(f"    Histogram Flip: {ind['histogram_flip']}  \u26a0")
    print(f"    Signal:         {ind.get('signal', 'N/A')}  (score: {ind.get('score', 'N/A')})")
    print(f"    Zone:           {ind.get('zone', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
