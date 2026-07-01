"""market-macro — ticker-agnostic cross-asset regime fetcher.

Thin wrapper over :func:`analysis.macro.fetch_regime`. The skill
exists for two reasons: (1) a discoverable CLI entry point an
agent can call as a tool, and (2) a stable import surface so the
``run-all-l3`` runner (or any future orchestrator) can read the
macro block without going through the CLI.
"""

from __future__ import annotations

from analysis.macro import fetch_regime as _fetch_regime


def analyze(*, ttl_seconds: float = 300, write_history: bool = True) -> dict:
    """Return the current cross-asset regime signal.

    Args:
        ttl_seconds: in-process cache lifetime. ``0`` disables
            caching. Forwarded to :func:`analysis.macro.fetch_regime`.
        write_history: append this tick to the macro ring buffer.
            Forwarded to :func:`analysis.macro.fetch_regime`.

    Returns:
        A :class:`~analysis.contracts.RegimeSignal` dict.
    """
    return _fetch_regime(ttl_seconds=ttl_seconds, write_history=write_history)
