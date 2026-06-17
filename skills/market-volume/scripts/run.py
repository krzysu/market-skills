#!/usr/bin/env python3
"""market-volume — Volume analysis: ratio, OBV trend, regime classification."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import importlib.util


def _load_lib():
    """Load lib.py from the skill directory (handles hyphens in path)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib.py")
    spec = importlib.util.spec_from_file_location("market_volume_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import emit_json, parse_args, print_header, require_ticker


def analyze(ticker, source=None):
    candles = fetch_ohlc(ticker, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = _load_lib()
    result = _lib.analyze(candles)
    if "error" in result:
        return {"ticker": ticker, **result}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    return {
        "skill": "market-volume",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": "1d",
        "period": "1y",
        "candles_used": len(candles),
        "indicators": result,
        "score": None,
        "signal": None,
        "zone": None,
    }


def main():
    ticker, json_mode, source = parse_args(sys.argv[1:])
    require_ticker(ticker, json_mode)
    result = analyze(ticker, source=source)

    if json_mode:
        emit_json(result)
        return

    if "error" in result:
        print(f"  {ticker}: {result['error']}")
        return

    ind = result["indicators"]
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
