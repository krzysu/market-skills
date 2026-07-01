#!/usr/bin/env python3
"""market-macro — cross-asset regime signal (ticker-agnostic)."""

import sys

from analysis.formatting import emit_json, print_header
from analysis.macro import clear_cache, fetch_regime


def _parse_argv(argv):
    json_mode = False
    ttl: float | None = None
    write_history = True
    for a in argv:
        if a == "--json":
            json_mode = True
        elif a == "--no-cache":
            ttl = 0
        elif a == "--no-history":
            write_history = False
        elif a.startswith("--ttl="):
            try:
                ttl = float(a.split("=", 1)[1])
            except ValueError:
                print(f"error: --ttl expects a number, got {a.split('=', 1)[1]!r}", file=sys.stderr)
                sys.exit(2)
        else:
            print(f"error: unknown flag {a!r}", file=sys.stderr)
            print(
                "usage: run.py [--json] [--ttl=SECONDS] [--no-cache] [--no-history]",
                file=sys.stderr,
            )
            sys.exit(2)
    return json_mode, ttl, write_history


def main():
    json_mode, ttl, write_history = _parse_argv(sys.argv[1:])
    if ttl is None:
        clear_cache()  # ensure fresh on direct CLI invocation
        kwargs = {"write_history": write_history}
    else:
        kwargs = {"ttl_seconds": ttl, "write_history": write_history}
    signal = fetch_regime(**kwargs)

    if json_mode:
        emit_json(signal)
        return

    print_header("MACRO REGIME")
    inputs = signal.get("inputs", {})
    regime = signal.get("regime", {})
    print(f"  timestamp:  {signal.get('timestamp', '?')}")
    print(
        f"  regime:     "
        f"{regime.get('risk_appetite', '?')} / "
        f"{regime.get('liquidity', '?')} / "
        f"{regime.get('sentiment', '?')}"
    )
    print()
    print(f"  VIX:        {inputs.get('vix')}")
    print(f"  DXY:        {inputs.get('dxy')}")
    print(f"  US10Y:      {inputs.get('us10y')}")
    print(f"  F&G:        {inputs.get('fng')}" + (f"  ({inputs.get('fng_label')})" if inputs.get("fng_label") else ""))
    print(
        f"  BTC.D:      {inputs.get('btc_dominance')}"
        + (f"  [{inputs.get('btc_dominance_source')}]" if inputs.get("btc_dominance_source") else "")
    )
    print(f"  Total mcap: {inputs.get('total_mcap_usd')}")
    print()
    print(f"  note: {signal.get('regime_note', '')}")
    errors = signal.get("errors") or []
    if errors:
        print()
        print("  source errors:")
        for e in errors:
            print(f"    - {e}")
    print()


if __name__ == "__main__":
    main()
