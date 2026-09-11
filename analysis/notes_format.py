"""analysis/notes_format — pure formatting/validation helpers for market notes.

No I/O. No env vars. No module-level state. All functions are deterministic
(except those that read `datetime.now()` for timestamps/expiration checks).

This module is the source of truth for the note schema and helpers;
analysis/notes.py adds I/O on top; skills/market-notes/lib.py re-exports
the union for skill convention compatibility.

Validation API split:
    - ``validate_entry_findings`` / ``validate_storage_findings`` return
      ``Finding(severity, message)`` with severity in ``{"error", "warning"}``.
      These are the severity-tagged entry points — use them when you need to
      distinguish hard errors from advisory warnings.
    - ``validate_entry`` / ``validate_storage`` are the backward-compatible
      error-only accessors (return ``list[str]``). They return only error
      messages, never warnings. ``add_note``, ``migrate``, and the existing
      test suite depend on this: a non-empty return means "fatal".

A note is a dict:

    {
        # core identity
        "note": <str>,                          # free-text thesis / observation / plan
        "added": <ISO str>,                     # timestamp of creation (UTC)
        "expires": <ISO str | None>,            # when the note is no longer considered active
        "updated": <ISO str | None>,            # timestamp of last edit (optional)

        # canonical triple — required for new entries, optional for legacy
        "status": <str>,                        # lifecycle state (recommended: STATUSES)
        "type":   <str>,                        # kind of note (recommended: TYPES)
        "state":  <str>,                        # structural state (recommended: STATES)

        # first-class typed fields (all optional)
        "active_timeframe": <str | None>,       # e.g. "1d", "4h", "1wk"
        "dependencies":       <list[str]>,      # other pair keys this note rides on
        "price_refs": {                         # typed price levels + extra escape hatch
            "stop": <float | None>,
            "target": <float | None>,
            "target_2": <float | None>,
            "target_3": <float | None>,
            "entry": <float | None>,
            "invalidation_below": <float | None>,
            "invalidation_above": <float | None>,
            "extra": <dict | None>,             # free-form object for non-canonical refs
        },
        "invalidates_on": <str | None>,         # free-text condition (e.g. "weekly_close_above_EMA21")
        "tags": <list[str]>,                    # escape hatch for tags that don't fit the triple
    }

Legacy `meta` is still accepted on input for backward compat (translated to typed
fields). New entries are written with typed fields only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

_SHORTHAND_RE = re.compile(r"^(\d+)([dwmh])$")


class Finding(NamedTuple):
    severity: str
    message: str


_CANONICAL_PRICE_REFS = frozenset(
    {"stop", "target", "target_2", "target_3", "entry", "invalidation_below", "invalidation_above"}
)

# Canonical enums (kept as frozensets for O(1) membership + frozen iter)
STATUSES = frozenset(
    {
        "thesis",  # long-held directional view
        "open",  # position is currently active
        "setup",  # actionable setup, ready to enter
        "watchlist",  # on radar, not yet active
        "hedge",  # defensive / offsetting position
        "starter",  # initial position (scaling in)
        "invalidated",  # thesis broken, awaiting post-mortem
        "post_mortem",  # broken + conclusion documented
    }
)

TYPES = frozenset(
    {
        "thesis",  # directional thesis
        "setup",  # actionable trade setup
        "observation",  # research / observation
        "plan",  # pending action plan
        "note",  # generic
    }
)

STATES = frozenset(
    {
        "coiled_range_intact",  # coiled range structure still intact
        "coiled_range_broken",  # coiled range triggered / failed
        "trending_up",
        "trending_down",
        "range_bound",
        "unknown",
    }
)

# Legacy ad-hoc tags (from the free-form meta.tags era) and how they map into
# the new triple. Used by `migrate_entry` to normalise old entries.
_LEGACY_TAG_MAP = {
    "thesis": ("type", "thesis"),
    "open": ("status", "open"),
    "setup": ("type", "setup"),
    "watchlist": ("status", "watchlist"),
    "hedge": ("status", "hedge"),
    "starter": ("status", "starter"),
    "invalidated": ("status", "invalidated"),
    "post-mortem": ("status", "post_mortem"),
    "post_mortem": ("status", "post_mortem"),
    "setup_invalidated": None,  # composite: status=invalidated + type=setup
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_expires(value: str | None) -> str | None:
    """Accept shorthand ('14d','2w','1m','6h'), ISO date, or None. Returns ISO str or None."""
    if value is None:
        return None
    s = str(value).strip()
    m = _SHORTHAND_RE.match(s.lower())
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "w": timedelta(weeks=n),
            "m": timedelta(days=30 * n),
        }[unit]
        return (now_utc() + delta).isoformat()
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    except ValueError as e:
        raise ValueError(f"Bad expires value '{value}'. Use shorthand (14d, 2w, 1m, 6h) or ISO date/datetime.") from e


def is_active(note: dict, now: datetime | None = None) -> bool:
    exp = note.get("expires")
    if not exp:
        return True
    if now is None:
        now = now_utc()
    try:
        return datetime.fromisoformat(exp) > now
    except ValueError:
        return True


def filter_active(notes: list[dict], now: datetime | None = None) -> list[dict]:
    """Return the subset of notes whose `expires` is in the future."""
    return [n for n in notes if is_active(n, now)]


def _coerce_price_refs(value) -> dict | None:
    """Validate/normalize a price_refs dict. Returns None for empty/None input.

    Canonical typed keys must be numeric (or None). Unknown top-level keys are
    preserved verbatim so read-modify-write round-trips are lossless; writers
    should migrate non-canonical content into ``extra``.

    Raises ``ValueError`` for:
    - non-dict ``price_refs``
    - non-dict ``extra``
    - non-numeric value in a canonical key
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"price_refs must be a dict, got {type(value).__name__}")
    cleaned: dict = {}
    for k, v in value.items():
        if k == "extra":
            if v is not None and not isinstance(v, dict):
                raise ValueError(f"price_refs.extra must be a dict, got {type(v).__name__}")
            if v is not None:
                cleaned[k] = v
            continue
        if k in _CANONICAL_PRICE_REFS:
            if v is not None:
                try:
                    cleaned[k] = float(v)
                except (TypeError, ValueError) as e:
                    raise ValueError(f"price_refs.{k} must be numeric, got {v!r}") from e
        else:
            cleaned[k] = v
    return cleaned or None


