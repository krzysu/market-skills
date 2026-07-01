#!/usr/bin/env python3
"""market-liquidity-sweep — L2 pattern detection: detects liquidity sweeps and fakeouts."""

import sys
from datetime import UTC, datetime  # noqa: E402

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, print_header, require_ticker, safe_parse_args
from analysis.skill_loader import load_lib_for_script


def analyze(ticker, *, source=None, interval="1d", period="1y"):

    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = load_lib_for_script(__file__)
    result = _lib.analyze(candles, interval=interval, period=period)
    if "error" in result.get("pattern", {}) or (
        not result.get("input_scores") and "insufficient" in result.get("narrative", "")
    ):
        if "insufficient" in result.get("narrative", ""):
            return {"ticker": ticker, "error": result["narrative"]}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    return {
        "skill": "market-liquidity-sweep",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "pattern": result["pattern"],
        "signals": result["signals"],
        "input_scores": result["input_scores"],
        "narrative": result["narrative"],
    }


def main():
    ticker, json_mode, source, interval, period = safe_parse_args(sys.argv[1:])
    require_ticker(ticker, json_mode)
    result = analyze(ticker, source=source, interval=interval, period=period)

    if json_mode:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    pat = result["pattern"]
    sigs = result["signals"]
    print_header(f"LIQUIDITY SWEEP PATTERN — {ticker}")
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
