#!/usr/bin/env python3
"""market-volume — Volume analysis: ratio, OBV trend, regime classification."""

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

DEFAULT_FIELDS = ["ticker", "volume_ratio", "obv_trend", "regime"]


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
        "skill": "market-volume",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "candles_used": len(candles),
        "current_price": result.get("current_price"),
        "current_volume": result.get("current_volume"),
        "sma_volume_20": result.get("sma_volume_20"),
        "volume_ratio": result.get("volume_ratio"),
        "obv_trend": result.get("obv_trend"),
        "obv_divergence": result.get("obv_divergence"),
        "regime": result.get("regime"),
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `market-breakout {ticker} --json` to see volume-confirmed breakouts",
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
    print_header("VOLUME ANALYSIS")
    print(f"  {ticker}  (price: {ind.get('current_price', 'N/A'):>10,.2f})")
    print()
    print(
        f"    Volume:         {ind.get('current_volume', 'N/A'):>12,.0f}  (SMA20: {ind.get('sma_volume_20', 'N/A'):>10,.0f})"
    )
    print(f"    Volume Ratio:   {ind.get('volume_ratio', 'N/A'):>10.2f}x  ({ind.get('regime', 'N/A')})")
    print(f"    OBV Trend:      {ind.get('obv_trend', 'N/A')}")
    if ind.get("obv_divergence"):
        print(f"    OBV Divergence: {ind['obv_divergence']}  \u26a0")
    print()


if __name__ == "__main__":
    main()
