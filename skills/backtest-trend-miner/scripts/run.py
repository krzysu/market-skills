#!/usr/bin/env python3
"""backtest-trend-miner — mine runs.jsonl + 7n baseline for per-combo decay/improvement.

Read-only analyzer over the nightly backtest pipeline's ``runs.jsonl`` batch
history and ``backtest-pipeline-state.json`` rolling 7-night Sharpe baseline.
Detects per-(interval, strategy, ticker) Sharpe decay / improvement and the
stable edges, and reports them through the canonical AXI envelope. No network,
no writes to any pipeline file.

Usage:
    uv run skills/backtest-trend-miner/scripts/run.py --json
    uv run skills/backtest-trend-miner/scripts/run.py --json --min-runs 7
    uv run skills/backtest-trend-miner/scripts/run.py --json --intervals 1d 4h
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from analysis.output import emit_envelope_json, empty_state, parse_axi_flags, print_envelope, resolve_fields
from analysis.skill_loader import load_lib_for_script

_lib = load_lib_for_script(__file__)

# skills/backtest-trend-miner/scripts/run.py -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_DEFAULT_OUT_DIRNAME = Path("data") / "backtest-nightly"


def _resolve_out_dir() -> Path:
    """Resolve the pipeline output directory.

    ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR`` wins; otherwise fall back to the
    repo-relative ``data/backtest-nightly/`` default documented in the bead.
    """
    env = os.environ.get(_lib.ENV_OUT_DIR)
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / _DEFAULT_OUT_DIRNAME


def _load_runs(out_dir: Path) -> list[dict]:
    """Parse ``runs.jsonl`` (one JSON object per line), skipping malformed lines."""
    runs_file = out_dir / "runs.jsonl"
    runs: list[dict] = []
    if not runs_file.is_file():
        return runs
    try:
        with runs_file.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    runs.append(obj)
    except OSError:
        return runs
    return runs


def _load_state(out_dir: Path) -> dict:
    """Parse ``backtest-pipeline-state.json``; ``{}`` when missing or unreadable."""
    state_file = out_dir / "backtest-pipeline-state.json"
    if not state_file.is_file():
        return {}
    try:
        with state_file.open() as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _collect_insufficient(runs: list[dict]) -> list[str]:
    """Flatten every run's ``insufficient_data`` strings for the envelope errors list."""
    errors: list[str] = []
    for run in runs:
        flagged = run.get("insufficient_data")
        if isinstance(flagged, list):
            for entry in flagged:
                if isinstance(entry, str):
                    errors.append(entry)
    return errors


def _print_human(payload: dict) -> None:
    print("backtest-trend-miner")
    print(
        f"runs: {payload['config']['total_runs']}  combos: {len(payload['combos'])}  "
        f"min-runs: {payload['config']['min_runs']}"
    )
    flags = payload["flags"]
    print(f"decay: {len(flags['decay'])}  improving: {len(flags['improving'])}  stable: {len(flags['stable'])}")
    for interval, entries in payload["analysis"].items():
        print(f"\n--- {interval} ({len(entries)} combos) ---")
        for entry in entries:
            if entry["downtrend"]:
                tag = "DOWN"
            elif entry["improving"]:
                tag = "UP  "
            else:
                tag = "    "
            avg = f"{entry['avg_sharpe_7n']:+.2f}" if entry["avg_sharpe_7n"] is not None else "    —"
            print(
                f"  {tag} {entry['strategy']:<28} {entry['ticker']:<12} "
                f"slope={entry['trend_slope']:+.4f} latest={entry['sharpe_latest']:+.2f} "
                f"7n={avg} n={entry['n_runs']}"
            )
    print("\nuse --json for the full AXI envelope")


def main() -> int:
    fields_arg, full, toon, _from_state, _ttl, filtered_argv = parse_axi_flags(sys.argv[1:])
    parser = argparse.ArgumentParser(
        prog="backtest-trend-miner",
        description="Mine the nightly backtest runs.jsonl + 7n baseline for per-combo Sharpe decay/improvement.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the AXI envelope {data, count, errors, help}.")
    parser.add_argument(
        "--min-runs",
        type=int,
        default=_lib.DEFAULT_MIN_RUNS,
        help=f"Only analyze combos seen in >= N runs (default: {_lib.DEFAULT_MIN_RUNS}).",
    )
    parser.add_argument(
        "--intervals",
        nargs="*",
        default=None,
        help="Restrict analysis to these intervals (e.g. --intervals 1d 4h). Default: all.",
    )
    args = parser.parse_args(filtered_argv)

    if args.min_runs < 1:
        print("error: --min-runs must be >= 1", file=sys.stderr)
        return 2

    out_dir = _resolve_out_dir()
    runs = _load_runs(out_dir)
    state = _load_state(out_dir)
    errors = _collect_insufficient(runs)

    if not runs:
        help_lines = [
            f"No runs.jsonl found under the pipeline output dir (resolved to {_lib.ENV_OUT_DIR} or data/backtest-nightly/).",
            "Run the nightly backtest-pipeline first, or set MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR to the dir that contains runs.jsonl.",
        ]
        if args.json:
            print_envelope(
                empty_state(
                    errors=["no runs.jsonl data found"],
                    help=help_lines,
                )
            )
        else:
            print("no runs.jsonl data found — run the backtest-pipeline first")
        return 0

    intervals = args.intervals or None
    payload = _lib.analyze(runs, state, min_runs=args.min_runs, intervals=intervals)
    distinct_intervals = sorted({c["interval"] for c in payload["combos"]})
    payload["config"] = {
        "min_runs": args.min_runs,
        "intervals": intervals if intervals else distinct_intervals,
        "total_runs": len(runs),
    }

    count = len(payload["combos"])

    if args.json:
        emit_envelope_json(
            payload,
            count=count,
            errors=errors,
            help=[
                "Pass --min-runs=N to raise/lower the combo visibility floor",
                "Pass --intervals 1d 4h to restrict analysis to specific intervals",
                "Pass --full for the full payload or --fields=<csv> to project",
            ],
            fields=resolve_fields(fields_arg, full=full, default=None),
            toon=toon,
        )
    else:
        _print_human(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
