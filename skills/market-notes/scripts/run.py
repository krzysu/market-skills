#!/usr/bin/env python3
"""market-notes — per-pair thesis notes CLI.

Notes are stored as `{pair: [note, ...]}` JSON at
`skills/market-notes/data/notes.json` (gitignored). Each note carries an
`added` timestamp and optional `expires`; expired notes are hidden by
`list`/`load_active` but kept on disk until `prune` removes them.

Exit codes:
  0 — success
  1 — fatal (bad args, file error)
  2 — invalid usage (missing arguments, unknown subcommand)

CLI:
  uv run skills/market-notes/scripts/run.py add PAIR "note text" [options]
      --expires 14d                      shorthand (default: 14d; use 'never' for none)
      --status STATUS                    lifecycle state
      --type TYPE                        kind of note
      --state STATE                      structural state of underlying
      --active-timeframe 1d              first-class timeframe (e.g. 1d, 4h)
      --dependencies BTCUSD,ETHUSD       other pair keys this note rides on
      --price-refs '{"stop": 2.54}'      typed price levels (JSON literal or @path)
      --invalidates-on "weekly_close_above_EMA21"   free-text condition
      --tags thesis,wait                 escape-hatch tags that don't fit the triple
      --meta '{...}'                     LEGACY: free-form metadata blob (auto-migrated)
  uv run skills/market-notes/scripts/run.py list [PAIR] [--all]
  uv run skills/market-notes/scripts/run.py remove PAIR INDEX
  uv run skills/market-notes/scripts/run.py prune
  uv run skills/market-notes/scripts/run.py migrate [--dry-run]
  uv run skills/market-notes/scripts/run.py validate
  uv run skills/market-notes/scripts/run.py --json list [PAIR]        # machine output
"""

import argparse
import json
import sys

from analysis.notes import (
    STATES,
    STATUSES,
    TYPES,
    add_note,
    format_note,
    is_active,
    load_raw,
    migrate_entry,
    now_utc,
    prune_expired,
    remove_note,
    save_raw,
    validate_storage,
)
from analysis.notes_format import validate_entry


def _parse_kv_json(value: str | None, flag: str) -> dict | None:
    """Parse --price-refs: JSON literal OR @path to read JSON from a file."""
    if value is None:
        return None
    if value.startswith("@"):
        path = value[1:]
        with open(path) as f:
            data = json.load(f)
    else:
        data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError(f"{flag} must decode to a JSON object")
    return data


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return parts or None


