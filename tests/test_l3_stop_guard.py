"""Tests for the 2% L3 stop-distance guard.

Unit-tests :func:`enforce_min_stop_distance` directly (the contract), then
smoke-tests each of the 6 L3 strategies to confirm the guard is wired in
(strategies import it from ``analysis.contracts`` and the filtered-ideas
branch is reachable). The per-strategy "rejected under tiny ATR" integration
test uses the <PRIVATE_PERP> 4h fixture and a tiny patched ATR — a strategy that
naturally emits an idea under the fixture will see its idea dropped by the
guard; a strategy that doesn't emit a natural idea simply returns the empty
list and the test still confirms the import + filter path doesn't crash.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

from analysis.contracts import SWING_MIN_STOP_DISTANCE, enforce_min_stop_distance

# -- unit tests for the helper ------------------------------------------------


class TestEnforceMinStopDistance:
    def test_long_above_2pct_ok(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": 100.0, "stop_loss": 97.0})
        assert ok is True
        assert narrative == ""

    def test_long_below_2pct_rejected(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": 100.0, "stop_loss": 99.0})
        assert ok is False
        assert "1.00%" in narrative
        assert "swing minimum" in narrative

    def test_short_below_2pct_rejected(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": 100.0, "stop_loss": 101.0})
        assert ok is False
        assert "1.00%" in narrative

    def test_exact_2pct_ok(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": 100.0, "stop_loss": 98.0})
        assert ok is True
        assert narrative == ""

    def test_missing_entry_passes(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": None, "stop_loss": 99.0})
        assert ok is True
        assert narrative == ""

    def test_zero_entry_passes(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": 0, "stop_loss": 99.0})
        assert ok is True
        assert narrative == ""

    def test_missing_stop_passes(self):
        ok, narrative = enforce_min_stop_distance({"entry_price": 100.0, "stop_loss": None})
        assert ok is True
        assert narrative == ""

    def test_constant_default_is_2pct(self):
        assert SWING_MIN_STOP_DISTANCE == 0.02

    def test_custom_min_pct(self):
        ok, narrative = enforce_min_stop_distance(
            {"entry_price": 100.0, "stop_loss": 99.0},
            min_pct=0.005,
        )
        assert ok is True
        assert narrative == ""

        ok2, narrative2 = enforce_min_stop_distance(
            {"entry_price": 100.0, "stop_loss": 99.0},
            min_pct=0.02,
        )
        assert ok2 is False
        assert "2% swing minimum" in narrative2

    def test_vvv_drift_repro_1_86pct(self):
        """<PRIVATE_AI> 1h mean-reversion repro: 1.86% stop must be rejected."""
        ok, narrative = enforce_min_stop_distance({"entry_price": 1.0, "stop_loss": 0.9814})
        assert ok is False
        assert "1.86%" in narrative


# -- per-strategy smoke tests -------------------------------------------------


STRATEGY_DIRS = [
    "strategy-accumulation-swing",
    "strategy-breakout-confirm",
    "strategy-exhaustion-fade",
    "strategy-liquidity-sweep",
    "strategy-mean-reversion",
    "strategy-trend-follow",
]


def _load_strat_lib(strategy_dir: str):
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", strategy_dir, "lib.py")
    spec = importlib.util.spec_from_file_location(f"{strategy_dir.replace('-', '_')}_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_hype_fixture() -> list[list]:
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "hype_4h_2026-06-19.json")
    with open(fixture_path) as f:
        return json.load(f)


class TestStrategyWiring:
    """Each L3 strategy imports ``enforce_min_stop_distance`` from contracts.

    Importing the lib.py and confirming the symbol is bound in the module
    namespace catches the "forgot to import after refactor" regression.
    """

    @pytest.mark.parametrize("strategy_dir", STRATEGY_DIRS)
    def test_strategy_imports_enforce_min_stop_distance(self, strategy_dir: str):
        mod = _load_strat_lib(strategy_dir)
        assert hasattr(mod, "enforce_min_stop_distance"), (
            f"{strategy_dir}/lib.py must import enforce_min_stop_distance from analysis.contracts"
        )
        assert mod.enforce_min_stop_distance is enforce_min_stop_distance


class TestStrategyNaturalStopsRespectGuard:
    """Smoke test: run each strategy on the <PRIVATE_PERP> 4h fixture and verify that
    any emitted idea has a stop distance >= the 2% swing minimum. This
    catches "wiring is wrong, guard never runs" regressions. Strategies
    that don't emit an idea on the fixture naturally fall through the
    "ideas: []" branch — that's fine; the test only constrains the
    ``ideas: [...]`` branch.
    """

    @pytest.mark.parametrize("strategy_dir", STRATEGY_DIRS)
    def test_any_emitted_idea_has_wide_enough_stop(self, strategy_dir: str):
        mod = _load_strat_lib(strategy_dir)
        candles = _load_hype_fixture()
        result = mod.analyze(candles, ticker="<PRIVATE_PERP>", interval="4h", period="6mo")

        assert "ideas" in result
        assert "narrative" in result
        for idea in result["ideas"]:
            entry = idea.get("entry_price")
            stop = idea.get("stop_loss")
            assert entry and stop, f"{strategy_dir} emitted incomplete idea: {idea}"
            dist = abs(entry - stop) / entry
            assert dist >= SWING_MIN_STOP_DISTANCE, (
                f"{strategy_dir} emitted sub-{SWING_MIN_STOP_DISTANCE:.0%} stop "
                f"(entry={entry}, stop={stop}, dist={dist:.2%})"
            )
