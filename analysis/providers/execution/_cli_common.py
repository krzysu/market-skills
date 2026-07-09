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


# Underscore-prefixed aliases so existing call sites in
# skills/execution-kraken-* can `from ._cli_common import _confirm`
# without renaming every internal call.
_confirm = confirm
_emit_json = emit_json
_resolve_portfolio_id = resolve_portfolio_id

__all__ = ["confirm", "emit_json", "resolve_portfolio_id", "_confirm", "_emit_json", "_resolve_portfolio_id"]
