#!/usr/bin/env python3
"""recipe-scanner — multi-ticker momentum/breakout sweep."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.data import fetch_ohlc
from lib.indicators import (
    compute_ema, compute_rsi, compute_squeeze, classify_squeeze,
    classify_ema_trend, compute_obv_trend, extract_ohlcv,
)
from lib.formatting import emit_json, print_header, safe_round

DEFAULT_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "BTC-USD", "GLD", "SLV", "XLE", "XLF"]


def _scan_one(ticker, source=None):
    """Run trend + momentum analysis on a single ticker."""
    try:
        candles = fetch_ohlc(ticker, period="2y", source=source)
        if not candles:
            return {"ticker": ticker, "error": "no data"}
        if len(candles) < 60:
            return {"ticker": ticker, "error": f"insufficient data ({len(candles)} days)"}

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
                rsi_zone = "oversold"
            elif rsi < 40:
                rsi_score = 1
                rsi_zone = "near oversold"
            elif rsi <= 60:
                rsi_score = 0
                rsi_zone = "neutral"
            elif rsi <= 70:
                rsi_score = -1
                rsi_zone = "near overbought"
            else:
                rsi_score = -2
                rsi_zone = "overbought"
        else:
            rsi_score = 0
            rsi_zone = "unknown"

        # Squeeze
        squeeze_on, momentum, squee_dir = compute_squeeze(closes, highs, lows)
        squee_signal = classify_squeeze(momentum, squee_dir)
        squee_map = {"BULLISH": 2, "BULLISH FADING": 1, "BEARISH": -2, "BEARISH FADING": -1, "FLAT": 0, "UNKNOWN": 0}
        squee_score = squee_map.get(squee_signal, 0)

        # Volume
        obv_trend_val = compute_obv_trend(closes, volumes)
        vol_score = 1 if obv_trend_val == "rising" else (-1 if obv_trend_val == "falling" else 0)

        raw = trend_score * 35 + rsi_score * 25 + squee_score * 25 + vol_score * 15
        max_raw = 185
        min_raw = -185
        unified = ((raw - min_raw) / (max_raw - min_raw)) * 100

        if unified >= 75:
            action = "STRONG_BUY"
        elif unified >= 55:
            action = "BUY"
        elif unified >= 35:
            action = "WATCH"
        else:
            action = "AVOID"

        # Build rationale
        parts = []
        if trend != "UNKNOWN":
            parts.append(f"trend {trend.lower()}")
        if rsi is not None:
            parts.append(f"RSI {rsi_zone} ({safe_round(rsi)})")
        if squee_signal not in ("FLAT", "UNKNOWN"):
            parts.append(f"squeeze {squee_signal.lower()}")
        if obv_trend_val:
            parts.append(f"OBV {obv_trend_val}")

        rationale = "; ".join(parts) if parts else "insufficient signal"

        return {
            "ticker": ticker,
            "price": safe_round(price, 2),
            "trend": trend,
            "rsi": safe_round(rsi),
            "squeeze": squee_signal,
            "squeeze_on": squeeze_on,
            "unified_score": safe_round(unified),
            "action": action,
            "rationale": rationale,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def scan(tickers, action_filter=None, top_n=None, source=None):
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_scan_one, t, source): t for t in tickers}
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
    parser = argparse.ArgumentParser(description="Multi-ticker momentum scanner")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols (default: watchlist)")
    parser.add_argument("--action", help="Filter: STRONG_BUY, BUY, WATCH, AVOID")
    parser.add_argument("--top", type=int, help="Limit to top N results")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--source", help="Data provider: kraken, yfinance (default: auto)")
    args = parser.parse_args()

    tickers = args.tickers if args.tickers else DEFAULT_WATCHLIST
    matches, errors = scan(tickers, action_filter=args.action, top_n=args.top, source=args.source)

    output = {
        "scanned": len(tickers),
        "matches": len(matches),
        "errors": errors,
        "results": matches,
    }

    if args.json:
        emit_json(output)
        return

    print_header("MOMENTUM SCANNER")
    if matches:
        print(f"  {'Ticker':<10} {'Price':>10} {'Trend':<16} {'RSI':>6} {'Squeeze':<16} {'Score':>6} {'Action':<12} Rationale")
        print(f"  {'-'*10} {'-'*10} {'-'*16} {'-'*6} {'-'*16} {'-'*6} {'-'*12} {'-'*30}")
        for m in matches:
            print(f"  {m['ticker']:<10} {m['price']:>10,.2f} {m['trend']:<16} {m['rsi']:>6} {m['squeeze']:<16} {m['unified_score']:>6.0f} {m['action']:<12} {m['rationale']}")
    else:
        print("  No tickers matched the filter.")

    if errors:
        print()
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    {e['ticker']}: {e['error']}")
    print()


if __name__ == "__main__":
    main()
