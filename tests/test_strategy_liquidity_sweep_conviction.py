"""Tests for strategy-liquidity-sweep conviction formula (bead market-skills-7eq).

The grid search (scripts/conviction_grid.py) scores alternative formulas via
``conviction_from_confidences``. These tests pin the pure function's contract
and prove the shipped ``"current"`` mode is behavior-identical to the legacy
inline ``min(5, sweep + accum // 2)`` expression.
"""

from __future__ import annotations

import importlib.util
import os

import pytest


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "strategy-liquidity-sweep", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_liquidity_sweep_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LIB = _load_lib()
conviction_from_confidences = _LIB.conviction_from_confidences


def test_current_mode_matches_legacy_expression():
    # "current" must equal the shipped inline formula min(5, sweep + accum // 2)
    # for every (sweep, accum) pair — guarantees the refactor is behavior-neutral.
    for sweep in range(0, 6):
        for accum in range(0, 6):
            legacy = min(5, sweep + accum // 2)
            assert conviction_from_confidences(sweep, accum, mode="current") == legacy


def test_formula_variants():
    # Hand-computed reference values for the candidate modes.
    assert conviction_from_confidences(3, 3, mode="current") == 4        # 3 + 3//2 = 4
    assert conviction_from_confidences(3, 3, mode="add") == 5            # 3 + 3 = 6 -> cap 5
    assert conviction_from_confidences(3, 3, mode="add_minus_one") == 5  # 3 + 3 - 1 = 5
    assert conviction_from_confidences(3, 3, mode="max_plus_one") == 4   # max(3,3)+1 = 4
    # A low-accumulation case shows where the modes diverge most.
    assert conviction_from_confidences(3, 1, mode="current") == 3        # 3 + 0 = 3
    assert conviction_from_confidences(3, 1, mode="add") == 4            # 3 + 1 = 4
    assert conviction_from_confidences(3, 1, mode="max_plus_one") == 4   # max(3,1)+1 = 4


def test_conviction_is_capped():
    assert conviction_from_confidences(5, 5, mode="add") == 5            # cap at 5
    # "current" matches the legacy formula exactly, including the no-floor edge.
    assert conviction_from_confidences(0, 0, mode="current") == 0
    assert conviction_from_confidences(1, 1, mode="current") == 1


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        conviction_from_confidences(3, 3, mode="bogus")
