"""analysis/notes — typed accessor for the market-notes skill data file.

Centralises the on-disk path resolution so other skills don't have to know
about the file layout. All read/write happens here; skills/market-notes
exposes the same via its CLI.

Default location: `skills/market-notes/data/notes.json`, overridable via:
    - MARKET_SKILLS_NOTES_PATH env var (absolute path)
    - explicit `path=` argument to every function

The file format is a JSON object: `{pair: [note, ...]}`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from analysis.notes_format import (
    STATES,
    STATUSES,
    TYPES,
    filter_active,
    format_note,
    is_active,
    make_entry,
    migrate_entry,
    now_utc,
    parse_expires,
    validate_entry,
    validate_storage,
)


def default_path() -> Path:
    """Resolve the default notes file path.

    Skill lives at `skills/market-notes/`, data at `skills/market-notes/data/`.
    """
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    return repo_root / "skills" / "market-notes" / "data" / "notes.json"


def _resolve_path(path: str | os.PathLike | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("MARKET_SKILLS_NOTES_PATH")
    if env:
        return Path(env).expanduser()
    return default_path()


def load_raw(path: str | os.PathLike | None = None) -> dict:
    """Read the raw `{pair: [note, ...]}` dict. Returns {} if file missing."""
    p = _resolve_path(path)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save_raw(data: dict, path: str | os.PathLike | None = None) -> None:
    """Atomic write: tmp + rename. Creates parent dirs as needed."""
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


def load_active(pair: str, path: str | os.PathLike | None = None) -> list[dict]:
    """Active (non-expired) notes for a pair. Empty list if pair absent."""
    return filter_active(load_raw(path).get(pair, []))


def load_all_pairs(path: str | os.PathLike | None = None) -> list[str]:
    """Pairs that have at least one note (active or expired)."""
    return sorted(load_raw(path).keys())


def add_note(
    pair: str,
    text: str,
    expires: str | None = None,
    *,
    status: str | None = None,
    type_: str | None = None,
    state: str | None = None,
    active_timeframe: str | None = None,
    dependencies: list[str] | None = None,
    price_refs: dict | None = None,
    invalidates_on: str | None = None,
    tags: list[str] | None = None,
    meta: dict | None = None,
    path: str | os.PathLike | None = None,
) -> dict:
    """Append a new note to `pair`. Returns the created entry.

    New entries should use the typed keyword args (status, type, state,
    active_timeframe, dependencies, price_refs, invalidates_on, tags). The
    legacy ``meta`` kwarg is accepted for backward compat and translated to
    typed fields via ``migrate_entry`` before the entry is stored.
    """
    data = load_raw(path)
    entry = make_entry(
        text,
        expires=expires,
        status=status,
        type_=type_,
        state=state,
        active_timeframe=active_timeframe,
        dependencies=dependencies,
        price_refs=price_refs,
        invalidates_on=invalidates_on,
        tags=tags,
        meta=meta,
    )
    errors = validate_entry(entry)
    if errors:
        raise ValueError(f"invalid note: {'; '.join(errors)}")
    data.setdefault(pair, []).append(entry)
    save_raw(data, path)
    return entry


def remove_note(pair: str, index: int, path: str | os.PathLike | None = None) -> dict:
    """Remove note at `index` for `pair`. Cleans up empty pair keys."""
    data = load_raw(path)
    notes = data.get(pair, [])
    if index < 0 or index >= len(notes):
        raise IndexError(f"index {index} out of range for {pair} ({len(notes)} notes)")
    removed = notes.pop(index)
    if not notes:
        data.pop(pair, None)
    save_raw(data, path)
    return removed


def prune_expired(path: str | os.PathLike | None = None) -> int:
    """Drop expired notes from disk. Returns count of removed notes."""
    data = load_raw(path)
    removed = 0
    for pair in list(data.keys()):
        kept = filter_active(data[pair])
        removed += len(data[pair]) - len(kept)
        if kept:
            data[pair] = kept
        else:
            data.pop(pair)
    save_raw(data, path)
    return removed


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
    "now_utc",
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
