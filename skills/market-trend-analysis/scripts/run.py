#!/usr/bin/env python3
"""market-trend-analysis — composite trend verdict from multiple indicators."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from lib.data import fetch_ohlc
from lib.indicators import (
    compute_ema, compute_rsi, compute_squeeze, classify_squeeze,
    compute_obv_trend, detect_crossover, ema_slope_pct, extract_ohlcv,
)
from lib.formatting import emit_json, print_header, parse_args, safe_round


def analyze(ticker, source=None):
    candles = fetch_ohlc(ticker, period="2y", source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    if len(candles) < 220:
        return {"ticker": ticker, "error": f"insufficient data (need 220+ days, got {len(candles)})"}

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]

    # --- EMA Component ---
    ema_21, ema_21_series = compute_ema(closes, 21)
    ema_50, ema_50_series = compute_ema(closes, 50)
    ema_100, ema_100_series = compute_ema(closes, 100)
    ema_200, ema_200_series = compute_ema(closes, 200)

    emas = [ema_21, ema_50, ema_100, ema_200]
    if all(e is not None for e in emas):
        if ema_21 > ema_50 > ema_100 > ema_200:
            ema_signal = "BULLISH"
            ema_score = 3.5
        elif ema_200 > ema_100 > ema_50 > ema_21:
            ema_signal = "BEARISH"
            ema_score = -3.5
        elif ema_21 > ema_50:
            ema_signal = "LEAN_BULLISH"
            ema_score = 2.0
        elif ema_50 > ema_21:
            ema_signal = "LEAN_BEARISH"
            ema_score = -2.0
        else:
            ema_signal = "TANGLED"
            ema_score = 0.0
    else:
        ema_signal = "UNKNOWN"
        ema_score = 0.0

    above_count = sum(1 for e in emas if e is not None and current_price > e)
    slope_21 = ema_slope_pct(ema_21_series)
    crossover = detect_crossover(ema_21_series, ema_50_series, lookback=5)

    ema_component = {
        "signal": ema_signal,
        "alignment": "FULL_BULL" if ema_score == 3.5 else ("FULL_BEAR" if ema_score == -3.5 else ema_signal),
        "price_above_emas": above_count,
        "slope_21_pct": safe_round(slope_21, 3),
        "crossover": crossover,
    }

    # --- RSI Component ---
    rsi = compute_rsi(closes, 14)
    if rsi is not None:
        if rsi < 30:
            rsi_signal = "OVERSOLD"
            rsi_score = 2.5
        elif rsi < 40:
            rsi_signal = "APPROACHING_OVERSOLD"
            rsi_score = 1.25
        elif rsi <= 60:
            rsi_signal = "NEUTRAL"
            rsi_score = 0.0
        elif rsi <= 70:
            rsi_signal = "APPROACHING_OVERBOUGHT"
            rsi_score = -1.25
        else:
            rsi_signal = "OVERBOUGHT"
            rsi_score = -2.5
    else:
        rsi_signal = "UNKNOWN"
        rsi_score = 0.0

    rsi_component = {
        "value": safe_round(rsi),
        "signal": rsi_signal,
    }

    # --- Squeeze Component ---
    squeeze_on, momentum, squee_dir = compute_squeeze(closes, highs, lows)
    squee_signal = classify_squeeze(momentum, squee_dir)

    if squee_signal == "BULLISH":
        squee_score = 2.5
    elif squee_signal == "BULLISH FADING":
        squee_score = 1.0
    elif squee_signal == "BEARISH":
        squee_score = -2.5
    elif squee_signal == "BEARISH FADING":
        squee_score = -1.0
    else:
        squee_score = 0.0

    squeeze_component = {
        "squeeze_on": squeeze_on,
        "momentum": safe_round(momentum, 4) if momentum else None,
        "direction": squee_dir,
        "signal": squee_signal,
    }

    # --- Volume Component ---
    obv_trend_val = compute_obv_trend(closes, volumes)

    if obv_trend_val == "rising":
        vol_signal = "CONFIRMING"
        vol_score = 1.5
    elif obv_trend_val == "falling":
        vol_signal = "DIVERGING"
        vol_score = -1.5
    else:
        vol_signal = "UNKNOWN"
        vol_score = 0.0

    volume_component = {
        "obv_trend": obv_trend_val,
        "signal": vol_signal,
    }

    # --- Composite Scoring ---
    weights = {"ema": 0.35, "rsi": 0.25, "squeeze": 0.25, "volume": 0.15}
    raw_score = (
        ema_score * weights["ema"]
        + rsi_score * weights["rsi"]
        + squee_score * weights["squeeze"]
        + vol_score * weights["volume"]
    )

    # Max achievable |raw_score| ~2.7; map thresholds accordingly.
    if raw_score >= 2.0:
        direction = "BULLISH"
        conviction = "HIGH"
    elif raw_score >= 1.0:
        direction = "BULLISH"
        conviction = "MEDIUM"
    elif raw_score >= 0.3:
        direction = "BULLISH"
        conviction = "LOW"
    elif raw_score > -0.3:
        direction = "NEUTRAL"
        conviction = "LOW"
    elif raw_score > -1.0:
        direction = "BEARISH"
        conviction = "LOW"
    elif raw_score > -2.0:
        direction = "BEARISH"
        conviction = "MEDIUM"
    else:
        direction = "BEARISH"
        conviction = "HIGH"

    # Conflict detection
    conflicts = []
    component_signals = [
        ("ema", ema_signal),
        ("rsi", rsi_signal),
        ("squeeze", squee_signal),
        ("volume", vol_signal),
    ]

    bullish_count = sum(1 for _, s in component_signals if s in ("BULLISH", "LEAN_BULLISH", "OVERSOLD", "APPROACHING_OVERSOLD", "CONFIRMING"))
    bearish_count = sum(1 for _, s in component_signals if s in ("BEARISH", "LEAN_BEARISH", "OVERBOUGHT", "APPROACHING_OVERBOUGHT", "DIVERGING"))

    if bullish_count >= 1 and bearish_count >= 1:
        bullish_parts = [n for n, s in component_signals if s in ("BULLISH", "LEAN_BULLISH", "OVERSOLD", "APPROACHING_OVERSOLD", "CONFIRMING")]
        bearish_parts = [n for n, s in component_signals if s in ("BEARISH", "LEAN_BEARISH", "OVERBOUGHT", "APPROACHING_OVERBOUGHT", "DIVERGING")]
        conflicts.append(f"Mixed signals: {', '.join(bullish_parts)} bullish vs {', '.join(bearish_parts)} bearish")

    return {
        "ticker": ticker,
        "price": safe_round(current_price, 2),
        "components": {
            "ema": ema_component,
            "rsi": rsi_component,
            "squeeze": squeeze_component,
            "volume": volume_component,
        },
        "verdict": {
            "direction": direction,
            "conviction": conviction,
            "raw_score": safe_round(raw_score, 2),
        },
        "conflicts": conflicts,
    }


def main():
    ticker, json_mode, source = parse_args(sys.argv[1:], default_ticker="SPY")
    result = analyze(ticker, source=source)

    if json_mode:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    v = result["verdict"]
    c = result["components"]

    print_header("COMPOSITE TREND ANALYSIS")
    print(f"  {ticker}  (price: {result['price']:,.2f})")
    print()
    print(f"  Verdict:    {v['direction']} (conviction: {v['conviction']}, score: {v['raw_score']})")
    print()
    print(f"  Components:")
    print(f"    EMA:      {c['ema']['signal']} (above {c['ema']['price_above_emas']}/4 EMAs)")
    print(f"    RSI:      {c['rsi']['signal']} ({c['rsi']['value']})")
    print(f"    Squeeze:  {c['squeeze']['signal']} {'[compressing]' if c['squeeze']['squeeze_on'] else '[released]'}")
    print(f"    Volume:   {c['volume']['signal']} (OBV {c['volume']['obv_trend']})")
    if result["conflicts"]:
        print()
        for conflict in result["conflicts"]:
            print(f"  ⚠ {conflict}")
    print()


if __name__ == "__main__":
    main()
