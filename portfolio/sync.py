"""Derive the position-watchdog held file (open-positions) from the ledger.

The held file that drives ``position-watchdog --config`` is hand-authored;
nothing derived it from the ledger, so it drifted every time a position
was sold (a dead entry kept monitoring stops against a position that no
longer exists, while a real position with no entry went unwatched). This
module re-grounds the file against the portfolio SQLite ledger — the
source of truth for what is actually held.

Source of truth is the LEDGER, never the venue. Per the 2026-09-18
convention the ledger records *decisions* and the venue records
*reality*; they are expected to diverge. Nothing in this module makes a
network call: reference prices come from ``--price-override`` values or
the ledger-local ``price_cache`` table only.

What is derived — and nothing more:

- **Membership**: an enabled watch whose ledger net is flat
  (``abs(net) <= FLAT_EPSILON``) is removed from the file and reported as
  a *watchlist candidate* (its zones must be re-grounded there; deleting
  a watch outright is the ZEC failure mode). A **negative** ledger net is
  not flat — a perps short (recorded as SELL on the same
  ``kraken:<PAIR>`` key) or an over-sold residue — so the watch is kept
  byte-identical and reported under ``negative_net``. Unmatched /
  ambiguous watches are kept untouched and reported.
- **``position_size``**: set to ``round(net, POSITION_SIZE_DECIMALS)`` for
  every surviving matched enabled watch with a positive net. POSITION_SIZE_DECIMALS is 8 —
  the held file's quantity precision (the reference ETH case expects
  ``0.03815984``, which is exactly ``round(0.0381598383, 8)``).
- **``entry_price``**: filled from the ledger FIFO average cost of the
  remaining lots **only when the entry has none (missing or null)**. A
  hand-authored ``entry_price`` is a decision reference that anchors the
  watchdog's drop/recovery math — it is NEVER overwritten, even when it
  disagrees with the ledger average (e.g. a staking reward priced at 0
  drags the ledger average below the hand-authored entry).

Everything else in an entry — ``levels``, zones, ``_comment_*``,
``format_style``, ``signals``, ``monitor_provider``, ``interval``,
``period`` — is hand-authored and preserved byte-identical. A sync that
regenerates zones would be worse than the drift it fixes.

Every correction is auditable: changed entries get (or extend) a
``_comment_ledger_sync`` note recording old -> new with a UTC timestamp.
Writes are **spliced, not re-dumped**: the raw text is re-parsed with
``json.JSONDecoder().raw_decode`` to locate the top-level ``watches``
array and the byte span of each element; untouched elements are copied
back as their exact original bytes (inline ``levels`` formatting and all
hand-authored fields survive byte-identically), a corrected element gets
only its changed **value tokens** spliced into its original raw bytes —
the ``position_size`` number, a newly inserted ``entry_price`` member,
and the ``_comment_ledger_sync`` string — so ``levels``/``signals``
literals such as ``500.00`` never normalize; a whole-element
re-serialization happens only as a recorded fallback for THAT element,
and a whole-document ``json.dumps`` only when the raw array cannot be
located at all (also recorded in the report ``notes``).

The sync is idempotent — when nothing changed the file is not written at
all (mtime untouched). Writes are atomic (sibling temp file +
``os.replace``).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from typing import Any

from portfolio.db import (
    compute_fifo,
    compute_positions,
    get_cached_prices,
    get_portfolio,
    list_portfolios,
    list_transactions,
)

ENV_OPEN_POSITIONS_PATH = "MARKET_SKILLS_OPEN_POSITIONS_PATH"
"""Env var naming the position-watchdog held file (open-positions JSON).

No host-specific default: when it is unset and no ``--config PATH`` was
given, :func:`resolve_config_path` raises :class:`OSError` naming the env
var (see AGENTS.md "What to avoid"). A CLI ``--config`` wins over the env
var."""

FLAT_EPSILON = 1e-6
"""Ledger net at or below this magnitude counts as flat (no holding).

Float residue from an exactly-flat position (e.g. net ``-3.0e-09``) must
not read as a holding."""

POSITION_SIZE_DECIMALS = 8
"""The held file's quantity precision.

