#!/usr/bin/env python3
"""market-valuation — SP500 CAPE valuation signal (ticker-agnostic)."""

import sys

from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    maybe_render_home_view,
    parse_axi_flags,
    resolve_fields,
)
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
                "usage: run.py [--json] [--ttl=SECONDS] [--no-cache] [--no-history] [--fields=<csv>] [--full]",
                file=sys.stderr,
            )
            sys.exit(2)
    return json_mode, ttl, write_history


def _help_lines() -> list[str]:
    return [
        "Run `market-macro --json` for the cross-asset regime complement",
        "Pass --full for the full payload or --fields=<csv> to project",
    ]


def main():
    fields_arg, full, toon, _ = parse_axi_flags(sys.argv[1:])
    json_mode, ttl, write_history = _parse_argv(sys.argv[1:])
    if len(sys.argv) == 1:
        if maybe_render_home_view(__file__, None, json_mode):
            return
    if ttl is None:
        clear_cache()
        kwargs = {"write_history": write_history}
    else:
        kwargs = {"ttl_seconds": ttl, "write_history": write_history}
    signal = fetch_valuation(**kwargs)

    if json_mode:
        fields = resolve_fields(fields_arg, full=full, default=["regime", "regime_note", "incomplete"])
        cache_run_result(__file__, signal)
        emit_envelope_json(
            signal,
            count=None,
            help=_help_lines(),
            errors=signal.get("errors") or [],
            fields=fields, toon=toon,)
        return

    print()
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
    errs = signal.get("errors") or []
    if errs:
        print()
        print("  source errors:")
        for e in errs:
            print(f"    - {e}")
    print()


if __name__ == "__main__":
    main()
