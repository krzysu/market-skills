"""Conviction-calibration indicators — read L3 idea history to surface market state.

Pure math + a small JSON-backed history store. The store path resolves via
the ``XDG_DATA_HOME`` env var (path: ``$XDG_DATA_HOME/market-skills/l3_idea_history.json``)
so per-user state stays out of the repo. ``XDG_DATA_HOME`` must be set by
the consumer — the library does not paper over it with a host-specific
fallback. Pass ``path=`` explicitly to override.

The headline indicator is :func:`chop_score` — the fraction of recent L3
ideas sitting at conviction <= 2. A persistent high chop score (>0.70 over
3 ticks) is the "transition zone" signal the swing-scan cron has been
manually tagging since 2026-06-22.

Naming
------

This module was previously ``analysis/regime.py``. The name collided with
``analysis/macro.py``'s ``RegimeSignal`` (external cross-asset data: F&G,
VIX, DXY, US10Y, BTC.D, total mcap). The two are different signals —
``macro`` fetches live external state, this module reads the L3 idea
history. ``chop`` reflects the module's sole domain: how scattered the
L3 conviction distribution is.
"""

from __future__ import annotations

import json
import os
from typing import Any

# -- chop_score ---------------------------------------------------------------


# A "low conviction" idea is anything at or below this threshold. v1=1, v2=2.
LOW_CONVICTION_MAX = 2

# Default window: 3 ticks at the swing-scan's 4h cadence ≈ 12h. Match the
# HANDOFF contract.
DEFAULT_WINDOW_TICKS = 3


def chop_score(ideas: list[dict], *, window_ticks: int = DEFAULT_WINDOW_TICKS) -> float | None:
    """Fraction of L3 ideas at conviction <= 2 over the window.

    Args:
        ideas: list of idea dicts. Each must have a numeric ``conviction``
            field; non-numeric or missing convictions are skipped (not
            counted in either the numerator or denominator).
        window_ticks: kept for API symmetry with future rolling-window
            indicators. The current implementation operates on the full
            list (caller slices the history).

    Returns:
        float in [0.0, 1.0]. ``None`` when there are no countable ideas
        (the caller can distinguish "no signal" from "0% chop" cleanly).

    Thresholds (advisory, not gating):
        - > 0.70: transition-zone, be patient, no new sizing
        - 0.40 - 0.70: normal swing mode
        - < 0.40: aggressive, sized entries on the standard bucket are fine
    """
    del window_ticks  # current scope operates on the full list

    if not ideas:
        return None
    countable = 0
    low = 0
    for idea in ideas:
        c = idea.get("conviction") if isinstance(idea, dict) else None
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            continue
        countable += 1
        if int(c) <= LOW_CONVICTION_MAX:
            low += 1
    if countable == 0:
        return None
    return low / countable


# -- L3 idea history store ----------------------------------------------------


def default_history_path() -> str:
    """Default path for the L3 idea history JSON.

    Resolves to ``$XDG_DATA_HOME/market-skills/l3_idea_history.json``.
    Raises :class:`EnvironmentError` when ``XDG_DATA_HOME`` is unset —
    the library deliberately does not paper over with a host-specific
    fallback (see AGENTS.md "What to avoid"). Callers may pass ``path=``
    to use an explicit location.
    """
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        raise OSError(
            "XDG_DATA_HOME is not set; cannot resolve the L3 idea history "
            "path. Set XDG_DATA_HOME or pass path= explicitly to "
            "load_history / append_tick / chop_score_from_history."
        )
    return os.path.join(base, "market-skills", "l3_idea_history.json")


def _resolve_path(path: str | os.PathLike | None) -> str:
    if path is None:
        return default_history_path()
    return os.fspath(path)


def load_history(path: str | os.PathLike | None = None) -> list[dict]:
    """Read the rolling L3 idea history.

    Returns an empty list when the file is missing or malformed; never
    raises (the calling cron must not crash on a corrupted history).
    """
    resolved = _resolve_path(path)
    if not os.path.exists(resolved):
        return []
    try:
        with open(resolved) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return data


def append_tick(ideas: list[dict], *, path: str | os.PathLike | None = None) -> int:
    """Append a tick's worth of ideas to the history file.

    Each idea is enriched with a timestamp so consumers can reason about
    which tick it came from. Returns the count appended.

    The store is FIFO-capped at 200 entries (HANDOFF contract: <= 200
    per window). Older entries are evicted on overflow.
    """
    resolved = _resolve_path(path)
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC).isoformat()
    history = load_history(resolved)
    for idea in ideas or []:
        if not isinstance(idea, dict):
            continue
        # Strip heavy fields before persisting (ideas can carry input_scores
        # and large source_skills arrays we don't need for chop_score).
        entry = {
            "ts": now,
            "pair": idea.get("pair"),
            "direction": idea.get("direction"),
            "conviction": idea.get("conviction"),
            "version": idea.get("version"),
        }
        history.append(entry)

    cap = 200
    if len(history) > cap:
        history = history[-cap:]

    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    with open(resolved, "w") as f:
        json.dump(history, f, indent=2)
    return len(ideas or [])


def chop_score_from_history(
    path: str | os.PathLike | None = None,
    *,
    window_ticks: int = DEFAULT_WINDOW_TICKS,
) -> dict[str, Any] | None:
    """Read the history file and compute chop_score over the last N ticks.

    Returns a dict with ``score``, ``window_ticks``, ``ideas_count``, and
    ``low_count`` for the bug-scan finding envelope, or ``None`` when the
    history has fewer than ``window_ticks`` ticks.
    """
    history = load_history(path)
    if not history:
        return None
    # Slice to the most recent window_ticks distinct timestamps.
    # Group by ts first; history is append-ordered, so we can walk from
    # the end and collect unique ts values.
    by_ts: dict[str, list[dict]] = {}
    ts_order: list[str] = []
    for entry in history:
        ts = entry.get("ts", "")
        if ts not in by_ts:
            by_ts[ts] = []
            ts_order.append(ts)
        by_ts[ts].append(entry)
    recent_ticks = ts_order[-window_ticks:] if ts_order else []
    recent_ideas: list[dict] = []
    for ts in recent_ticks:
        recent_ideas.extend(by_ts[ts])

    if len(recent_ticks) < window_ticks:
        return None

    score = chop_score(recent_ideas, window_ticks=window_ticks)
    if score is None:
        return None
    low = sum(
        1
        for i in recent_ideas
        if isinstance(i.get("conviction"), (int, float))
        and not isinstance(i.get("conviction"), bool)
        and int(i["conviction"]) <= LOW_CONVICTION_MAX
    )
    return {
        "score": round(score, 4),
        "window_ticks": window_ticks,
        "ideas_count": len(recent_ideas),
        "low_count": low,
    }
