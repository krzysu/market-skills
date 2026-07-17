"""Per-fix test fixture for backtest-engine walk-forward replay (bead bt-1).

The counterfactual flip-test (``test_no_lookahead_when_future_bar_flipped``) is
the per-fix fixture required by AGENTS.md: it constructs synthetic OHLC, runs
the loop, and asserts no idea at bar t includes data from bars > t. All tests
are deterministic (seeded RNG, mock strategy) and network-free.
"""

from __future__ import annotations

import importlib.util
import os
import random

import pytest


def _load_bt_lib():
    """Load skills/backtest-engine/lib.py dynamically (mirror test_l1_skills)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "backtest-engine", "lib.py")
    spec = importlib.util.spec_from_file_location("backtest_engine_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BT = _load_bt_lib()
WalkForwardRunner = _BT.WalkForwardRunner
NoLookaheadError = _BT.NoLookaheadError


def _make_candles(n: int = 250, seed: int = 42) -> list[list]:
    """Deterministic synthetic OHLC: [[i*86400, price, price+1, price-1, price, vol], ...]."""
    rng = random.Random(seed)
    candles: list[list] = []
    price = 100.0
    for i in range(n):
        price = price + rng.uniform(-2.0, 2.0)
        candles.append([i * 86400, price, price + 1.0, price - 1.0, price, rng.randint(100000, 500000)])
    return candles


class _MockStrategy:
    """Deterministic strategy: one idea per call whose entry_price = last close seen.

    The idea is a pure function of the last bar's close, so flipping a future
    bar's close cannot change the idea at an earlier bar — the property the
    no-lookahead flip-test asserts.
    """

    def analyze(self, candles, *, ticker, interval="1d", period="1y", asset_class=None):
        if not candles:
            return {"ideas": [], "narrative": "no data"}
        close = candles[-1][4]
        return {
            "ideas": [
                {
                    "pair": ticker,
                    "direction": "long",
                    "conviction": 1,
                    "entry_type": "market",
                    "entry_price": close,
                    "stop_loss": close * 0.99,
                    "take_profit": [close * 1.01, close * 1.02, close * 1.05],
                    "reasoning": "mock",
                    "source_skills": ["mock"],
                }
            ],
            "narrative": "",
        }


class _PreComputedStrategy:
    """Anti-pattern: exposes a precomputed idea stream instead of building per bar."""

    precomputed_ideas = [{"pair": "X"}]

    def analyze(self, candles, **kwargs):
        raise AssertionError("analyze must not be called when precomputed_ideas is exposed")


class TestWalkForwardRunner:
    def test_run_returns_one_entry_per_bar_after_warmup(self):
        candles = _make_candles(n=250)
        runner = WalkForwardRunner()
        windows = runner.run(_MockStrategy(), "TEST", candles, warmup=50)
        assert len(windows) == 200
        assert [w["bar_index"] for w in windows] == list(range(50, 250))

    def test_no_lookahead_when_future_bar_flipped(self):
        # Per-fix fixture: counterfactual flip-test. Flip a future bar's close
        # and assert the idea at every earlier bar is unchanged — i.e. no idea
        # at bar t depends on data from bars > t.
        candles = _make_candles(n=250)
        warmup = 50
        mut_idx = warmup + 50  # bar 100 — strictly future relative to bars [50, 99]
        runner = WalkForwardRunner()
        baseline = runner.run(_MockStrategy(), "TEST", candles, warmup=warmup)

        mutated = [list(c) for c in candles]
        mutated[mut_idx][4] = 9999.0  # flip the close of a future bar
        mutated_windows = runner.run(_MockStrategy(), "TEST", mutated, warmup=warmup)

        base_map = {w["bar_index"]: w for w in baseline}
        mut_map = {w["bar_index"]: w for w in mutated_windows}

        # Bars before mut_idx never saw the flipped bar → ideas unchanged.
        for t in range(warmup, mut_idx):
            assert base_map[t]["idea"] == mut_map[t]["idea"], f"look-ahead leaked at bar {t}"

        # The mutation took effect at mut_idx (the strategy saw the flipped
        # close) — proves the flip was real, not a no-op.
        assert base_map[mut_idx]["idea"] != mut_map[mut_idx]["idea"]

    def test_precomputed_ideas_raises_no_lookahead_error(self):
        runner = WalkForwardRunner()
        with pytest.raises(NoLookaheadError) as exc_info:
            runner.run(_PreComputedStrategy(), "TEST", _make_candles(n=60), warmup=10)
        assert "precomputed_ideas" in str(exc_info.value)
        assert "look-ahead" in str(exc_info.value)

    def test_idempotency_two_runs_identical(self):
        candles = _make_candles(n=120)
        runner = WalkForwardRunner()
        first = runner.run(_MockStrategy(), "TEST", candles, warmup=20)
        second = runner.run(_MockStrategy(), "TEST", candles, warmup=20)
        assert first == second

    def test_warmup_zero_and_warmup_n(self):
        candles = _make_candles(n=250)
        runner = WalkForwardRunner()
        assert len(runner.run(_MockStrategy(), "TEST", candles, warmup=0)) == 250
        assert len(runner.run(_MockStrategy(), "TEST", candles, warmup=100)) == 150

    def test_two_instances_produce_identical_output(self):
        candles = _make_candles(n=120)
        a = WalkForwardRunner().run(_MockStrategy(), "TEST", candles, warmup=20)
        b = WalkForwardRunner().run(_MockStrategy(), "TEST", candles, warmup=20)
        assert a == b

    def test_empty_idea_window_is_none_not_dropped(self):
        # When the strategy fires no idea, the window keeps idea=None so the
        # window length stays observable (not silently truncated).
        class _NeverFires:
            def analyze(self, candles, **kwargs):
                return {"ideas": [], "narrative": "nothing"}

        candles = _make_candles(n=80)
        windows = WalkForwardRunner().run(_NeverFires(), "TEST", candles, warmup=10)
        assert len(windows) == 70
        assert all(w["idea"] is None for w in windows)
