"""market-valuation — ticker-agnostic SP500 CAPE valuation signal.

Thin wrapper over :func:`analysis.valuation.fetch_valuation`. The skill
exists for two reasons: (1) a discoverable CLI entry point an agent can
call as a tool, and (2) a stable import surface so the L3 layer
(``strategy-mean-reversion``) and the morning-brief orchestrator can read
the CAPE z-score without going through the CLI.

The signal is narrate-only by design (ADR-0002). Consumers either drop
the ``regime_note`` into a briefing or attach the z-score to an L3
idea's ``veto_reasons`` list as a soft tag.
"""

from __future__ import annotations

from analysis.valuation import fetch_valuation as _fetch_valuation


def analyze(*, ttl_seconds: float = 3600, write_history: bool = True) -> dict:
    """Return the current SP500 CAPE valuation signal.

    Args:
        ttl_seconds: in-process cache lifetime. ``0`` disables caching.
            Forwarded to :func:`analysis.valuation.fetch_valuation`.
        write_history: append this tick to the valuation ring buffer.
            Forwarded to :func:`analysis.valuation.fetch_valuation`.

    Returns:
        A ValuationSignal-shaped dict. See ``SKILL.md`` for the schema.
    """
    return _fetch_valuation(ttl_seconds=ttl_seconds, write_history=write_history)
