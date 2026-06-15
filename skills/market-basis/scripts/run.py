#!/usr/bin/env python3
"""market-basis — perp market structure: funding, basis, spot-perp squeeze/RSI divergence."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from lib.data import fetch_funding_rate, fetch_ohlc
from lib.formatting import emit_json, print_header, safe_round
from lib.indicators import (
    classify_ema_trend,
    classify_squeeze,
    compute_ema,
    compute_rsi,
    compute_squeeze,
    extract_ohlcv,
)


def _analyze_one(data, label):
    if not data or len(data) < 50:
        return None
    o, h, low, c, v = extract_ohlcv(data)
    price = c[-1]
    ema21, _ = compute_ema(c, 21)
    ema50, _ = compute_ema(c, 50)
    rsi = compute_rsi(c, 14)
    sq, mom, sdir = compute_squeeze(c, h, low)
    sig = classify_squeeze(mom, sdir)
    if ema21 and ema50:
        trend, _ = classify_ema_trend(ema21, ema50, price)
    else:
        trend = "NEUTRAL"
    return {
        "price": safe_round(price, 4),
        "ema21": safe_round(ema21, 4),
        "ema50": safe_round(ema50, 4),
        "rsi": safe_round(rsi, 1),
        "squeeze": sig,
        "squeeze_momentum": safe_round(mom, 4),
        "trend": trend,
    }


def analyze(ticker, source="ccxt:binance"):
    result = {"ticker": ticker, "source": source}

    # Extract base exchange from source for perp ticker
    exchange = source.split(":", 1)[1] if ":" in source else "binance"
    base_source = f"ccxt:{exchange}"

    # Determine perp ticker: if ticker has /, append :USDT if not already perp
    if "/" in ticker and ":" not in ticker:
        perp_ticker = f"{ticker}:USDT"
    else:
        perp_ticker = ticker

    # Spot data
    if ":" not in ticker:
        spot_data = fetch_ohlc(ticker, interval="1d", period="6mo", source=base_source)
        spot = _analyze_one(spot_data, "spot")
        if spot:
            result["spot"] = spot
    else:
        spot_data = None
        spot = None

    # Perp data
    perp_data = fetch_ohlc(perp_ticker, interval="1d", period="6mo", source=base_source)
    perp = _analyze_one(perp_data, "perp")
    if perp:
        result["perp"] = perp

    if not spot and not perp:
        return {"ticker": ticker, "error": "no data from provider"}

    # Basis
    if spot and perp:
        basis_abs = perp["price"] - spot["price"]
        basis_pct = ((perp["price"] / spot["price"]) - 1) * 100
        result["basis"] = {
            "absolute": safe_round(basis_abs, 4),
            "percent": safe_round(basis_pct, 4),
        }

    # Funding rate
    funding = fetch_funding_rate(perp_ticker, source=base_source)
    if funding:
        fr = funding.get("funding_rate")
        avg = funding.get("funding_rate_avg_30")
        out = {}
        if fr is not None:
            out["current_rate"] = safe_round(float(fr) * 100, 6)
            out["annualized_apr"] = safe_round(float(fr) * 100 * 3 * 365, 2)
        if avg is not None:
            out["avg_30_rate"] = safe_round(float(avg) * 100, 6)
            out["annualized_apr_avg"] = safe_round(float(avg) * 100 * 3 * 365, 2)
        if out:
            result["funding"] = out

    # Divergence flags
    divergences = []
    if spot and perp:
        s_sig = spot["squeeze"]
        p_sig = perp["squeeze"]
        if s_sig != p_sig and None not in (s_sig, p_sig):
            divergences.append(f"squeeze: spot={s_sig} vs perp={p_sig}")
        s_rsi = spot.get("rsi")
        p_rsi = perp.get("rsi")
        if s_rsi is not None and p_rsi is not None:

            def _zone(r):
                if r < 30:
                    return "oversold"
                if r < 40:
                    return "near_oversold"
                if r <= 60:
                    return "neutral"
                if r <= 70:
                    return "near_overbought"
                return "overbought"

            if _zone(s_rsi) != _zone(p_rsi):
                divergences.append(f"rsi_zone: spot={_zone(s_rsi)} vs perp={_zone(p_rsi)}")
    if divergences:
        result["divergences"] = divergences

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Perpetual swap market structure analysis")
    parser.add_argument("ticker", nargs="?", default="BTC/USDT", help="Ticker (e.g. BTC/USDT)")
    parser.add_argument("--source", default="ccxt:binance", help="CCXT provider and exchange (default: ccxt:binance)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = analyze(args.ticker, source=args.source)

    if args.json:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {args.ticker}: {result['error']}")
        return

    print_header("PERP MARKET STRUCTURE")
    print(f"  {args.ticker}  ({args.source})")
    print()

    funding = result.get("funding", {})
    if funding:
        print("  ── Funding ──")
        cr = funding.get("current_rate")
        aa = funding.get("annualized_apr")
        ar = funding.get("avg_30_rate")
        aa30 = funding.get("annualized_apr_avg")
        if cr is not None:
            print(f"    Current:    {cr:+.6f}% / 8h  ({aa:+.2f}% APR)" if aa else "")
        if ar is not None:
            print(f"    30-avg:     {ar:+.6f}% / 8h  ({aa30:+.2f}% APR)" if aa30 else "")
        print()

    basis = result.get("basis")
    if basis:
        print("  ── Basis ──")
        print(f"    Spot:       {result['spot']['price']}")
        print(f"    Perp:       {result['perp']['price']}")
        print(f"    Diff:       {basis['absolute']:+.4f}  ({basis['percent']:+.4f}%)")
        print()

    for market in ("spot", "perp"):
        d = result.get(market)
        if not d:
            continue
        label = market.upper()
        print(f"  ── {label} ──")
        print(f"    Price:      {d['price']}")
        print(f"    Trend:      {d['trend']}  (EMA21={d['ema21']}, EMA50={d['ema50']})")
        print(f"    RSI(14):    {d['rsi']}")
        print(f"    Squeeze:    {d['squeeze']}  (mom: {d['squeeze_momentum']})")
        print()

    divergences = result.get("divergences")
    if divergences:
        print("  ── Divergences ──")
        for d in divergences:
            print(f"    ⚠ {d}")
        print()


if __name__ == "__main__":
    main()
