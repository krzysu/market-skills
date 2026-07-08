#!/usr/bin/env python3
"""market-rsi — RSI momentum oscillator."""

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
    truncate,
)
from analysis.skill_loader import load_lib_for_script

DEFAULT_FIELDS = ["ticker", "rsi_14", "signal", "score"]
NARRATIVE_LIMIT = 80


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
        "skill": "market-rsi",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "rsi_14": result.get("rsi_14"),
        "rsi_7d_ago": result.get("rsi_7d_ago"),
        "rsi_delta_7d": result.get("rsi_delta_7d"),
        "signal": result.get("signal"),
        "score": result.get("score"),
        "zone": result.get("zone"),
        "trend": result.get("trend"),
        "summary": _summary_line(ticker, result),
    }


def _summary_line(ticker: str, result: dict) -> str:
    rsi = result.get("rsi_14")
    signal = result.get("signal") or "N/A"
    if rsi is None:
        return f"{ticker} rsi=NA {signal}"
    return f"{ticker} rsi={rsi:g} {signal}"


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-ema {ticker} --json` for trend context",
        f"Run `market-trend-quality {ticker} --json` for the L2 verdict",
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

    rsi = result.get("rsi_14", 50) or 50
    bar_pos = max(0, min(40, round(rsi / 100 * 40)))
    bar = "\u2591" * bar_pos + "\u2588" + "\u2591" * (40 - bar_pos)
    os_marker = " " * 12 + "\u219130"
    ob_marker = " " * 28 + "\u219170"

    print_header("RSI MOMENTUM")
    print(f"  {ticker}  (price: {result.get('current_price', 0):,.2f})")
    print(f"    RSI(14):   {result.get('rsi_14', 'N/A')}")
    if result.get("rsi_delta_7d") is not None:
        print(f"    7d change: {result['rsi_delta_7d']:+.2f} ({result.get('trend', 'N/A')})")
    print(f"    Position:  [{bar}]")
    print(f"               {os_marker}    {ob_marker}")
    print(f"    Signal:    {result.get('signal', 'N/A')}")
    print()
    print(f"  {truncate(_summary_line(ticker, result), limit=NARRATIVE_LIMIT)}")
    print()


if __name__ == "__main__":
    main()
