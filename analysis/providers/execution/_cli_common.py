"""Trivial shared helpers for the execution-skill CLI scripts.

Used by both ``skills/execution-kraken-spot/scripts/run.py`` and
``skills/execution-kraken-perps/scripts/run.py``. Intentionally tiny —
only truly identical helpers live here. Subcommand bodies, intent
shapes, AFK-gate wiring, and provider registration all stay in the
per-venue script because they diverge between spot and perps.
"""

from __future__ import annotations

import json
import sys


def emit_json(payload: dict | list) -> None:
    """Print a dict/list as indent-2 JSON to stdout."""
    print(json.dumps(payload, indent=2, default=str))


def confirm(prompt: str) -> bool:
    """Read y/n from stdin; default to False on EOF / non-affirmative reply."""
    try:
        reply = input(prompt)
    except EOFError:
        return False
    return reply.strip().lower() in ("y", "yes")


def resolve_portfolio_id(db_path: str, portfolio: str | None) -> int | None:
    """Resolve a portfolio name-or-id argument to its DB row id.

    Returns ``None`` when ``portfolio`` is not supplied. Exits with
    code 2 + stderr message when the portfolio does not exist.
    """
    if not portfolio:
        return None
    from portfolio.db import get_portfolio

    pf = get_portfolio(db_path, portfolio)
    if pf is None:
        print(f"error: portfolio '{portfolio}' not found in {db_path}", file=sys.stderr)
        sys.exit(2)
    return pf["id"]


def _num_repr(value) -> str:
    """Render a report scalar for the post-fill stderr line (numbers as
    floats, so ``3417`` reads ``3417.0`` like the ledger-sync audit note)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return repr(float(value))
    return str(value)


def _removed_watchlist_hint(removed: dict) -> str:
    """Zone bounds + watchlist hint for a flat removal.

    The report's ``removed`` entry carries the verbatim hand-authored
    ``levels``/``_comment_*`` and ``belongs_in: "watchlist"``; the stderr
    line must surface the zone bounds so the removal does not silently
    take the hand-authored zones out of the held file.
    """
    bounds: list[str] = []
    for lv in removed.get("levels") or []:
        if not isinstance(lv, dict) or lv.get("type") != "zone":
            continue
        low, high = lv.get("low"), lv.get("high")
        if low is None and high is None:
            continue
        bounds.append(f"{_num_repr(low)}-{_num_repr(high)}")
    if bounds:
        return f" (zones {'; '.join(bounds)} — watchlist candidate)"
    return " (watchlist candidate)"


def _sync_stderr_line(report: dict) -> str:
    parts = [f"{r['name']} ledger flat -> removed{_removed_watchlist_hint(r)}" for r in report.get("removed", [])]
    parts += [
        f"{e['name']} ledger net negative ({_num_repr(e['net'])}) — watch left untouched"
        for e in report.get("negative_net", [])
    ]
    parts += [
        f"{u['name']} {u['field']} {_num_repr(u['was'])} -> {_num_repr(u['now'])}" for u in report.get("updated", [])
    ]
    if not parts:
        return "open-positions: no drift"
    return "open-positions: " + "; ".join(parts)


def sync_open_positions_after_fill(db_path: str) -> dict | None:
    """Re-ground the position-watchdog held file right after a recorded fill.

    Drift is created by fills, so this runs on the execution ->
    portfolio-mgmt write path (and on demand via the
    ``portfolio-mgmt sync-open-positions`` subcommand — no cron job).

    The held-file path comes from ``$MARKET_SKILLS_OPEN_POSITIONS_PATH``;
    when unset, print one stderr warning and return ``None``. Everything
    is wrapped in try/except: this must never raise, never change the
    caller's exit code, and never pollute stdout (which carries the JSON
    payload).
    """
    try:
        from portfolio.sync import ENV_OPEN_POSITIONS_PATH, sync_open_positions_from_env

        report = sync_open_positions_from_env(db_path)
        if report is None:
            print(
                f"warning: {ENV_OPEN_POSITIONS_PATH} not set — open-positions drift not checked after fill",
                file=sys.stderr,
            )
            return None
        print(_sync_stderr_line(report), file=sys.stderr)
        return report
    except Exception as e:  # noqa: BLE001 — advisory hook, never fatal after a fill
        print(f"warning: open-positions sync failed: {e}", file=sys.stderr)
        return None


# Underscore-prefixed aliases so existing call sites in
# skills/execution-kraken-* can `from ._cli_common import _confirm`
# without renaming every internal call.
_confirm = confirm
_emit_json = emit_json
_resolve_portfolio_id = resolve_portfolio_id

__all__ = [
    "confirm",
    "emit_json",
    "resolve_portfolio_id",
    "sync_open_positions_after_fill",
    "_confirm",
    "_emit_json",
    "_resolve_portfolio_id",
]