The reference ETH case expects ``0.03815984``, which is exactly
``round(0.0381598383, 8)`` — the staking accrual reflected to the file's
precision."""

STALE_ZONE_MIN_DISTANCE_PCT = 25.0
"""A zone level counts as stale when it sits more than this percent
below the reference price. Stale levels are flagged, never rewritten —
re-ground, do not prune."""

_AUDIT_KEY = "_comment_ledger_sync"

_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "EUR")


def resolve_config_path(cli_path: str | None = None) -> str:
    """Resolve the held-file path: CLI argument wins, else the env var.

    Raises :class:`OSError` naming the env var when neither is present —
    the library deliberately does not fall back to a host-specific path
    (mirror of ``default_db_path()`` in ``skills/portfolio-mgmt/lib.py``).
    """
    if cli_path:
        return cli_path
    path = os.environ.get(ENV_OPEN_POSITIONS_PATH)
    if not path:
        raise OSError(
            f"{ENV_OPEN_POSITIONS_PATH} is not set; cannot resolve the "
            "position-watchdog open-positions held-file path. Set the "
            "env var to point at your open-positions JSON config, or "
            "pass --config PATH to override for a single invocation."
        )
    return path


def _render_num(value: Any) -> str:
    """Render a JSON scalar for audit notes / human diffs.

    Numbers render as floats (``3417`` -> ``3417.0``) so old -> new lines
    match the ledger-derived precision regardless of how the value was
    stored in the file.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return repr(float(value))
    return str(value)


def _normalize_ticker(raw: str) -> str:
    """``provider:TICKER-EUR`` -> ``TICKER`` (for fuzzy watch matching)."""
    bare = raw.split(":", 1)[-1]
    out = bare.upper().replace("-", "").replace("/", "")
    for suffix in _QUOTE_SUFFIXES:
        if out.endswith(suffix) and len(out) > len(suffix):
            return out[: -len(suffix)]
    return out


def _resolve_portfolio_id(db_path: str, portfolio: int | str | None) -> int | None:
    if portfolio is None:
        return None
    if isinstance(portfolio, int):
        return portfolio
    pf = get_portfolio(db_path, portfolio)
    if pf is None:
        raise ValueError(f"portfolio '{portfolio}' not found in {db_path}")
    return int(pf["id"])


def _ledger_assets(db_path: str, portfolio_id: int | None) -> dict[str, list[dict]]:
    """Per-asset portfolio entries with FIFO net quantity.

    Returns ``{asset_key: [{"pid": int, "net": float}, ...]}`` covering
    every portfolio entry that has transactions — held (net >
    FLAT_EPSILON) and flat alike, so a watch can be matched to a
    just-sold position and removed.
    """
    rows = list_transactions(db_path, portfolio_id=portfolio_id)
    fifo = compute_fifo(rows)
    assets: dict[str, list[dict]] = {}
    for key in set(fifo["total_bought_qty"]) | set(fifo["total_sold_qty"]):
        pid, asset = key
        net = fifo["total_bought_qty"].get(key, 0.0) - fifo["total_sold_qty"].get(key, 0.0)
        assets.setdefault(asset, []).append({"pid": pid, "net": net})
    return assets


def _find_ledger_match(
    watch: dict, assets: dict[str, list[dict]]
) -> tuple[str, str | None, int | None, float | None, str]:
    """Match a watch's ``monitor_provider`` against ledger asset keys.

    (a) Exact asset-key match wins. (b) Otherwise compare the normalized
    bare ticker (drop the ``provider:`` prefix, drop a trailing
    USD/USDT/USDC/EUR quote suffix, uppercase, strip ``-``/``/``) and
    accept only when exactly one ledger asset normalizes to it — that
    asset's net is then classified exactly like the exact-key match
    (held / flat / negative / multi-portfolio), so a flat cross-provider
    watch is removed and a negative one is reported.

    Returns ``(status, asset, pid, net, reason)`` with status one of
    ``matched`` / ``unmatched`` / ``ambiguous``.
    """
    monitor = watch.get("monitor_provider") or ""
    if monitor in assets:
        entries = assets[monitor]
        held = [e for e in entries if e["net"] > FLAT_EPSILON]
        if len(held) > 1:
            return "ambiguous", monitor, None, None, f"asset '{monitor}' is held in more than one portfolio"
        if len(held) == 1:
            return "matched", monitor, held[0]["pid"], held[0]["net"], ""
        if len(entries) == 1:
            return "matched", monitor, entries[0]["pid"], entries[0]["net"], ""
        return "ambiguous", monitor, None, None, f"asset '{monitor}' appears in more than one portfolio"

    want = _normalize_ticker(monitor)
    if not want:
        return "unmatched", monitor, None, None, f"no ledger asset matches monitor_provider '{monitor}'"
    candidates = sorted(k for k in assets if _normalize_ticker(k) == want)
    if len(candidates) > 1:
        joined = ", ".join(candidates)
        return (
            "ambiguous",
            monitor,
            None,
            None,
            f"{len(candidates)} ledger assets normalize to '{want}': {joined}",
        )
    if candidates:
        asset = candidates[0]
        held = [e for e in assets[asset] if e["net"] > FLAT_EPSILON]
        if len(held) > 1:
            return "ambiguous", asset, None, None, f"asset '{asset}' is held in more than one portfolio"
        if len(held) == 1:
            return "matched", asset, held[0]["pid"], held[0]["net"], ""
        if len(assets[asset]) == 1:
            return "matched", asset, assets[asset][0]["pid"], assets[asset][0]["net"], ""
        return "ambiguous", asset, None, None, f"asset '{asset}' appears in more than one portfolio"
    return "unmatched", monitor, None, None, f"no ledger asset matches monitor_provider '{monitor}'"


