"""analysis.output - AXI envelope, field projection, content truncation, home view.

This module owns the on-the-wire output contract adopted in
ADR-0004. It is additive to analysis.formatting: emit_json,
parse_args, print_header, require_ticker, safe_round, render_notes
stay in formatting.py; the AXI-specific envelope, projection,
truncation, and home-view helpers live here.

The lib.py contracts (L1Result / L2Result / L3Result / L3Idea /
RegimeSignal / RiskVerdict / FillConfirmation / Intent) are not
rewritten by this module - they describe in-process shapes; this
module describes the on-the-wire envelope.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_TRUNCATE_LIMIT = 80
"""Default per-field truncation budget for narrative / thesis strings."""

_HOME_VIEW_DIRNAME = "market-skills"
_HOME_VIEW_SUFFIX = "_last.json"
_HOME_VIEW_FALLBACK = "no cached state yet - run `{cmd}` to populate this view, or pass `--help` to see usage."


def _state_cache_path(skill_name: str) -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, _HOME_VIEW_DIRNAME, f"{skill_name}{_HOME_VIEW_SUFFIX}")


def skill_name_from_file(file_path: str) -> str:
    """Derive the skill name from a ``skills/<name>/scripts/<file>.py`` path.

    Falls back to the file stem when the path does not live under a
    ``skills/`` tree so non-skill helpers can still call the
    home-view utilities without crashing.
    """
    parts = Path(file_path).parts
    for i, p in enumerate(parts):
        if p == "skills" and i + 2 < len(parts) and parts[i + 2] == "scripts":
            return parts[i + 1]
    return Path(file_path).stem


def _age_human(iso_ts: str) -> str:
    try:
        then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    delta = datetime.now(UTC) - then
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def project_fields(d: Any, fields: Iterable[str] | str | None) -> Any:
    """Return a copy of `d` containing only the requested fields.

    `fields` may be:
      None / "" / []            -> return `d` unchanged.
      "all"                     -> return `d` unchanged.
      a comma-separated string  -> split on commas, strip, drop empties.
      a list / tuple of strings -> use as-is.
    Unknown fields are silently skipped so a caller can request
    `--fields=a,b,c` on a payload that doesn't carry every key.
    Non-dict inputs are returned unchanged.
    """
    if fields is None:
        return d
    if isinstance(fields, str):
        if fields == "all" or fields == "":
            return d
        fields = [f.strip() for f in fields.split(",") if f.strip()]
        if not fields:
            return d
    if not isinstance(d, dict):
        return d
    return {k: d[k] for k in fields if k in d}


def truncate(text: Any, limit: int | None = DEFAULT_TRUNCATE_LIMIT, hint: bool = True) -> Any:
    """Truncate a string with an optional size hint.

    Returns `text` unchanged when it is None, not a string, or already
    within the limit. The hint follows the AXI convention: a trailing
    `(truncated, N chars total - use --full to see complete body)`.
    """
    if text is None or not isinstance(text, str):
        return text
    if limit is None or len(text) <= limit:
        return text
    if hint:
        suffix = f" ... (truncated, {len(text)} chars total - use --full to see complete body)"
    else:
        suffix = " ..."
    return text[:limit] + suffix


_TOON_BARE_KEY = re.compile(r"^[A-Za-z_][\w.\-]*$")
_TOON_NEEDS_QUOTE_RE = re.compile(r"^(true|false|null)$|^-?\d")
_TOON_VALUE_STRUCTURAL = set('":{}#\n\r\t\\')
_TOON_CELL_STRUCTURAL = set('":{}#,\n\r\t\\')


def _toon_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _toon_quote_value(s: str) -> str:
    """Quote a string when it appears as a dict value (not in an inline array)."""
    if s == "":
        return '""'
    if _TOON_NEEDS_QUOTE_RE.match(s):
        return f'"{_toon_escape(s)}"'
    if any(c in _TOON_VALUE_STRUCTURAL for c in s):
        return f'"{_toon_escape(s)}"'
    return s


def _toon_quote_cell(s: str) -> str:
    """Quote a string when it appears as an inline array cell (commas are separators)."""
    if s == "":
        return '""'
    if _TOON_NEEDS_QUOTE_RE.match(s):
        return f'"{_toon_escape(s)}"'
    if any(c in _TOON_CELL_STRUCTURAL for c in s):
        return f'"{_toon_escape(s)}"'
    return s


def _toon_is_uniform_object_list(items: list) -> bool:
    if not items or not all(isinstance(x, dict) for x in items):
        return False
    keys = list(items[0].keys())
    if not keys or not all(_TOON_BARE_KEY.match(k) for k in keys):
        return False
    for x in items:
        if list(x.keys()) != keys:
            return False
        for v in x.values():
            if not (isinstance(v, (str, int, float, bool)) or v is None):
                return False
    return True


def _toon_primitive(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return _toon_quote_value(str(v))


def _toon_encode(obj: Any, indent: int = 2) -> str:
    lines: list[str] = []

    def key_str(k: Any) -> str:
        s = str(k)
        return s if _TOON_BARE_KEY.match(s) else _toon_quote_value(s)

    def emit(value: Any, depth: int) -> None:
        prefix = " " * (indent * depth)
        if isinstance(value, dict):
            if not value:
                return
            for k, v in value.items():
                kk = key_str(k)
                if isinstance(v, dict):
                    if not v:
                        lines.append(f"{prefix}{kk}: {{}}")
                    else:
                        lines.append(f"{prefix}{kk}:")
                        emit(v, depth + 1)
                elif isinstance(v, list):
                    if not v:
                        lines.append(f"{prefix}{kk}: []")
                    elif _toon_is_uniform_object_list(v):
                        keys = list(v[0].keys())
                        header = ",".join(keys)
                        lines.append(f"{prefix}{kk}[{len(v)},]{{{header}}}:")
                        row_prefix = prefix + " " * indent
                        for row in v:
                            cells = []
                            for k in keys:
                                rv = row[k]
                                if rv is None:
                                    cells.append("null")
                                elif isinstance(rv, bool):
                                    cells.append("true" if rv else "false")
                                elif isinstance(rv, (int, float)):
                                    cells.append(str(rv))
                                else:
                                    cells.append(_toon_quote_cell(str(rv)))
                            lines.append(row_prefix + ",".join(cells))
                    elif all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                        cells = []
                        for x in v:
                            if x is None:
                                cells.append("null")
                            elif isinstance(x, bool):
                                cells.append("true" if x else "false")
                            elif isinstance(x, (int, float)):
                                cells.append(str(x))
                            else:
                                cells.append(_toon_quote_cell(str(x)))
                        lines.append(f"{prefix}{kk}[{len(v)}]: {','.join(cells)}")
                    else:
                        lines.append(f"{prefix}{kk}[{len(v)}]:")
                        for item in v:
                            if isinstance(item, dict):
                                lines.append(f"{prefix}{' ' * indent}- ")
                                inner = prefix + " " * indent + "  "
                                for kk2, vv in item.items():
                                    kk3 = key_str(kk2)
                                    if isinstance(vv, (str, int, float, bool)) or vv is None:
                                        lines[-1] = lines[-1] + f"{kk3}: {_toon_primitive(vv)} "
                                    else:
                                        lines.append(f"{inner}{kk3}:")
                                        emit(vv, depth + 2)
                            else:
                                lines.append(f"{prefix}{' ' * indent}- {_toon_primitive(item)}")
                else:
                    lines.append(f"{prefix}{kk}: {_toon_primitive(v)}")
        else:
            lines.append(prefix + _toon_primitive(value))

    emit(obj, 0)
    return "\n".join(lines) + "\n"


def toon_dump(obj: Any) -> str:
    """Serialize `obj` for the AXI on-the-wire path as TOON.

    TOON (Token-Oriented Object Notation) is a compact, YAML-flavoured
    encoding designed for LLM consumption. Round-trips via
    :func:`toon_load`. Off by default — the `--toon` flag in
    :func:`parse_axi_flags` opts the caller in.

    This is a hand-rolled minimal encoder scoped to the AXI envelope
    shapes (top-level dict, nested dicts, lists of strings, lists of
    uniform objects, primitives). It does not depend on any external
    library. Measured byte savings on representative envelopes
    (indent-2 JSON vs TOON):

      - 22-28% smaller for envelopes with primitive-only payloads
        (e.g. market-macro, market-valuation).
      - 35-78% smaller for envelopes with lists of uniform objects
        (e.g. strategy-trend-follow ideas, conviction-scan rows) —
        the tabular CSV row format compresses repeated keys.

    The tabular path fires when the list is a value of a dict key
    (the canonical envelope shape ``{"data": {"ideas": [...]}}``).
    A top-level list of dicts (e.g. ``toon_dump(some_list)`` directly)
    falls through to Python repr and gets no compression — pass the
    list as the value of a key instead.

    The format follows the TOON spec at
    <https://github.com/toon-format/spec>: ``key: value`` for objects,
    ``[N]: v1,v2,v3`` for inline primitive arrays, ``[N,]{f1,f2}:``
    followed by CSV rows for tabular arrays. Strings are quoted only
    when they contain structural characters (``: " , \\n\\r\\t [ ] { }``)
    or look like a number, a keyword, or an empty string.
    """
    return _toon_encode(obj)


_TOON_KEY_ARRAY = re.compile(r"^(.+?)\[(\d+),?\](?:\{([^}]*)\})?$")


def toon_load(text: str) -> Any:
    """Decode a TOON string produced by :func:`toon_dump`.

    The decoder is intentionally minimal — it supports the same shapes
    the encoder emits (the AXI envelope patterns). For general
    fallback use `json.loads` on the JSON form instead.
    """
    if not text.strip():
        return None
    raw_lines = text.split("\n")
    lines = [ln for ln in raw_lines if ln.strip()]
    pos = [0]

    def line_indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def split_csv(s: str) -> list[str]:
        out: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            if s[i].isspace():
                i += 1
                continue
            if s[i] == ",":
                i += 1
                continue
            if s[i] == '"':
                j = i + 1
                buf = []
                while j < n:
                    if s[j] == "\\" and j + 1 < n:
                        esc = s[j + 1]
                        buf.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}.get(esc, esc))
                        j += 2
                    elif s[j] == '"':
                        break
                    else:
                        buf.append(s[j])
                        j += 1
                out.append("".join(buf))
                i = j + 1
            else:
                j = i
                while j < n and s[j] != ",":
                    j += 1
                out.append(s[i:j].strip())
                i = j
        return out

    def parse_primitive(s: str) -> Any:
        s = s.strip()
        if s == "" or s == "null":
            return None
        if s == "true":
            return True
        if s == "false":
            return False
        if s.startswith('"') and s.endswith('"'):
            inner = s[1:-1]
            i = 0
            res = []
            while i < len(inner):
                if inner[i] == "\\" and i + 1 < len(inner):
                    c = inner[i + 1]
                    res.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(c, c))
                    i += 2
                else:
                    res.append(inner[i])
                    i += 1
            return "".join(res)
        if s == "{}":
            return {}
        if s == "[]":
            return []
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    def parse_inline_array(rest: str) -> list:
        return [parse_primitive(c) for c in split_csv(rest)]

    def parse_key(raw_key: str) -> tuple[str, int | None, list[str] | None]:
        """Parse a key like `errors[2]` or `ideas[2,]{a,b}`.

        Returns (key, length, fields). `length` is the declared
        array length when the key carries an array header, else None.
        `fields` is the field-name list for tabular arrays, else None.
        """
        m = _TOON_KEY_ARRAY.match(raw_key)
        if not m:
            return raw_key, None, None
        key, length_str, fields_str = m.group(1), m.group(2), m.group(3)
        length = int(length_str) if length_str else None
        fields = [f.strip() for f in fields_str.split(",")] if fields_str else None
        return key, length, fields

    def read_block(depth: int) -> dict:
        out: dict = {}
        while pos[0] < len(lines):
            line = lines[pos[0]]
            ind = line_indent(line)
            if ind < depth:
                break
            if ind > depth:
                raise ValueError(f"unexpected indent at line {pos[0] + 1}: {line!r}")
            content = line.strip()
            if ":" not in content:
                raise ValueError(f"expected ':' at line {pos[0] + 1}: {line!r}")
            colon = content.index(":")
            raw_key = content[:colon].strip()
            if raw_key.startswith('"') and raw_key.endswith('"'):
                raw_key = parse_primitive(raw_key)
            key, length, fields = parse_key(raw_key)
            rest = content[colon + 1 :].lstrip()
            pos[0] += 1
            if length is not None and fields is not None:
                rows: list[dict] = []
                while pos[0] < len(lines) and line_indent(lines[pos[0]]) > depth:
                    cells = split_csv(lines[pos[0]].strip())
                    if not cells or all(c == "" for c in cells):
                        pos[0] += 1
                        continue
                    rows.append({f: parse_primitive(c) for f, c in zip(fields, cells)})
                    pos[0] += 1
                out[key] = rows
                continue
            if length is not None:
                out[key] = parse_inline_array(rest) if rest else []
                continue
            if not rest:
                if pos[0] < len(lines) and line_indent(lines[pos[0]]) > depth:
                    out[key] = read_block(line_indent(lines[pos[0]]))
                else:
                    out[key] = {}
                continue
            out[key] = parse_primitive(rest)
        return out

    return read_block(0)


def envelope(
    data: Any,
    *,
    count: int | None = None,
    help: Iterable[str] | None = None,
    errors: Iterable[str] | None = None,
    fields: Iterable[str] | str | None = None,
) -> dict[str, Any]:
    """Wrap a payload in the canonical AXI envelope.

    Shape: ``{data, count, errors, help[]}``. `data` is the
    skill-specific payload (already projected when `fields` is set).
    `count` is the canonical item count - skills return 1 for
    singletons, N for lists. `errors` and `help` are always lists
    (empty when unset) so consumers never branch on None.
    """
    projected = project_fields(data, fields) if fields is not None else data
    return {
        "data": projected,
        "count": count,
        "errors": list(errors) if errors else [],
        "help": list(help) if help else [],
    }


def emit_envelope_json(
    data: Any,
    *,
    count: int | None = None,
    help: Iterable[str] | None = None,
    errors: Iterable[str] | None = None,
    fields: Iterable[str] | str | None = None,
    toon: bool = False,
) -> None:
    """Print the AXI envelope to stdout.

    `toon=False` (default) emits indent-2 JSON. `toon=True` routes
    through the hand-rolled TOON encoder (`toon_dump`); opt-in per
    ADR-0004 phase 5.
    """
    payload = envelope(data, count=count, help=help, errors=errors, fields=fields)
    if toon:
        print(toon_dump(payload))
    else:
        print(json.dumps(payload, indent=2, default=str))


def _read_state_cache(skill_name: str) -> dict[str, Any] | None:
    path = _state_cache_path(skill_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def render_home_view(skill_name: str, *, command_hint: str | None = None) -> str:
    """Render the no-arg home view from the last-cached state.

    Falls back to a one-line "no cached state" message when the
    per-skill cache is empty or unreadable. The default
    `command_hint` is ``<skill-name> --json`` so a fresh install
    still gives the LLM a concrete next step.
    """
    state = _read_state_cache(skill_name)
    hint = command_hint or f"{skill_name} --json"
    if not state:
        return _HOME_VIEW_FALLBACK.format(cmd=hint)
    summary = state.get("summary") or state.get("narrative") or state.get("ticker") or state.get("regime")
    ts = state.get("cached_at") or state.get("timestamp") or state.get("last_run")
    body = "last cached state"
    if summary:
        body += f": {summary}"
    if ts:
        body += f" on {ts}"
        age = _age_human(ts)
        if age:
            body += f" ({age})"
    return f"{body}\n  try: `{hint}`"


def write_state_cache(skill_name: str, payload: dict[str, Any]) -> None:
    """Write the last-cached state for a skill's home view.

    Best-effort: a write failure is silent (e.g. read-only
    filesystem, permission denied) because the home view is a
    nice-to-have, not a hard contract.
    """
    path = _state_cache_path(skill_name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except OSError:
        return


def cache_run_result(script_file: str, result: dict[str, Any] | None) -> None:
    """Cache a skill's run result for the home view.

    Adds a ``cached_at`` ISO timestamp and writes via
    :func:`write_state_cache`. Skips silently when ``result`` is
    ``None`` or contains an ``"error"`` key (errors are not state).
    The skill name is derived from ``script_file`` via
    :func:`skill_name_from_file`; pass an explicit skill name as
    the first positional if you need to override.
    """
    if script_file and "/" not in script_file and "\\" not in script_file:
        skill_name = script_file
        payload = result
    else:
        skill_name = skill_name_from_file(script_file)
        payload = result
    if not payload or "error" in payload:
        return
    stamped = {"cached_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), **payload}
    write_state_cache(skill_name, stamped)


def maybe_render_home_view(
    script_file: str,
    ticker: str | None,
    json_mode: bool,
) -> bool:
    """Render the per-skill home view when no ticker (or no positional) was given.

    Returns ``True`` when the home view was emitted and the caller
    should ``return`` from ``main()``; returns ``False`` when a
    ticker was given (caller proceeds with the normal analyze path).

    Behaviour by mode:

    - **ticker present** -> return False (do not render).
    - **JSON mode + no ticker** -> emit an :func:`empty_state` envelope
      (one ``error``, one ``help`` line pointing at the next command)
      and return True. The LLM gets a structured response it can
      branch on without a usage-exit fork.
    - **text mode + no ticker** -> emit :func:`render_home_view`
      (last-cached state, or the standard "no cached state" fallback)
      and return True.

    The skill name is derived from ``script_file`` via
    :func:`skill_name_from_file`.
    """
    if ticker:
        return False
    skill_name = skill_name_from_file(script_file)
    if json_mode:
        print_envelope(
            empty_state(
                errors=[f"no ticker provided for {skill_name}"],
                help=[
                    f"Run `{skill_name} <TICKER> --json` to populate this view",
                    f"Run `{skill_name} --help` for full usage",
                ],
            )
        )
        return True
    print(render_home_view(skill_name), file=sys.stdout)
    return True


def empty_state(*, help: Iterable[str] | None = None, errors: Iterable[str] | None = None) -> dict[str, Any]:
    """Return the canonical zero-result envelope (AXI principle 5).

    Use :func:`print_envelope` to emit the result, not
    :func:`emit_envelope_json` — the latter would re-wrap this
    envelope in a ``data`` key.
    """
    return {
        "data": None,
        "count": 0,
        "errors": list(errors) if errors else [],
        "help": list(help) if help else [],
    }


def print_envelope(env: dict[str, Any], *, file: Any = None) -> None:
    """Print a pre-built envelope to stdout (or another file).

    Use this when the envelope is already constructed (e.g. via
    :func:`empty_state`) and only needs serialisation. For the
    data-happy path, use :func:`emit_envelope_json` which builds
    + prints in one call.
    """
    out = json.dumps(env, indent=2, default=str)
    if file is None:
        print(out)
    else:
        print(out, file=file)


def parse_axi_flags(argv: list[str]) -> tuple[Any, bool, bool, list[str]]:
    """Extract AXI-specific flags from argv.

    Returns ``(fields, full, toon, filtered_argv)``. `fields` is the
    raw `--fields=` value (string) or None; `full` is True when
    `--full` is set; `toon` is True when `--toon` is set;
    `filtered_argv` is the input argv with the AXI flags stripped, so
    the caller can pass it to ``safe_parse_args`` without tripping
    the "unrecognized flag" check. Recognised AXI flags: ``--full``,
    ``--fields=`` / ``--fields <value>``, ``--toon``.
    """
    fields: Any = None
    full = False
    toon = False
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--full":
            full = True
            i += 1
        elif a == "--toon":
            toon = True
            i += 1
        elif a.startswith("--fields="):
            fields = a.split("=", 1)[1]
            i += 1
        elif a == "--fields":
            if i + 1 >= len(argv):
                raise ValueError("--fields requires a value")
            fields = argv[i + 1]
            i += 2
        else:
            out.append(a)
            i += 1
    return fields, full, toon, out


def resolve_fields(
    fields_arg: Any,
    *,
    full: bool,
    default: list[str] | None = None,
) -> Any:
    """Resolve the three-way field-selection decision.

    Precedence: ``--full`` (full payload) > ``--fields=`` (user
    projection) > ``default`` (per-skill minimal schema) > None
    (full payload). The returned value feeds straight into
    ``envelope(..., fields=...)``: None means "no projection, ship
    the whole payload"; a list/string means "project to these
    keys".
    """
    if full:
        return None
    if fields_arg is not None:
        return fields_arg
    if default is not None:
        return default
    return None
