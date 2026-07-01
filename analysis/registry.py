"""Registry of L2 and L3 skills.

Single source of truth for which skills the batch runners
(``run-all-l2``, ``run-all-l3``, ``run-watchlist``) iterate over. Adding
a new skill is a one-line ``register_*`` call here — the runners pick
it up automatically.

Mirrors the provider-registry pattern in
``analysis/providers/base.py`` and
``analysis/providers/execution_base.py``.

Layer taxonomy:

  L1 — pure-math indicators (``skills/market-*/lib.py`` returning ``L1Result``).
  L2 — pattern detectors composing L1s (returning ``L2Result``).
  L3 — strategies composing L2s (returning ``L3Result``).
"""

from __future__ import annotations

from analysis.skill_loader import load_skill

DEFAULT_L2_SKILLS: tuple[str, ...] = (
    "market-accumulation",
    "market-breakout",
    "market-exhaustion",
    "market-liquidity-sweep",
    "market-trend-quality",
)

DEFAULT_L3_STRATEGIES: tuple[str, ...] = (
    "strategy-trend-follow",
    "strategy-mean-reversion",
    "strategy-breakout-confirm",
    "strategy-accumulation-swing",
    "strategy-exhaustion-fade",
    "strategy-liquidity-sweep",
)

_l2_skills: list[str] = list(DEFAULT_L2_SKILLS)
_l3_strategies: list[str] = list(DEFAULT_L3_STRATEGIES)


def register_l2(name: str) -> None:
    """Append a skill to the L2 registry. Idempotent (no duplicates)."""
    if name not in _l2_skills:
        _l2_skills.append(name)


def register_l3(name: str) -> None:
    """Append a strategy to the L3 registry. Idempotent (no duplicates)."""
    if name not in _l3_strategies:
        _l3_strategies.append(name)


def l2_skills() -> list[str]:
    """Snapshot of registered L2 skill names (insertion order)."""
    return list(_l2_skills)


def l3_strategies() -> list[str]:
    """Snapshot of registered L3 strategy names (insertion order)."""
    return list(_l3_strategies)


def load_l2(name: str):
    """Convenience wrapper: load a registered L2 skill by name."""
    return load_skill(name)


def load_l3(name: str):
    """Convenience wrapper: load a registered L3 strategy by name."""
    return load_skill(name)


__all__ = [
    "DEFAULT_L2_SKILLS",
    "DEFAULT_L3_STRATEGIES",
    "l2_skills",
    "l3_strategies",
    "load_l2",
    "load_l3",
    "register_l2",
    "register_l3",
]
