#!/usr/bin/env python3
"""market-exhaustion — L2 pattern detection: detects whether a price move is about to end."""

import sys
from datetime import UTC, datetime

from analysis.data import fetch_ohlc
from analysis.formatting import print_header, require_ticker, safe_parse_args
from analysis.output import (
    emit_envelope_json,
    empty_state,
    parse_axi_flags,
    print_envelope,
    resolve_fields,
    truncate,
)
from analysis.skill_loader import load_lib_for_script

NARRATIVE_LIMIT = 120
DEFAULT_FIELDS = ["ticker", "fired", "classification", "confidence"]


def analyze(ticker, *, source=None, interval="1d", period="1y"):
    candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
    if not candles:
        return {"ticker": ticker, "error": "no data"}

    _lib = load_lib_for_script(__file__)
    result = _lib.analyze(candles, interval=interval, period=period)
    if "insufficient" in result.get("narrative", ""):
        return {"ticker": ticker, "error": result["narrative"]}

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider = source or "auto-detected"

    pattern = result["pattern"]
    fired = bool(pattern.get("present")) and pattern.get("classification") is not None

    return {
        "skill": "market-exhaustion",
        "ticker": ticker,
        "timestamp": now,
        "provider": provider,
        "interval": interval,
        "period": period,
        "fired": fired,
        "classification": pattern.get("classification"),
        "confidence": pattern.get("confidence"),
        "max_confidence": pattern.get("max_confidence"),
        "type": pattern.get("type"),
        "signals": result["signals"],
        "input_scores": result["input_scores"],
        "narrative": result["narrative"],
    }


def _help_lines(ticker: str) -> list[str]:
    return [
        f"Run `strategy-exhaustion-fade {ticker} --json` for the L3 idea",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def main():
    fields_arg, full, filtered_argv = parse_axi_flags(sys.argv[1:])
    ticker, json_mode, source, interval, period = safe_parse_args(filtered_argv)
    require_ticker(ticker, json_mode)
    result = analyze(ticker, source=source, interval=interval, period=period)

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

    pat = result
    sigs = result.get("signals", {})
    print_header(f"EXHAUSTION PATTERN — {ticker}")
    print(f"  {ticker}")
    print()
    print(
        f"    Pattern Present:  {'YES' if pat.get('fired') else 'NO'}  (confidence: {pat.get('confidence')}/{pat.get('max_confidence')})"
    )
    if pat.get("classification"):
        print(f"    Classification:   {pat['classification']}")
    if pat.get("type"):
        print(f"    Type:             {pat['type']}")
    print()
    if sigs:
        print("  Sub-Signals:")
        for name, sig in sigs.items():
            check = "\u2713" if sig.get("present") else "\u2717"
            print(f"    {check} {name:<25s}  (w={sig.get('weight')})")
        print()
    print(f"  {truncate(result.get('narrative', ''), limit=NARRATIVE_LIMIT)}")
    print()


if __name__ == "__main__":
    main()
