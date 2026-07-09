"""Registry of L2 and L3 skills.

Single source of truth for which skills the batch runners
(``run-all-l2``, ``run-all-l3``, ``run-watchlist``) iterate over. New
skills are appended to ``_l2_skills`` / ``_l3_strategies`` below — the
runners pick them up automatically via :func:`l2_skills` /
:func:`l3_strategies`.

Layer taxonomy:

  L1 — pure-math indicators (``skills/market-*/lib.py`` returning ``L1Result``).
  L2 — pattern detectors composing L1s (returning ``L2Result``).
  L3 — strategies composing L2s (returning ``L3Result``).
"""

from __future__ import annotations

_l2_skills: list[str] = [
    "market-accumulation",
    "market-breakout",
    "market-exhaustion",
    "market-liquidity-sweep",
    "market-trend-quality",
]

_l3_strategies: list[str] = [
    "strategy-trend-follow",
    "strategy-mean-reversion",
    "strategy-breakout-confirm",
    "strategy-accumulation-swing",
    "strategy-exhaustion-fade",
    "strategy-liquidity-sweep",
]


def l2_skills() -> list[str]:
    """Snapshot of registered L2 skill names (insertion order)."""
    return list(_l2_skills)


def l3_strategies() -> list[str]:
    """Snapshot of registered L3 strategy names (insertion order)."""
    return list(_l3_strategies)


__all__ = [
    "l2_skills",
    "l3_strategies",
]