def _claimed_ledger_keys(watch: dict, assets: dict[str, list[dict]]) -> set[str]:
    """Ledger asset keys the watch names or normalizes to.

    Enabled, disabled, ambiguous and matched watches alike claim their
    ``monitor_provider``'s ledger key, so the ``unwatched`` report does
    not re-list an asset a watch already tracks: the human report prints
    the disabled/ambiguous watch on its own line, and a duplicate
    ``! unwatched`` line for the same asset invites a duplicate watch
    entry.
    """
    monitor = watch.get("monitor_provider") or ""
    if monitor in assets:
        return {monitor}
    norm = _normalize_ticker(monitor)
    return {k for k in assets if _normalize_ticker(k) == norm}


def _resolve_price(
    asset: str,
    monitor_provider: str,
    price_overrides: dict[str, float],
    price_cache: dict[str, float],
) -> tuple[float | None, str | None]:
    """Reference price for the stale-zone check — ledger-local only.

    ``--price-override`` values first, then the ledger-local
    ``price_cache`` (keyed by the ledger asset key and by
    ``monitor_provider``). Never a venue fetch.
    """
    for source, mapping in (("price-override", price_overrides), ("price_cache", price_cache)):
        for key in (asset, monitor_provider):
            if key and key in mapping:
                return float(mapping[key]), source
    return None, None


def _stale_zone_report(name: str, levels: list, price: float, price_source: str) -> dict | None:
    """Flag an entry whose every zone sits > STALE_ZONE_MIN_DISTANCE_PCT
    below the reference price. Levels are never rewritten."""
    per_zone: list[dict] = []
    for lv in levels:
        if not isinstance(lv, dict) or lv.get("type") != "zone":
            continue
        bound = lv.get("high")
        if bound is None:
            bound = lv.get("low")
        if bound is None or price <= 0:
            continue
        distance_pct = (bound - price) / price * 100
        per_zone.append(
            {
                "low": lv.get("low"),
                "high": lv.get("high"),
                "label": lv.get("label"),
                "distance_pct": round(distance_pct, 1),
            }
        )
    if not per_zone:
        return None
    if not all(z["distance_pct"] < -STALE_ZONE_MIN_DISTANCE_PCT for z in per_zone):
        return None
    return {
        "name": name,
        "price": price,
        "price_source": price_source,
        "distance_pct": min(z["distance_pct"] for z in per_zone),
        "zones": per_zone,
    }


def _detect_json_style(text: str) -> dict:
    """Detect indent width, ascii mode, and trailing newline from the
    original file text so re-serialization round-trips byte-identically
    for untouched entries and untouched regions.

    Raw non-ASCII in the text (raw emoji, non-ASCII punctuation) means
    the file was written with ``ensure_ascii=False``; all-ASCII text is
    indistinguishable from an escaped write, so the default stays
    ``ensure_ascii=True``.
    """
    matches = re.search(r"\n( +)", text)
    return {
        "indent": len(matches.group(1)) if matches else 2,
        "ensure_ascii": text.isascii(),
        "trailing_newline": text.endswith("\n"),
    }


def _serialize(doc: dict, style: dict) -> str:
    text = json.dumps(doc, indent=style["indent"], ensure_ascii=style["ensure_ascii"])
    if style["trailing_newline"]:
        text += "\n"
    return text


