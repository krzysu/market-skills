#!/usr/bin/env python3
"""bug-scan — classifier-anomaly detector (L2 Pattern B shapes + L3 skew + cross-TF).

Three input modes:

  # 1. Fresh fetch — positional tickers, optional --interval/--period (comma-separated).
  uv run skills/bug-scan/scripts/run.py HYPEUSD SOLUSD \\
      --interval=1h,4h --period=1mo,6mo

  # 2. Read from the swing-scan state tracker (no network, schema translation only).
  uv run skills/bug-scan/scripts/run.py --from-state

  # 3. Read a pre-fetched run-all-l2 or run-all-l3 envelope.
  uv run skills/bug-scan/scripts/run.py --from-json /path/to/l2_envelope.json
"""

import sys

from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe
from analysis.output import (
    cache_run_result,
    emit_envelope_json,
    empty_state,
    maybe_render_home_view,
    parse_axi_flags,
    print_envelope,
    resolve_fields,
)
from analysis.skill_loader import load_lib_for_script


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_argv(argv: list[str]) -> dict:
    tickers: list[str] = []
    intervals = [DEFAULT_INTERVAL]
    periods = [DEFAULT_PERIOD]
    source = None
    from_state = None
    from_json = None
    json_mode = False
    with_chop_score = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            json_mode = True
        elif a == "--from-state":
            from_state = "DEFAULT"
        elif a.startswith("--from-state="):
            from_state = a.split("=", 1)[1]
        elif a == "--from-json":
            from_json = "DEFAULT"
        elif a.startswith("--from-json="):
            from_json = a.split("=", 1)[1]
        elif a == "--with-chop-score":
            with_chop_score = True
        elif a.startswith("--source="):
            source = a.split("=", 1)[1]
        elif a.startswith("--interval="):
            intervals = _split_csv(a.split("=", 1)[1])
        elif a.startswith("--period="):
            periods = _split_csv(a.split("=", 1)[1])
        elif a.startswith("--"):
            print(f"  unknown flag: {a}", file=sys.stderr)
            sys.exit(2)
        else:
            tickers.append(a)
        i += 1
    return {
        "tickers": tickers,
        "intervals": intervals,
        "periods": periods,
        "source": source,
        "from_state": from_state,
        "from_json": from_json,
        "json_mode": json_mode,
        "with_chop_score": with_chop_score,
    }


def main() -> int:
    fields_arg, full, filtered_argv = parse_axi_flags(sys.argv[1:])
    args = _parse_argv(filtered_argv)
    args["fields"] = fields_arg
    args["full"] = full
    if not args["from_state"] and not args["from_json"] and not args["tickers"]:
        if maybe_render_home_view(__file__, None, args["json_mode"]):
            return 0
        print(
            "usage: run.py TICKER [TICKER ...] [--json] [--source=PROVIDER] "
            "[--interval=INTERVALS] [--period=PERIODS] "
            "[--from-state[=PATH]] [--from-json[=PATH]] "
            "[--with-chop-score] [--fields=<csv>] [--full]",
            file=sys.stderr,
        )
        return 2

    # Validate interval/period combos (single-pair case only; multi-TF
    # callers should validate per-pair via a custom loop if they care).
    if not args["from_state"] and not args["from_json"]:
        try:
            for interval, period in zip(args["intervals"], args["periods"], strict=False):
                validate_timeframe(interval, period)
        except ValueError as e:
            print(f"  invalid timeframe: {e}", file=sys.stderr)
            return 2

    # Import lib lazily so --from-state / --from-json paths don't pay the
    # analysis-import cost when the state-tracker JSON is malformed (those
    # paths don't need the L2/L3 helpers).
    lib = load_lib_for_script(__file__)

    from_state_path = None
    if args["from_state"]:
        if args["from_state"] == "DEFAULT":
            from_state_path = lib.default_state_path()
        else:
            from_state_path = args["from_state"]
    from_json_path = None
    if args["from_json"]:
        if args["from_json"] == "DEFAULT":
            print(
                "  --from-json requires an explicit path (e.g. --from-json=/tmp/l2.json). "
                "There is no canonical default L2/L3 envelope; use --from-state for the "
                "default state-tracker path.",
                file=sys.stderr,
            )
            return 2
        from_json_path = args["from_json"]

    envelope = lib.run_scan(
        tickers=args["tickers"] or None,
        intervals=args["intervals"],
        periods=args["periods"],
        source=args["source"],
        from_state=from_state_path,
        from_json=from_json_path,
        with_chop_score=args["with_chop_score"],
    )

    if args["json_mode"]:
        findings = envelope.get("findings") or []
        if envelope.get("ok") is False:
            print_envelope(
                empty_state(
                    errors=[envelope.get("error", "unknown")],
                    help=[
                        "Run with explicit tickers to debug",
                        "Pass --full for the full payload or --fields=<csv> to project",
                    ],
                )
            )
            return 1
        fields = resolve_fields(
            args["fields"],
            full=args["full"],
            default=["findings", "scan_summary", "tickers_scanned"],
        )
        cache_run_result(__file__, envelope)
        emit_envelope_json(
            envelope,
            count=len(findings),
            help=[
                "Run `bug-scan HYPEUSD SOLUSD --json` for a fresh scan on a pair",
                "Pass --full for the full payload or --fields=<csv> to project",
            ],
            fields=fields,
        )
        return 0

    findings = envelope.get("findings") or []
    if envelope.get("ok") is False:
        print(f"  error: {envelope.get('error', 'unknown')}")
        return 1
    print(f"  bug-scan findings: {len(findings)}")
    if findings:
        print()
        print(lib.format_for_terminal(envelope))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
