#!/usr/bin/env python3
"""market-overview — unified market scan across multiple tickers."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from analysis.data import fetch_ohlc
from analysis.formatting import print_header, safe_round
from analysis.indicators import (
    classify_ema_trend,
    classify_squeeze,
    compute_ema,
    compute_obv_trend,
    compute_rsi,
    compute_squeeze,
    extract_ohlcv,
)
from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    empty_state,
    maybe_render_home_view,
    parse_axi_flags,
    print_envelope,
    project_fields,
    resolve_fields,
)

DEFAULT_WATCHLIST = ["SPY", "QQQ", "AAPL", "GOOGL", "BTC-USD", "GLD"]


def _analyze_one(ticker, source=None, interval=DEFAULT_INTERVAL, period=DEFAULT_PERIOD):
    """Run composite trend analysis on a single ticker, return dict or error."""
    try:
        candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
        if not candles:
            return {"ticker": ticker, "error": "no data"}
        if len(candles) < 220:
            return {"ticker": ticker, "error": f"insufficient data ({len(candles)} candles on {interval})"}

        opens, highs, lows, closes, volumes = extract_ohlcv(candles)
        price = closes[-1]

        # EMA
        ema_21, ema_21_series = compute_ema(closes, 21)
        ema_50, ema_50_series = compute_ema(closes, 50)
        trend, trend_score = classify_ema_trend(ema_21, ema_50, price)

        # RSI
        rsi = compute_rsi(closes, 14)
        if rsi is not None:
            if rsi < 30:
                rsi_score = 2
            elif rsi < 40:
                rsi_score = 1
            elif rsi <= 60:
                rsi_score = 0
            elif rsi <= 70:
                rsi_score = -1
            else:
                rsi_score = -2
        else:
            rsi_score = 0

        # Squeeze
        squeeze_on, momentum, squee_dir = compute_squeeze(closes, highs, lows)
        squee_signal = classify_squeeze(momentum, squee_dir)
        squee_map = {"BULLISH": 2, "BULLISH FADING": 1, "BEARISH": -2, "BEARISH FADING": -1, "FLAT": 0, "UNKNOWN": 0}
        squee_score = squee_map.get(squee_signal, 0)

        # Volume
        obv_trend_val = compute_obv_trend(closes, volumes)
        vol_score = 1 if obv_trend_val == "rising" else (-1 if obv_trend_val == "falling" else 0)

        # Unified score: normalize raw component scores to 0-100
        raw = trend_score * 35 + rsi_score * 25 + squee_score * 25 + vol_score * 15
        max_raw = 2 * 35 + 2 * 25 + 2 * 25 + 1 * 15  # = 185
        min_raw = -2 * 35 + -2 * 25 + -2 * 25 + -1 * 15  # = -185
        unified = ((raw - min_raw) / (max_raw - min_raw)) * 100

        if unified >= 75:
            action = "STRONG_BUY"
        elif unified >= 55:
            action = "BUY"
        elif unified >= 35:
            action = "WATCH"
        else:
            action = "AVOID"

        return {
            "ticker": ticker,
            "price": safe_round(price, 2),
            "trend": trend,
            "rsi": safe_round(rsi),
            "squeeze": squee_signal,
            "unified_score": safe_round(unified),
            "action": action,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def scan(tickers, action_filter=None, top_n=None, source=None, interval=DEFAULT_INTERVAL, period=DEFAULT_PERIOD):
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_analyze_one, t, source, interval, period): t for t in tickers}
        for future in as_completed(futures):
            r = future.result()
            if "error" in r:
                errors.append(r)
            else:
                results.append(r)

    results.sort(key=lambda x: x["unified_score"] or 0, reverse=True)

    if action_filter:
        results = [r for r in results if r["action"] == action_filter]

    if top_n:
        results = results[:top_n]

    return results, errors


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Unified market overview scan")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols (default: watchlist)")
    parser.add_argument("--action", help="Filter by action: STRONG_BUY, BUY, WATCH, AVOID")
    parser.add_argument("--top", type=int, help="Limit to top N results")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--source", help="Data provider: kraken, yfinance (default: auto)")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Candle interval (default: 1d)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Candle period (default: 1y)")
    parser.add_argument("--pretty", action="store_true", help="Human-readable table (default without --json)")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        if maybe_render_home_view(__file__, None, args.json):
            return

    fields_arg, full, toon, _ = parse_axi_flags(sys.argv[1:])

    validate_timeframe(args.interval, args.period)

    tickers = args.tickers if args.tickers else DEFAULT_WATCHLIST
    results, errors = scan(
        tickers,
        action_filter=args.action,
        top_n=args.top,
        source=args.source,
        interval=args.interval,
        period=args.period,
    )

    output = {
        "tickers_scanned": len(tickers),
        "interval": args.interval,
        "period": args.period,
        "results": len(results),
        "errors": errors,
        "ranked": results,
    }
    del output

    if args.json:
        if not results and errors:
            print_envelope(
                empty_state(
                    errors=[e.get("error", "unknown") for e in errors],
                    help=[
                        "Try a longer --period",
                        "Pass --source to force a provider",
                        "Pass --full for the full payload or --fields=<csv> to project",
                    ],
                )
            )
            return
        fields = resolve_fields(
            fields_arg,
            full=full,
            default=["ticker", "price", "unified_score", "action"],
        )
        projected_results = [project_fields(r, fields) for r in results]
        out = {
            "tickers_scanned": len(tickers),
            "interval": args.interval,
            "period": args.period,
            "results": len(projected_results),
            "errors": errors,
            "ranked": projected_results,
            "summary": f"{len(tickers)} tickers scanned, {len(projected_results)} matched",
        }
        cache_run_result(__file__, out)
        emit_envelope_json(
            out,
            count=len(projected_results),
            help=[
                f"Pass --action={results[0]['action'] if results else 'BUY'} to filter the panel",
                "Pass --top=N to limit the panel size",
                "Pass --full for the full payload or --fields=<csv> to project",
            ],
            errors=[e.get("error", "unknown") for e in errors],
            toon=toon,
        )
        return

    print_header("UNIFIED MARKET OVERVIEW")
    print(f"  interval={args.interval} period={args.period}")
    if results:
        print(f"  {'Ticker':<10} {'Price':>10} {'Trend':<16} {'RSI':>6} {'Squeeze':<16} {'Score':>6} {'Action'}")
        print(f"  {'-' * 10} {'-' * 10} {'-' * 16} {'-' * 6} {'-' * 16} {'-' * 6} {'-' * 10}")
        for r in results:
            print(
                f"  {r['ticker']:<10} {r['price']:>10,.2f} {r['trend']:<16} {r['rsi']:>6} {r['squeeze']:<16} {r['unified_score']:>6.0f} {r['action']}"
            )
    else:
        print("  No tickers matched the filter.")

    if errors:
        print()
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    {e['ticker']}: {e['error']}")
    print()


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
        sys.exit(2)
