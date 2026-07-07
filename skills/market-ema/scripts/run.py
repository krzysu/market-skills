#!/usr/bin/env python3
"""market-ema — EMA filter and trend structure analysis."""

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

DEFAULT_FIELDS = ["ticker", "alignment", "signal", "score"]


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
        "skill": "market-ema",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "ema_21": result.get("ema_21"),
        "ema_50": result.get("ema_50"),
        "ema_100": result.get("ema_100"),
        "ema_200": result.get("ema_200"),
        "alignment": result.get("alignment"),
        "price_above_emas": result.get("price_above_emas"),
        "slope_21_pct": result.get("slope_21_pct"),
        "slope_50_pct": result.get("slope_50_pct"),
        "crossover": result.get("crossover"),
        "score": result.get("score"),
        "signal": result.get("signal"),
        "zone": result.get("zone"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-trend {ticker} --json` for the L1 trend score",
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
    print_header("EMA TREND STRUCTURE")
    print(f"  {ticker}  (price: {ind.get('current_price', 0):,.2f})")
    print()
    labels = [
        ("EMA 21", ind.get("ema_21")),
        ("EMA 50", ind.get("ema_50")),
        ("EMA 100", ind.get("ema_100")),
        ("EMA 200", ind.get("ema_200")),
    ]
    for label, val in labels:
        if val:
            pos = "\u25b2" if ind.get("current_price", 0) > val else "\u25bc"
            pct = (ind.get("current_price", 0) - val) / val * 100
            print(f"    {label}:  {val:,.2f}  ({pct:+.1f}%) {pos}")
    print()
    print(f"    Alignment:  {ind.get('alignment', 'N/A')} (price above {ind.get('price_above_emas', 0)}/4 EMAs)")
    if ind.get("slope_21_pct") is not None:
        print(f"    Slope 21:   {ind['slope_21_pct']:+.3f}%/5d")
    if ind.get("slope_50_pct") is not None:
        print(f"    Slope 50:   {ind['slope_50_pct']:+.3f}%/5d")
    if ind.get("crossover"):
        note = "bullish reversal" if ind["crossover"] == "golden_cross" else "bearish reversal"
        print(f"    Crossover:  {ind['crossover']} \u2014 {note}")
    print(f"    Signal:     {ind.get('signal', 'N/A')} (score: {ind.get('score', 'N/A')})")
    print()


if __name__ == "__main__":
    main()