def _locate_watches_array(text: str) -> tuple[int, int, list] | None:
    """Locate the top-level ``"watches"`` array in the raw text.

    Walks the top-level object with ``json.JSONDecoder().raw_decode`` so
    nested braces and escaped strings are handled by the stdlib parser
    (never a hand-rolled scanner). Returns ``(array_start, array_end,
    parsed_elements)`` — ``array_end`` is just past the closing ``]`` —
    or ``None`` when no parseable top-level ``watches`` array exists.
    """
    dec = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "{":
        return None
    i += 1
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] == "}":
            return None
        if text[i] != '"':
            return None
        try:
            key, i = dec.raw_decode(text, i)
        except ValueError:
            return None
        if not isinstance(key, str):
            return None
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != ":":
            return None
        i += 1
        while i < n and text[i].isspace():
            i += 1
        if key == "watches" and i < n and text[i] == "[":
            try:
                parsed, arr_end = dec.raw_decode(text, i)
            except ValueError:
                return None
            if not isinstance(parsed, list):
                return None
            return i, arr_end, parsed
        try:
            _value, i = dec.raw_decode(text, i)
        except ValueError:
            return None
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == ",":
            i += 1
            continue
        return None


def _element_spans(text: str, arr_start: int) -> list[tuple[int, int]] | None:
    """Byte spans ``(start, end)`` of each raw element of the array whose
    ``[`` sits at ``arr_start``.

    Elements are walked with ``raw_decode`` (commas and whitespace are
    skipped between them), so each span is exactly the element's own raw
    bytes — including any inline one-line formatting.
    """
    dec = json.JSONDecoder()
    i, n = arr_start + 1, len(text)
    spans: list[tuple[int, int]] = []
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return None
        if text[i] == "]":
            return spans
        try:
            _value, end = dec.raw_decode(text, i)
        except ValueError:
            return None
        spans.append((i, end))
        i = end
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return None
        if text[i] == ",":
            i += 1
            continue
        if text[i] == "]":
            return spans
        return None


def _reserialize_element(elem: dict, text: str, start: int, style: dict) -> str:
    """Re-serialize one mutated element in the file's detected style,
    re-indented to the element's original column.

    Only used as a per-element fallback when the value-level splice in
    :func:`_value_splice_element` cannot locate an unambiguous token to
    patch — recorded in the report ``notes``, never silent."""
    s = json.dumps(elem, indent=style["indent"], ensure_ascii=style["ensure_ascii"])
    nl = text.rfind("\n", 0, start)
    col = start - nl - 1 if nl != -1 else start
    if "\n" in s and col > 0:
        s = s.replace("\n", "\n" + " " * col)
    return s


def _member_spans(raw: str) -> list[dict] | None:
    """Locate each top-level member's value token inside one raw element.

    Walks the element with ``json.JSONDecoder().raw_decode`` (never a
    regex, never a string search on the member name alone) and returns a
    list of ``{"key", "vstart", "vend", "comma", "next_start"}`` in
    source order: ``comma`` is the index of the comma following the
    value (``None`` for the last member) and ``next_start`` is where the
    next member's key begins (or the closing ``}`` sits, past trailing
    whitespace). Returns ``None`` when ``raw`` is not one flat object.
    """
    dec = json.JSONDecoder()
    i, n = 0, len(raw)
    while i < n and raw[i].isspace():
        i += 1
    if i >= n or raw[i] != "{":
        return None
    i += 1
    members: list[dict] = []
    while True:
        while i < n and raw[i].isspace():
            i += 1
        if i >= n:
            return None
        if raw[i] == "}":
            return members
        if raw[i] != '"':
            return None
        kstart = i
        try:
            key, i = dec.raw_decode(raw, i)
        except ValueError:
            return None
        if not isinstance(key, str):
            return None
        while i < n and raw[i].isspace():
            i += 1
        if i >= n or raw[i] != ":":
            return None
        i += 1
        while i < n and raw[i].isspace():
            i += 1
        vstart = i
        try:
            _value, i = dec.raw_decode(raw, i)
        except ValueError:
            return None
        member = {"key": key, "kstart": kstart, "vstart": vstart, "vend": i, "comma": None, "next_start": None}
        members.append(member)
        j = i
        while j < n and raw[j].isspace():
            j += 1
        if j >= n:
            return None
        if raw[j] == ",":
            member["comma"] = j
            j += 1
            while j < n and raw[j].isspace():
                j += 1
            member["next_start"] = j
            i = j
            continue
        if raw[j] == "}":
            member["next_start"] = j
            return members
        return None


def _member_indent(raw: str, kstart: int) -> str:
    """Indentation of the line holding the member whose key starts at
    ``kstart`` (empty for an inline element)."""
    nl = raw.rfind("\n", 0, kstart)
    return raw[nl + 1 : kstart] if nl != -1 else ""


