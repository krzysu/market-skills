#!/usr/bin/env python3
"""market-trend-analysis — composite trend verdict from multiple L1 skills."""

import importlib.util
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from lib.data import fetch_ohlc
from lib.formatting import emit_json, parse_args, print_header


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_trend_analysis_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ticker, json_mode, source = parse_args(sys.argv[1:], default_ticker="SPY")

    candles = fetch_ohlc(ticker, period="2y", source=source)
    if not candles:
        result = {"ticker": ticker, "error": "no data"}
        if json_mode:
            emit_json(result)
        else:
            print(f"  {ticker}: no data")
        return

    _lib = _load_lib()
    analysis = _lib.analyze(candles)

    if "error" in analysis.get("pattern", {}):
        result = {"ticker": ticker, "error": analysis.get("narrative", "analysis failed")}
        if json_mode:
            emit_json(result)
        else:
            print(f"  {ticker}: {result['error']}")
        return

    output = {
        "skill": "market-trend-analysis",
        "ticker": ticker,
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": source or "auto-detected",
        "interval": "1d",
        "pattern": analysis["pattern"],
        "signals": analysis["signals"],
        "input_scores": analysis["input_scores"],
        "narrative": analysis["narrative"],
    }

    if json_mode:
        emit_json(output)
        return

    pat = output["pattern"]
    sig = output["signals"]
    ins = output["input_scores"]

    print_header("COMPOSITE TREND ANALYSIS")
    print(f"  {ticker}")
    print()
    print(
        f"    Pattern Present:  {'YES' if pat['present'] else 'NO'}  (confidence: {pat['confidence']}/{pat['max_confidence']})"
    )
    print(f"    Classification:   {pat['classification']}")
    print(f"    Narrative:        {output['narrative']}")
    print()

    signal_names = {
        "trend_momentum": "Trend Momentum",
        "rsi_extreme": "RSI Extreme",
        "squeeze_signal": "Squeeze Signal",
        "volume_confirmation": "Volume Confirmation",
    }
    for key, label in signal_names.items():
        s = sig.get(key, {})
        mark = "✓" if s.get("present") else " "
        print(f"    {mark} {label:<25} (weight: {s.get('weight', 0):.0%})")
    print()

    trend = ins.get("market-trend", {})
    rsi = ins.get("market-rsi", {})
    sqz = ins.get("market-squeeze", {})
    vol = ins.get("market-volume", {})

    if trend:
        print(f"    Trend:      {trend.get('signal', 'N/A')}  (score: {trend.get('score', 'N/A')})")
    if rsi:
        print(f"    RSI:        {rsi.get('signal', 'N/A')}  ({rsi.get('rsi_14', 'N/A')})")
    if sqz:
        sqz_on = sqz.get("squeeze_on")
        sqz_label = "[compressing]" if sqz_on else "[released]"
        print(f"    Squeeze:    {sqz.get('signal', 'N/A')}  {sqz_label}")
    if vol:
        print(f"    Volume:     {vol.get('obv_trend', 'N/A')}")
    print()


if __name__ == "__main__":
    main()
