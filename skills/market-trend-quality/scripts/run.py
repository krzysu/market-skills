#!/usr/bin/env python3
"""market-trend-quality — L2 pattern detection: assesses trend health quality."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    """Load lib.py from the skill directory (handles hyphens in path)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_trend_quality_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from datetime import UTC, datetime

from lib.data import fetch_ohlc
from lib.formatting import emit_json, parse_args, print_header


def analyze(ticker, source=None):
    candles = fetch_ohlc(ticker, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = _load_lib()
    result = _lib.analyze(candles)
    if "error" in result.get("pattern", {}) or (
        not result.get("input_scores") and "insufficient" in result.get("narrative", "")
    ):
        if "insufficient" in result.get("narrative", ""):
            return {"ticker": ticker, "error": result["narrative"]}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    return {
        "skill": "market-trend-quality",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": "1d",
        "pattern": result["pattern"],
        "signals": result["signals"],
        "input_scores": result["input_scores"],
        "narrative": result["narrative"],
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

    pat = result["pattern"]
    sigs = result["signals"]
    print_header(f"TREND QUALITY PATTERN — {ticker}")
    print(f"  {ticker}")
    print()
    print(
        f"    Pattern Present:  {'YES' if pat['present'] else 'NO'}  (confidence: {pat['confidence']}/{pat['max_confidence']})"
    )
    if pat["classification"]:
        print(f"    Classification:   {pat['classification']}")
    print(f"    Type:             {pat['type']}")
    print()
    print("  Sub-Signals:")
    for name, sig in sigs.items():
        check = "\u2713" if sig["present"] else "\u2717"
        print(f"    {check} {name:<30s}  (w={sig['weight']})")
    print()
    print(f"  {result['narrative']}")
    print()


if __name__ == "__main__":
    main()