def _append_member_after(raw: str, members: list[dict], anchor_key: str, member_text: str) -> str | None:
    """Insert ``member_text`` (a raw ``"key": value`` pair) as a new
    member immediately after the ``anchor_key`` member, or before the
    closing brace at the anchor's indentation when the anchor is the
    last member. Returns ``None`` when the anchor is missing."""
    anchor = next((m for m in members if m["key"] == anchor_key), None)
    if anchor is None:
        return None
    if anchor["comma"] is not None:
        w2 = raw[anchor["comma"] + 1 : anchor["next_start"]]
        return raw[: anchor["comma"]] + "," + w2 + member_text + "," + w2 + raw[anchor["next_start"] :]
    tail = raw[anchor["vend"] : anchor["next_start"]]
    if "\n" in tail:
        sep = "\n" + _member_indent(raw, anchor["kstart"])
    elif tail:
        sep = tail
    else:
        sep = " "
    return raw[: anchor["vend"]] + "," + sep + member_text + raw[anchor["next_start"] :]


def _append_member_at_end(raw: str, members: list[dict], member_text: str) -> str | None:
    """Append ``member_text`` as the LAST member of the raw element —
    immediately before the closing brace, at the element's member
    indentation (inline separators for an inline element). Returns
    ``None`` only for an element with no members at all."""
    if not members:
        return None
    return _append_member_after(raw, members, members[-1]["key"], member_text)


def _append_position_size_member(raw: str, members: list[dict], token: str) -> str | None:
    """Append ``"position_size": <token>`` as a NEW member of a raw
    element that had none.

    Anchor order mirrors the hand-authored file's member order: after an
    existing ``entry_price`` member, else after ``monitor_provider``,
    else as the last member before the closing brace. Only an element
    with no members at all (which can never reach the splice as a
    matched watch) returns ``None`` — a missing anchor must not send the
    element back to whole-element re-serialization.
    """
    member_text = '"position_size": ' + token
    for anchor_key in ("entry_price", "monitor_provider"):
        work = _append_member_after(raw, members, anchor_key, member_text)
        if work is not None:
            return work
    return _append_member_at_end(raw, members, member_text)


def _value_splice_element(state: dict, raw: str, style: dict) -> str | None:
    """Splice only the changed values into one element's raw bytes.

    ``state`` is the planner-mutated element and ``raw`` its ORIGINAL
    raw text, so the diff between ``json.loads(raw)`` and ``state`` is
    exactly the planned correction. Only three members may differ —
    ``position_size`` (changed in place, or APPENDED as a new member when
    the entry had none), a newly filled ``entry_price`` and
    ``_comment_ledger_sync``; anything else means the raw element does
    not match the plan and the caller must fall back. Every other byte
    of the element (``levels``, zones, ``_comment_*``, ``signals``,
    ``format_style``, ...) is copied through unchanged, so numeric
    literals like ``500.00`` never normalize.
    """
    try:
        old = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(old, dict):
        return None
    old_keys, new_keys = set(old), set(state)
    added = new_keys - old_keys
    if old_keys - new_keys or not added <= {"position_size", "entry_price", _AUDIT_KEY}:
        return None
    changed = {k for k in old_keys & new_keys if old[k] != state[k]}
    if not changed <= {"position_size", "entry_price", _AUDIT_KEY}:
        return None

    work = raw
    if "position_size" in changed:
        members = _member_spans(work)
        if members is None:
            return None
        m = next((mm for mm in members if mm["key"] == "position_size"), None)
        if m is None or isinstance(old["position_size"], bool) or not isinstance(old["position_size"], int | float):
            return None
        work = work[: m["vstart"]] + json.dumps(state["position_size"]) + work[m["vend"] :]
    if "position_size" in added:
        members = _member_spans(work)
        if members is None:
            return None
        work = _append_position_size_member(work, members, json.dumps(state["position_size"]))
        if work is None:
            return None
    if "entry_price" in added or "entry_price" in changed:
        # Only the fill case arises: a present non-null entry_price is
        # never overwritten by the planner.
        members = _member_spans(work)
        if members is None:
            return None
        existing = next((mm for mm in members if mm["key"] == "entry_price"), None)
        token = json.dumps(state["entry_price"])
        if existing is not None:
            work = work[: existing["vstart"]] + token + work[existing["vend"] :]
        else:
            work = _append_member_after(work, members, "position_size", '"entry_price": ' + token)
            if work is None:
                return None
    if _AUDIT_KEY in changed or _AUDIT_KEY in added:
        members = _member_spans(work)
        if members is None:
            return None
        token = json.dumps(state[_AUDIT_KEY], ensure_ascii=style["ensure_ascii"])
        existing = next((mm for mm in members if mm["key"] == _AUDIT_KEY), None)
        if existing is not None:
            work = work[: existing["vstart"]] + token + work[existing["vend"] :]
        else:
            key_token = json.dumps(_AUDIT_KEY, ensure_ascii=style["ensure_ascii"])
            work = _append_member_after(work, members, "position_size", f"{key_token}: {token}")
            if work is None:
                return None
    return work