def _cmd_add(args: argparse.Namespace) -> int:
    text = args.text
    if not text or not text.strip():
        print("error: empty note text", file=sys.stderr)
        return 2
    try:
        price_refs = _parse_kv_json(args.price_refs, "--price-refs")
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(f"error: bad --price-refs: {e}", file=sys.stderr)
        return 1

    meta = None
    if args.meta is not None:
        try:
            if args.meta.startswith("@"):
                with open(args.meta[1:]) as f:
                    meta = json.load(f)
            else:
                meta = json.loads(args.meta)
        except (json.JSONDecodeError, OSError) as e:
            print(f"error: bad --meta: {e}", file=sys.stderr)
            return 1

    try:
        entry = add_note(
            args.pair,
            text,
            expires=args.expires,
            status=args.status,
            type_=args.type,
            state=args.state,
            active_timeframe=args.active_timeframe,
            dependencies=_parse_csv(args.dependencies),
            price_refs=price_refs,
            invalidates_on=args.invalidates_on,
            tags=_parse_csv(args.tags),
            meta=meta,
            path=args.config,
        )
    except (ValueError, IndexError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"added": args.pair, "entry": entry}))
    else:
        print(f"Added to {args.pair}: {format_note(entry)}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from analysis.output import emit_envelope_json

    data = load_raw(args.config)
    now = now_utc()
    pairs = [args.pair] if args.pair else sorted(data.keys())
    if not args.pair and not pairs:
        if args.json:
            emit_envelope_json(
                {"pairs": {}},
                count=0,
                help=["Add a note with `market-notes add <PAIR> <TEXT>`"],
            )
        else:
            print("(no notes)")
        return 0

    if args.json:
        out_pairs = {}
        for p in pairs:
            notes = data.get(p, [])
            if args.all:
                out_pairs[p] = notes
            else:
                active_idx = [i for i, n in enumerate(notes) if is_active(n, now)]
                if not active_idx:
                    continue
                out_pairs[p] = [notes[i] for i in active_idx]
        total_notes = sum(len(v) for v in out_pairs.values())
        emit_envelope_json(
            {"pairs": out_pairs},
            count=total_notes,
            help=[
                "Run `market-notes add <PAIR> <TEXT>` to append a new note",
                "Pass --all to include expired notes",
            ],
        )
        return 0

    any_shown = False
    for p in pairs:
        notes = data.get(p, [])
        visible = notes if args.all else [n for n in notes if is_active(n, now)]
        if not visible:
            continue
        any_shown = True
        print(f"{p}:")
        for i, n in enumerate(notes):
            if not args.all and not is_active(n, now):
                continue
            marker = " (EXPIRED)" if not is_active(n, now) else ""
            print(f"  {i}. {format_note(n, now)}{marker}")
    if not any_shown:
        print("(no active notes)")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    try:
        removed = remove_note(args.pair, args.index, path=args.config)
    except IndexError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"removed_pair": args.pair, "removed_index": args.index, "entry": removed}))
    else:
        print(f"Removed from {args.pair}: {removed['note']}")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    n = prune_expired(args.config)
    if args.json:
        print(json.dumps({"removed": n}))
    else:
        print(f"Pruned {n} expired note(s)")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Rewrite legacy `meta`-blobs into the typed schema. Idempotent.

    Dry-run prints a per-pair summary without writing. Otherwise rewrites
    in-place and reports counts.
    """
    data = load_raw(args.config)
    pairs_touched = 0
    notes_migrated = 0
    notes_already_typed = 0
    validation_errors = 0

    for pair, notes in data.items():
        changed = False
        for i, n in enumerate(notes):
            if not isinstance(n, dict):
                continue
            if "meta" not in n:
                notes_already_typed += 1
                continue
            migrated = migrate_entry(n)
            errors = validate_entry(migrated)
            if errors:
                validation_errors += 1
                if not args.dry_run:
                    print(f"{pair}[{i}]: still invalid after migration: {errors}", file=sys.stderr)
                continue
            notes[i] = migrated
            notes_migrated += 1
            changed = True
        if changed:
            pairs_touched += 1

    if not args.dry_run:
        save_raw(data, args.config)

    summary = {
        "dry_run": bool(args.dry_run),
        "pairs_touched": pairs_touched,
        "notes_migrated": notes_migrated,
        "notes_already_typed": notes_already_typed,
        "validation_errors": validation_errors,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        mode = "would migrate" if args.dry_run else "migrated"
        print(
            f"{mode} {notes_migrated} note(s) across {pairs_touched} pair(s); "
            f"{notes_already_typed} already typed; {validation_errors} validation error(s)"
        )
    return 0 if validation_errors == 0 else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    data = load_raw(args.config)
    errors = validate_storage(data)
    if args.json:
        print(json.dumps({"errors": errors, "pairs": list(data.keys())}))
        return 0 if not errors else 1
    if errors:
        print("VALIDATION ERRORS:")
        for e in errors:
            print(f"  {e}")
        return 1
    print(f"OK — {len(data)} pair(s) with notes, {sum(len(v) for v in data.values())} note(s) total")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market-notes",
        description="Per-pair thesis notes with timestamps and optional expiration.",
    )
    p.add_argument("--config", help="Path to notes.json (default: skills/market-notes/data/notes.json)")
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="Append a note to a pair")
    pa.add_argument("pair", help="Ticker/pair key, e.g. BTCUSD, hl:LIT")
    pa.add_argument("text", help="Note text")
    pa.add_argument(
        "--expires", default="14d", help="Shorthand (14d/2w/1m/6h) or ISO date (default: 14d, use 'never' for none)"
    )
    pa.add_argument("--status", choices=sorted(STATUSES), help="Lifecycle state")
    pa.add_argument("--type", dest="type", choices=sorted(TYPES), help="Kind of note")
    pa.add_argument("--state", choices=sorted(STATES), help="Structural state of underlying")
    pa.add_argument("--active-timeframe", help="Timeframe the note applies to (e.g. 1d, 4h, 1wk)")
    pa.add_argument("--dependencies", help="Comma-separated pair keys this note rides on")
    pa.add_argument("--price-refs", help="Typed price levels as JSON literal or @path/to/file.json")
    pa.add_argument("--invalidates-on", help="Free-text invalidation condition")
    pa.add_argument("--tags", help="Comma-separated escape-hatch tags")
    pa.add_argument(
        "--meta",
        help="LEGACY: free-form metadata as JSON literal or @path. Auto-translated to typed fields.",
    )
    pa.set_defaults(func=_cmd_add)

    pl = sub.add_parser("list", help="List active notes (optionally for one pair)")
    pl.add_argument("pair", nargs="?", help="Optional pair filter")
    pl.add_argument("--all", action="store_true", help="Include expired notes")
    pl.set_defaults(func=_cmd_list)

    pr = sub.add_parser("remove", help="Remove a note by pair + index")
    pr.add_argument("pair")
    pr.add_argument("index", type=int)
    pr.set_defaults(func=_cmd_remove)

    pp = sub.add_parser("prune", help="Drop expired notes from disk")
    pp.set_defaults(func=_cmd_prune)

    pm = sub.add_parser(
        "migrate",
        help="Rewrite legacy 'meta' blobs into the typed schema. Idempotent.",
    )
    pm.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    pm.set_defaults(func=_cmd_migrate)

    pv = sub.add_parser("validate", help="Validate the storage file")
    pv.set_defaults(func=_cmd_validate)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "add" and args.expires and args.expires.lower() == "never":
        args.expires = None

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