def _coerce_str_list(value, field: str) -> list[str] | None:
    """Validate/normalize a list-of-str field. Returns None for empty/None input."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list, got {type(value).__name__}")
    out = [str(v) for v in value]
    return out or None


def _check_enum(value, allowed: frozenset, field: str) -> None:
    """Validate a descriptive field is a string. Enum membership is advisory only."""
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")


def make_entry(
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
) -> dict:
    """Build a new note dict. Does not persist.

    Precedence: explicit typed kwargs win over `meta` migration. If neither
    is provided, the corresponding field is left as None.
    """
    _check_enum(status, STATUSES, "status")
    _check_enum(type_, TYPES, "type")
    _check_enum(state, STATES, "state")
    cleaned_price_refs = _coerce_price_refs(price_refs)
    cleaned_deps = _coerce_str_list(dependencies, "dependencies")
    cleaned_tags = _coerce_str_list(tags, "tags")

    added_iso = now_utc().isoformat()
    entry: dict = {
        "note": text,
        "added": added_iso,
        "expires": parse_expires(expires),
        "updated": None,
        "status": None,
        "type": None,
        "state": None,
        "active_timeframe": None,
        "dependencies": None,
        "price_refs": None,
        "invalidates_on": None,
        "tags": None,
    }

    if meta is not None:
        migrated = migrate_entry(
            {
                "note": text,
                "added": added_iso,
                "expires": entry["expires"],
                "updated": None,
                "meta": meta,
            }
        )
        for k, v in migrated.items():
            if k == "meta":
                continue
            if v is not None and entry.get(k) is None:
                entry[k] = v

    for k, v in (
        ("status", status),
        ("type", type_),
        ("state", state),
        ("active_timeframe", active_timeframe),
        ("dependencies", cleaned_deps),
        ("price_refs", cleaned_price_refs),
        ("invalidates_on", invalidates_on),
        ("tags", cleaned_tags),
    ):
        if v is not None:
            entry[k] = v

    return entry


def migrate_entry(entry: dict) -> dict:
    """Normalise a legacy entry (with `meta` blob) into the typed schema.

    Idempotent: if the entry is already typed, returns a shallow copy with
    `meta` removed. If `meta` is present, its well-known keys are translated
    into typed fields and any unmappable keys are dropped (with a printed
    warning on stderr from callers that opt in).
    """
    out = dict(entry)
    meta = out.pop("meta", None)
    if not isinstance(meta, dict):
        return out

    if out.get("status") is None or out.get("type") is None:
        tags = meta.get("tags") or []
        for tag in tags:
            key = tag.strip().lower()
            if key in ("setup_invalidated",):
                out.setdefault("status", "invalidated")
                out.setdefault("type", "setup")
                continue
            mapping = _LEGACY_TAG_MAP.get(key)
            if mapping is None:
                continue
            field, value = mapping
            if field in ("status", "type") and out.get(field) is None:
                out[field] = value

    # 1d / 1d_only / 4h_pullback etc. → active_timeframe
    # Bare tf and tf_only are pure timeframe markers (consumed below);
    # tf_<suffix> tags carry extra meaning and stay in `tags`.
    if out.get("active_timeframe") is None:
        for tf in ("1d", "4h", "1h", "1wk", "15m", "1mo"):
            for tag in meta.get("tags") or []:
                if tag == tf or tag.startswith(f"{tf}_"):
                    out["active_timeframe"] = tf
                    break
            if out.get("active_timeframe") is not None:
                break

    if out.get("invalidates_on") is None and isinstance(meta.get("invalidates_on"), str):
        out["invalidates_on"] = meta["invalidates_on"]

    if out.get("price_refs") is None:
        price_keys = ("stop", "target", "entry", "invalidation_below", "invalidation_above")
        prices = {k: meta[k] for k in price_keys if isinstance(meta.get(k), (int, float))}
        if prices:
            out["price_refs"] = {k: float(v) for k, v in prices.items()}

    # Consumed by the detector above: bare tf (e.g. "1d") and tf_only.
    # tf_<suffix> tags (e.g. "4h_pullback") are kept — they carry meaning
    # beyond the timeframe and were promoted to active_timeframe above.
    consumed_by_detector = {"1d", "4h", "1h", "1wk", "15m", "1mo"} | {
        f"{tf}_only" for tf in ("1d", "4h", "1h", "1wk", "15m", "1mo")
    }
    leftover_tags = [
        t
        for t in (meta.get("tags") or [])
        if t not in _LEGACY_TAG_MAP and t not in consumed_by_detector and t not in ("setup_invalidated",)
    ]
    if leftover_tags:
        existing = list(out.get("tags") or [])
        for t in leftover_tags:
            if t not in existing:
                existing.append(t)
        out["tags"] = existing

    return out


def format_note(note: dict, now: datetime | None = None) -> str:
    """Human-friendly one-liner: '[YYYY-MM-DD] note (expires in Xd)'."""
    if now is None:
        now = now_utc()
    added = (note.get("added") or "")[:10]
    exp = note.get("expires")
    if exp:
        try:
            days = (datetime.fromisoformat(exp) - now).days
            if days >= 0:
                exp_str = f"expires in {days}d"
            else:
                exp_str = f"expired {-days}d ago"
        except ValueError:
            exp_str = f"expires {exp}"
    else:
        exp_str = "no expiry"
    return f"[{added}] {note['note']} ({exp_str})"


def validate_entry_findings(entry: dict) -> list[Finding]:
    """Return findings (errors + warnings) for a single note entry.

    Errors are hard failures (structural problems, wrong types).
    Warnings are advisory (unrecognised but valid string values, unknown
    top-level ``price_refs`` keys that should migrate into ``extra``).
    """
    findings: list[Finding] = []

    if not isinstance(entry, dict):
        findings.append(Finding("error", "entry must be an object"))
        return findings

    canonical = migrate_entry(dict(entry)) if isinstance(entry.get("meta"), dict) else entry

    if "note" not in canonical or not isinstance(canonical["note"], str) or not canonical["note"].strip():
        findings.append(Finding("error", "missing or empty 'note'"))
    if "added" not in canonical:
        findings.append(Finding("error", "missing 'added'"))

    for fld, recommended in (("status", STATUSES), ("type", TYPES), ("state", STATES)):
        v = canonical.get(fld)
        if v is None:
            continue
        if not isinstance(v, str):
            findings.append(Finding("error", f"{fld} must be a string, got {type(v).__name__}"))
        elif v not in recommended:
            findings.append(
                Finding(
                    "warning",
                    f"{fld}='{v}' is not in the recommended vocabulary "
                    f"{sorted(recommended)} — allowed, but consider widening/documenting",
                )
            )

    pr = canonical.get("price_refs")
    if pr is not None:
        if not isinstance(pr, dict):
            findings.append(Finding("error", "price_refs must be a dict"))
        else:
            extra = pr.get("extra")
            if extra is not None and not isinstance(extra, dict):
                findings.append(Finding("error", f"price_refs.extra must be a dict, got {type(extra).__name__}"))
            for k, v in pr.items():
                if k == "extra":
                    continue
                if k not in _CANONICAL_PRICE_REFS:
                    findings.append(
                        Finding(
                            "warning",
                            f"price_refs: unknown key '{k}' — move non-canonical refs into price_refs.extra",
                        )
                    )
                    continue
                if v is not None and not isinstance(v, (int, float)):
                    findings.append(Finding("error", f"price_refs.{k} must be numeric"))

    deps = canonical.get("dependencies")
    if deps is not None and not isinstance(deps, list):
        findings.append(Finding("error", "dependencies must be a list"))

    tags = canonical.get("tags")
    if tags is not None and not isinstance(tags, list):
        findings.append(Finding("error", "tags must be a list"))

    if "meta" in entry:
        findings.append(Finding("error", "legacy 'meta' field present — run `migrate` to normalise to typed fields"))

    return findings


def validate_storage_findings(data: dict) -> list[Finding]:
    """Validate a full notes storage dict. Returns findings (errors + warnings)."""
    findings: list[Finding] = []
    if not isinstance(data, dict):
        findings.append(Finding("error", "root must be a dict mapping pair -> list of notes"))
        return findings
    for pair, notes in data.items():
        if not isinstance(notes, list):
            findings.append(Finding("error", f"{pair}: value must be a list"))
            continue
        for i, n in enumerate(notes):
            for f in validate_entry_findings(n):
                findings.append(Finding(f.severity, f"{pair}[{i}]: {f.message}"))
    return findings


def validate_entry(entry: dict) -> list[str]:
    """Return list of validation errors only (empty == valid).

    Backward-compatible accessor: returns only error messages, never warnings.
    ``add_note`` and ``migrate`` treat a non-empty return as fatal.
    Use ``validate_entry_findings`` when you need to distinguish warnings.
    """
    return [f.message for f in validate_entry_findings(entry) if f.severity == "error"]


def validate_storage(data: dict) -> list[str]:
    """Validate a full notes storage dict. Returns list of errors only."""
    return [f.message for f in validate_storage_findings(data) if f.severity == "error"]
