#!/usr/bin/env python3
"""One-off backfill: re-derive closed idea outcomes using post-fix formulas.

Re-computes ``actual_return_pct``, ``hit_target``, and ``outcome_verdict`` for
every closed idea in the journal using the current (direction-aware, wick-based)
formulas from the journal-write-recipe.  Idempotent — safe to re-run.

Formulas applied (matching ``references/journal-write-recipe.md`` lines 149-167):

  actual_return_pct:
    long  → (exit_price - entry_price) / entry_price * 100
    short → (entry_price - exit_price) / entry_price * 100

  hit_target:
    long  → exit_wick_high >= tp1
    short → exit_wick_low  <= tp1

  outcome_verdict:
    hit  → hit_target is True
    miss → hit_target is False

Skips ideas that are not ``status == "closed"`` or have no ``exit_price``.
Ideas with ``outcome_verdict == "expired"`` are left untouched.

Usage:
    python3 scripts/backfill_outcomes.py                    # uses $MARKET_SKILLS_DAILY_TRADE_PICK_PATH
    python3 scripts/backfill_outcomes.py --journal /path/to/picks.json
    python3 scripts/backfill_outcomes.py --dry-run           # preview only, no write

Dependencies: none outside stdlib + analysis.track_record (already installed).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ENV_VAR = "MARKET_SKILLS_DAILY_TRADE_PICK_PATH"


def resolve_journal_path(explicit: str | os.PathLike | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    raise OSError(f"{ENV_VAR} is not set and no --journal path was provided.")


def recompute_outcomes(journal: list[dict]) -> tuple[int, int]:
    """Re-derive outcomes for every closed idea in the journal.

    Returns (n_recomputed, n_skipped) for reporting.
    Mutates ideas in-place.
    """
    n_recomputed = 0
    n_skipped = 0

    for scan in journal:
        for idea in scan.get("ideas", []):
            if idea.get("status") != "closed":
                continue
            if idea.get("outcome_verdict") == "expired":
                n_skipped += 1
                continue
            exit_price = idea.get("exit_price")
            if exit_price is None:
                n_skipped += 1
                continue

            entry = idea["entry_price"]
            direction = idea.get("direction", "long")

            # actual_return_pct
            if direction == "long":
                ret = (exit_price - entry) / entry * 100
            else:
                ret = (entry - exit_price) / entry * 100
            idea["actual_return_pct"] = round(ret, 4)

            # hit_target from wick touch
            tp1 = idea.get("tp1")
            if tp1 is not None:
                wick_low = idea.get("exit_wick_low", exit_price)
                wick_high = idea.get("exit_wick_high", exit_price)
                if direction == "long":
                    idea["hit_target"] = wick_high >= tp1
                else:
                    idea["hit_target"] = wick_low <= tp1
            else:
                idea["hit_target"] = False

            idea["outcome_verdict"] = "hit" if idea["hit_target"] else "miss"
            n_recomputed += 1

    return n_recomputed, n_skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill DTP journal outcomes with post-fix formulas")
    parser.add_argument("--journal", help="Path to picks.json (default: $MARKET_SKILLS_DAILY_TRADE_PICK_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    path = resolve_journal_path(args.journal)
    journal = json.loads(path.read_text())
    n_original = sum(
        1
        for scan in journal
        for idea in scan.get("ideas", [])
        if idea.get("status") == "closed" and idea.get("outcome_verdict") != "expired"
    )

    n_recomputed, n_skipped = recompute_outcomes(journal)

    print(f"Journal: {path}")
    print(f"  Closed ideas with outcome data: {n_original}")
    print(f"  Recomputed: {n_recomputed}")
    print(f"  Skipped (expired / no exit_price): {n_skipped}")

    if n_recomputed == 0:
        print("  Nothing to backfill.")
        return

    if args.dry_run:
        print("  [DRY RUN] No changes written.")
        return

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(journal, indent=2, default=str) + "\n")
    tmp.replace(path)
    print(f"  Wrote {path}")


if __name__ == "__main__":
    main()