def _splice_watches(text: str, kept: list, style: dict, element_states: list, notes: list) -> str | None:
    """Splice the planned changes back into the raw file text.

    ``element_states`` mirrors the original ``watches`` array one-to-one:
    ``None`` = untouched (copied as its original raw bytes), a dict = the
    mutated element (only its changed value tokens are spliced into the
    element's original raw bytes; a whole-element re-serialization runs
    as a per-element fallback, recorded in ``notes``), ``"removed"`` =
    dropped. Separators are rebuilt so the output stays valid JSON: the
    gap before the first surviving element loses the comma of any
    removed predecessor, a comma is re-inserted between survivors, and
    the array's original closing whitespace + ``]`` are preserved.

    Returns the new text, or ``None`` when the splice cannot be performed
    (caller falls back to :func:`_serialize`).
    """
    located = _locate_watches_array(text)
    if located is None:
        return None
    arr_start, arr_end, _parsed = located
    spans = _element_spans(text, arr_start)
    if spans is None or len(spans) != len(element_states):
        return None

    survivors: list[tuple[str, str]] = []
    for k, (start, end) in enumerate(spans):
        state = element_states[k]
        if state == "removed":
            continue
        gap_raw = text[spans[k - 1][1] : start] if k else text[arr_start + 1 : start]
        comma = gap_raw.find(",")
        gap = gap_raw[comma + 1 :] if comma != -1 else gap_raw
        if isinstance(state, dict):
            elem_bytes = _value_splice_element(state, text[start:end], style)
            if elem_bytes is None:
                elem_bytes = _reserialize_element(state, text, start, style)
                notes.append(
                    f"{state.get('name') or '?'}: could not splice the corrected values into the "
                    "element's raw bytes — re-serialized that entry (its inline formatting "
                    "was not preserved)"
                )
        else:
            elem_bytes = text[start:end]
        survivors.append((gap, elem_bytes))

    tail = text[spans[-1][1] : arr_end] if spans else text[arr_start + 1 : arr_end]
    parts: list[str] = [text[arr_start : arr_start + 1]]
    for idx, (gap, elem_bytes) in enumerate(survivors):
        if idx:
            parts.append(",")
        parts.append(gap)
        parts.append(elem_bytes)
    parts.append(tail)
    new_text = text[:arr_start] + "".join(parts) + text[arr_end:]

    try:
        if json.loads(new_text).get("watches") != kept:
            return None
    except ValueError:
        return None
    return new_text


def _splice_or_serialize(doc: dict, original_text: str, style: dict, element_states: list, report: dict) -> str:
    """Byte-preserving write text: splice the plan into the raw text;
    fall back to a whole-document re-dump (recorded in ``notes``) when
    the raw ``watches`` array cannot be located."""
    new_text = _splice_watches(original_text, doc.get("watches") or [], style, element_states, report["notes"])
    if new_text is not None:
        return new_text
    report["notes"].append(
        "could not locate the raw top-level 'watches' array for a byte-preserving "
        "splice — re-serialized the whole document (untouched entries lost their "
        "original formatting)"
    )
    return _serialize(doc, style)


