"""market-notes — per-pair thesis notes with timestamps and optional expiration.

Pure re-export of `analysis.notes` so that other skills in this repo can
load the skill's `lib.py` via `analysis.skill_loader.load_skill()` without
needing path gymnastics. The CLI in `scripts/run.py` uses the same module.

Storage layout: `skills/market-notes/data/notes.json` (gitignored).
File format: `{pair: [note, ...]}`.

Library entry points (all in `analysis.notes`):
    from analysis.notes import load_active, add_note, remove_note, prune_expired
    notes = load_active("BTCUSD")          # -> list of active note dicts
"""

from analysis.notes import (
    STATES,
    STATUSES,
    TYPES,
    add_note,
    default_path,
    filter_active,
    format_note,
    is_active,
    load_active,
    load_all_pairs,
    load_raw,
    make_entry,
    migrate_entry,
    parse_expires,
    prune_expired,
    remove_note,
    save_raw,
    validate_entry,
    validate_storage,
)

__all__ = [
    "add_note",
    "default_path",
    "filter_active",
    "format_note",
    "is_active",
    "load_active",
    "load_all_pairs",
    "load_raw",
    "make_entry",
    "migrate_entry",
    "parse_expires",
    "prune_expired",
    "remove_note",
    "save_raw",
    "STATES",
    "STATUSES",
    "TYPES",
    "validate_entry",
    "validate_storage",
]
