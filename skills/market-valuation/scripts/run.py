#!/usr/bin/env python3
"""market-valuation — SP500 CAPE valuation signal (ticker-agnostic)."""

import sys

from analysis.formatting import emit_json, print_header
from analysis.valuation import clear_cache, fetch_valuation


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
    signal = fetch_valuation(**kwargs)

    if json_mode:
        emit_json(signal)
        return

    print_header("SP500 VALUATION")
    inputs = signal.get("inputs", {})
    regime = signal.get("regime", {})
    print(f"  timestamp:    {signal.get('timestamp', '?')}")
    print(
        f"  regime:       {regime.get('regime', '?')}"
        + (f"  (z={regime.get('cape_zscore'):+.2f})" if isinstance(regime.get("cape_zscore"), (int, float)) else "")
    )
    print()
    print(f"  SP500:        {inputs.get('sp500')}")
    print(f"  Shiller CAPE: {inputs.get('cape')}")
    print(f"  50y mean/std: {inputs.get('cape_mean_50y')} / {inputs.get('cape_std_50y')}")
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