def _atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".open-positions-sync-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _plan(
    db_path: str,
    config_path: str,
    *,
    portfolio: int | str | None,
    price_overrides: dict[str, float] | None,
    now: datetime | None,
) -> tuple[dict, dict, dict, bool, str, list]:
    """Pure planner: read config + ledger, mutate the parsed doc in
    memory, return ``(report, doc, style, changed, original_text,
    element_states)``."""
    overrides = dict(price_overrides or {})
    now_dt = now or datetime.now(UTC)
    now_ts = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    portfolio_id = _resolve_portfolio_id(db_path, portfolio)

    with open(config_path, encoding="utf-8") as f:
        original_text = f.read()
    doc = json.loads(original_text)
    watches = doc.get("watches") if isinstance(doc, dict) else None
    if not isinstance(watches, list):
        raise ValueError(f"{config_path}: expected a top-level 'watches' list")

    style = _detect_json_style(original_text)

    assets = _ledger_assets(db_path, portfolio_id)
    pos_map = {(p["portfolio_id"], p["asset"]): p for p in compute_positions(db_path, portfolio_id)}
    pf_names = {p["id"]: p["name"] for p in list_portfolios(db_path)}
    price_cache = get_cached_prices(db_path)

    kept: list[dict] = []
    removed: list[dict] = []
    negative_net: list[dict] = []
    updated: list[dict] = []
    unchanged: list[str] = []
    skipped_disabled: list[str] = []
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    stale_levels: list[dict] = []
    price_unavailable: list[str] = []
    notes: list[str] = []
    candidates: list[str] = []
    changed = False
    # Every ledger asset key ANY watch names or normalizes to (enabled,
    # disabled, ambiguous and matched alike) — held assets on this set
    # are not re-reported as ``unwatched``.
    claimed_ledger_keys: set[str] = set()
    # Mirrors the raw watches array one-to-one: None = untouched (raw
    # bytes kept), dict = mutated element, "removed" = dropped. Drives
    # the byte-preserving splice in _splice_watches.
    element_states: list = []

    for watch in watches:
        name = watch.get("name") if isinstance(watch, dict) else None
        name = name or "?"
        if isinstance(watch, dict) and not watch.get("enabled"):
            # Parked config — any falsy ``enabled`` (missing, null,
            # false): position-watchdog skips exactly these entries (its
            # "close a position" contract keeps them for future re-adds),
            # so the sync must not prune or rewrite them either.
            claimed_ledger_keys.update(_claimed_ledger_keys(watch, assets))
            skipped_disabled.append(name)
            kept.append(watch)
            element_states.append(None)
            continue
        if not isinstance(watch, dict):
            unmatched.append({"name": name, "reason": "watch entry is not a JSON object"})
            kept.append(watch)
            element_states.append(None)
            continue

        claimed_ledger_keys.update(_claimed_ledger_keys(watch, assets))
        status, asset, pid, net, reason = _find_ledger_match(watch, assets)
        monitor = watch.get("monitor_provider") or ""
        levels = watch.get("levels") or []

        if status == "ambiguous":
            ambiguous.append({"name": name, "reason": reason})
            kept.append(watch)
            element_states.append(None)
            continue
        if status == "unmatched":
            unmatched.append({"name": name, "reason": reason})
            kept.append(watch)
            element_states.append(None)
            continue

        price, price_source = _resolve_price(asset, monitor, overrides, price_cache)
        if any(isinstance(lv, dict) and lv.get("type") == "zone" for lv in levels):
            if price is not None:
                stale = _stale_zone_report(name, levels, price, price_source)
                if stale is not None:
                    stale_levels.append(stale)
                    notes.append(
                        f"{name}: all zones sit more than "
                        f"{STALE_ZONE_MIN_DISTANCE_PCT:.0f}% below the last ledger-cached "
                        f"price {price} — levels kept verbatim; re-ground, do not prune"
                    )
            else:
                price_unavailable.append(name)

        if net < -FLAT_EPSILON:
            # A negative ledger net is NOT a flat position: either a
            # perps short (execution-kraken-perps records shorts as SELL
            # on the same kraken:<PAIR> key the spot watch shares) or an
            # over-sold residue. The short's size is not the ledger net
            # of the key it shares with spot — the watch is deliberately
            # left byte-identical and reported instead.
            negative_net.append(
                {
                    "name": name,
                    "asset": asset,
                    "net": net,
                    "note": (
                        "ledger net is negative — short or over-sold residue on this key; "
                        "the watch was deliberately left untouched (position_size is not "
                        "the ledger net of a key shared with a short)"
                    ),
                }
            )
            notes.append(
                f"{name}: ledger net is negative ({_render_num(net)}) — short or over-sold "
                "on this ledger key; watch kept untouched (not removed, position_size not rewritten)"
            )
            kept.append(watch)
            element_states.append(None)
            continue

        if abs(net) <= FLAT_EPSILON:
            removed.append(
                {
                    "name": name,
                    "asset": asset,
                    "monitor_provider": monitor,
                    "net": net,
                    "levels": levels,
                    "comments": {k: v for k, v in watch.items() if k.startswith("_comment_")},
                    "belongs_in": "watchlist",
                    "note": (
                        "ledger net is flat — watch removed; the zones must be "
                        "re-grounded in the watchlist config (deleting the watch "
                        "outright is the ZEC failure mode)"
                    ),
                }
            )
            candidates.append(name)
            notes.append(
                f"{name}: ledger net is flat ({_render_num(net)}) — watch removed; "
                "its zones are a watchlist candidate (re-ground, do not delete the watch)"
            )
            changed = True
            element_states.append("removed")
            continue

        corrections: list[str] = []
        new_size = round(net, POSITION_SIZE_DECIMALS)
        old_size = watch.get("position_size")
        if old_size != new_size:
            watch["position_size"] = new_size
            updated.append({"name": name, "asset": asset, "field": "position_size", "was": old_size, "now": new_size})
            corrections.append(
                f"position_size {_render_num(old_size)} -> {_render_num(new_size)} (ledger-derived {now_ts})"
            )
            changed = True
        if watch.get("entry_price") is None:
            pos = pos_map.get((pid, asset))
            avg_cost = pos.get("avg_cost") if pos else None
            if avg_cost is not None and avg_cost > 0:
                watch["entry_price"] = avg_cost
                updated.append({"name": name, "asset": asset, "field": "entry_price", "was": None, "now": avg_cost})
                corrections.append(
                    f"entry_price filled {_render_num(avg_cost)} (ledger avg cost, ledger-derived {now_ts})"
                )
                changed = True
            elif avg_cost is not None:
                notes.append(
                    f"{name}: ledger avg cost is 0 — zero-cost-basis position (airdrop/staking); "
                    "entry_price left unfilled (position-watchdog divides by entry)"
                )
        if corrections:
            existing = watch.get(_AUDIT_KEY)
            body = " | ".join(corrections)
            watch[_AUDIT_KEY] = f"{existing} | {body}" if existing else body
            kept.append(watch)
            element_states.append(watch)
        else:
            unchanged.append(name)
            kept.append(watch)
            element_states.append(None)

    unwatched: list[dict] = []
    for asset in sorted(assets):
        held = [e for e in assets[asset] if e["net"] > FLAT_EPSILON]
        if not held or asset in claimed_ledger_keys:
            continue
        unwatched.append(
            {
                "asset": asset,
                "ledger_net": round(sum(e["net"] for e in held), 10),
                "portfolios": [pf_names.get(e["pid"], str(e["pid"])) for e in held],
            }
        )

    doc["watches"] = kept

    report = {
        "config": config_path,
        "db": db_path,
        "dry_run": True,
        "changed": changed,
        "written": False,
        "removed": removed,
        "negative_net": negative_net,
        "updated": updated,
        "unchanged": unchanged,
        "skipped_disabled": skipped_disabled,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "unwatched": unwatched,
        "stale_levels": stale_levels,
        "price_unavailable": price_unavailable,
        "candidates": candidates,
        "notes": notes,
    }
    return report, doc, style, changed, original_text, element_states


def plan_open_positions_sync(
    db_path: str,
    config_path: str,
    *,
    portfolio: int | str | None = None,
    price_overrides: dict[str, float] | None = None,
    now: datetime | None = None,
) -> dict:
    """Pure planner — never writes, never touches the network.

    Returns the report described in the module docstring with
    ``dry_run=True`` and ``written=False``.
    """
    report, _doc, _style, _changed, _original_text, _element_states = _plan(
        db_path, config_path, portfolio=portfolio, price_overrides=price_overrides, now=now
    )
    return report


def sync_open_positions(
    db_path: str,
    config_path: str,
    *,
    dry_run: bool = False,
    portfolio: int | str | None = None,
    price_overrides: dict[str, float] | None = None,
    now: datetime | None = None,
) -> dict:
    """Planner + write. Returns the planner report plus ``written``.

    Writes only when something changed and ``--dry-run`` is off; the
    write is atomic (sibling temp file + ``os.replace``). The write text
    is spliced into the original raw text element-by-element (untouched
    entries keep their exact original bytes, including inline
    formatting); a whole-document re-dump happens only as a recorded
    fallback when the raw ``watches`` array cannot be located.
    """
    report, doc, style, changed, original_text, element_states = _plan(
        db_path, config_path, portfolio=portfolio, price_overrides=price_overrides, now=now
    )
    report["dry_run"] = bool(dry_run)
    written = False
    if changed and not dry_run:
        new_text = _splice_or_serialize(doc, original_text, style, element_states, report)
        if new_text != original_text:
            _atomic_write(config_path, new_text)
            written = True
    report["written"] = written
    return report


def sync_open_positions_from_env(
    db_path: str,
    *,
    portfolio: int | str | None = None,
    price_overrides: dict[str, float] | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Post-fill entry point: sync the held file named by the env var.

    Returns ``None`` when ``$MARKET_SKILLS_OPEN_POSITIONS_PATH`` is unset
    (the caller decides whether that is worth a warning line). Raises for
    other config errors — post-fill callers are expected to wrap this in
    try/except so a sync failure never changes their exit code.
    """
    path = os.environ.get(ENV_OPEN_POSITIONS_PATH)
    if not path:
        return None
    return sync_open_positions(db_path, path, portfolio=portfolio, price_overrides=price_overrides, now=now)
