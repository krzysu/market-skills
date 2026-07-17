"""Tests for ``analysis.conviction_thresholds`` (beads oin + czr).

The module is the single source of truth for per-strategy per-(ticker,
interval) conviction-gate thresholds. Per bead ``czr`` it ships with no
asset references — overrides are loaded from
``$MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` at import time.

The lib-level tests in
``test_strategy_trend_follow_conviction_gate.py`` and
``test_strategy_liquidity_sweep_conviction_gate.py`` exercise the gate
through the L3 pipelines; this file pins the lookup module's contract
directly so future changes (env var name, JSON shape, fall-through
semantics) cannot quietly regress the table.

The shipped (env-unset) state must satisfy these contracts:

- ``GLOBAL_MIN_CONVICTION_TO_EMIT == 1`` (legacy no-op).
- ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY == {}`` (no asset refs in source).
- ``lookup_min_conviction`` returns the global default for every
  unknown combination.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import analysis.conviction_thresholds as ct


class TestShippedState:
    """The shipped (env-unset) state must be asset-free and default-to-1."""

    def test_global_default_is_one(self):
        assert ct.GLOBAL_MIN_CONVICTION_TO_EMIT == 1, (
            f"GLOBAL_MIN_CONVICTION_TO_EMIT must remain 1 (legacy no-op); got "
            f"{ct.GLOBAL_MIN_CONVICTION_TO_EMIT}. Raising this number changes "
            f"the gate for every strategy / (ticker, interval) without an "
            f"explicit override, which is rarely the intent — prefer setting "
            f"GLOBAL_MIN_CONVICTION_TO_EMIT in the overrides JSON instead."
        )

    def test_shipped_table_is_empty(self):
        """No asset references in source: the shipped table must be empty.

        Per bead czr acceptance criteria: ``grep -r '<PROVIDER>:<TICKER>\\|...'
        analysis/conviction_thresholds.py`` returns nothing. The runtime
        corollary is that the shipped table starts empty and grows only when
        the env var points at an external JSON file."""
        assert ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY == {}, (
            "analysis/conviction_thresholds.py must ship with an empty "
            "MIN_CONVICTION_TO_EMIT_BY_STRATEGY (no asset references). "
            "Per-strategy per-(ticker, interval) overrides live in the JSON "
            "pointed to by $MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH."
        )


class TestTableContract:
    """Once populated, the table must satisfy its published structure."""

    def test_top_level_keys_are_strategy_names(self):
        with _patched("strategy-x", ("yf:AAA", "1d"), 4):
            for key in ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.keys():
                assert isinstance(key, str), f"Top-level key must be the strategy name string; got {key!r}"
                assert key.startswith("strategy-"), (
                    f"Strategy key should be the canonical L3 name (starts with 'strategy-'); got {key!r}"
                )

    def test_inner_keys_are_ticker_interval_tuples(self):
        """Each inner mapping must key on ``(ticker, interval)`` tuples,
        not single strings or other shapes. Catches accidental key
        flattening (e.g. someone using the JSON-nested form by mistake)."""
        with _patched("strategy-x", ("yf:AAA", "1d"), 4):
            for strat, table in ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.items():
                for k in table.keys():
                    assert isinstance(k, tuple) and len(k) == 2, (
                        f"{strat}: inner key must be (ticker, interval) tuple; got {k!r}"
                    )
                    ticker, interval = k
                    assert isinstance(ticker, str) and isinstance(interval, str), (
                        f"{strat}: tuple fields must be strings; got {k!r}"
                    )

    def test_inner_values_are_positive_ints(self):
        for strat, table in ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.items():
            for k, v in table.items():
                assert isinstance(v, int) and not isinstance(v, bool), f"{strat} {k}: threshold must be int; got {v!r}"
                assert v >= 0, f"{strat} {k}: threshold must be >= 0 (0 = opt-out); got {v!r}"


class TestEnvVarLoading:
    """The env-var contract: unset → empty, set → JSON wins, missing file → OSError."""

    def test_unset_env_keeps_shipped_state(self, monkeypatch):
        """Without the env var, the loader is a no-op and the shipped
        empty state survives."""
        monkeypatch.delenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", raising=False)
        # Force the loader to re-read with the env currently unset.
        with _snapshot():
            ct._load_overrides()
        assert ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY == {}
        assert ct.GLOBAL_MIN_CONVICTION_TO_EMIT == 1

    def test_env_var_loads_min_overrides(self, monkeypatch, tmp_path):
        """The JSON's MIN_CONVICTION_TO_EMIT_BY_STRATEGY is flattened
        to the in-memory tuple-keyed form (JSON keys must be strings)."""
        cfg = _write_cfg(
            tmp_path,
            {
                "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {
                    "strategy-trend-follow": {"provider:X": {"4h": 5}},
                },
            },
        )
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot():
            ct._load_overrides()
            assert ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY == {
                "strategy-trend-follow": {("provider:X", "4h"): 5},
            }

    def test_env_var_overrides_global_default(self, monkeypatch, tmp_path):
        """Setting GLOBAL_MIN_CONVICTION_TO_EMIT in the JSON replaces the
        module-level default."""
        cfg = _write_cfg(tmp_path, {"GLOBAL_MIN_CONVICTION_TO_EMIT": 3})
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot():
            ct._load_overrides()
            assert ct.GLOBAL_MIN_CONVICTION_TO_EMIT == 3
            assert ct.lookup_min_conviction("strategy-trend-follow", "provider:X", "4h") == 3

    def test_env_var_merges_multiple_strategies(self, monkeypatch, tmp_path):
        cfg = _write_cfg(
            tmp_path,
            {
                "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {
                    "strategy-trend-follow": {"provider:A": {"4h": 4}},
                    "strategy-liquidity-sweep": {"provider:B": {"1d": 2}},
                },
            },
        )
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot():
            ct._load_overrides()
            assert ct.lookup_min_conviction("strategy-trend-follow", "provider:A", "4h") == 4
            assert ct.lookup_min_conviction("strategy-liquidity-sweep", "provider:B", "1d") == 2
            # per-strategy isolation: trend-follow's entry must not leak.
            assert (
                ct.lookup_min_conviction("strategy-liquidity-sweep", "provider:A", "4h")
                == ct.GLOBAL_MIN_CONVICTION_TO_EMIT
            )

    def test_missing_file_raises_actionable_error(self, monkeypatch, tmp_path):
        """An env var that points at a missing file is a configuration
        bug, not a silent no-op (mirrors analysis.notes / analysis.watchlist
        failure-mode contract)."""
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(tmp_path / "nope.json"))
        with _snapshot(), pytest.raises(OSError, match="MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH"):
            ct._load_overrides()

    def test_malformed_top_level_raises(self, monkeypatch, tmp_path):
        cfg = tmp_path / "bad.json"
        cfg.write_text("[1, 2, 3]")
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot(), pytest.raises(ValueError, match="expected a JSON object"):
            ct._load_overrides()

    def test_rejects_bool_threshold(self, monkeypatch, tmp_path):
        """Bools are an ``int`` subclass — ``int(True) == 1`` silently — so the
        pre-fix loader would accept them. A boolean threshold is almost
        always a config typo (``true`` written where ``N`` was intended); the
        fix rejects it with a message that names the offending file path."""
        cfg = _write_cfg(tmp_path, {"GLOBAL_MIN_CONVICTION_TO_EMIT": True})
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot(), pytest.raises(ValueError, match="non-negative int"):
            ct._load_overrides()

    def test_rejects_float_threshold(self, monkeypatch, tmp_path):
        """Floats truncate silently under ``int()`` (``2.5`` becomes ``2``),
        masking a config bug. The fix rejects floats outright."""
        cfg = _write_cfg(tmp_path, {"GLOBAL_MIN_CONVICTION_TO_EMIT": 2.5})
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot(), pytest.raises(ValueError, match="non-negative int"):
            ct._load_overrides()

    def test_rejects_negative_threshold_in_global(self, monkeypatch, tmp_path):
        """A negative global would invert the gate (drop ideas whose conviction
        is strictly *below* a negative number — effectively always). Reject."""
        cfg = _write_cfg(tmp_path, {"GLOBAL_MIN_CONVICTION_TO_EMIT": -1})
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot(), pytest.raises(ValueError, match=">= 0"):
            ct._load_overrides()

    def test_rejects_negative_threshold_in_per_strategy_table(self, monkeypatch, tmp_path):
        """Same contract for per-strategy per-(ticker, interval) entries."""
        cfg = _write_cfg(
            tmp_path,
            {
                "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {
                    "strategy-trend-follow": {"hl:NEG": {"4h": -1}},
                },
            },
        )
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot(), pytest.raises(ValueError, match=">= 0"):
            ct._load_overrides()

    def test_zero_threshold_still_accepted(self, monkeypatch, tmp_path):
        """0 is the documented opt-out value and must keep working — only
        strictly-negative ints are rejected."""
        cfg = _write_cfg(tmp_path, {"GLOBAL_MIN_CONVICTION_TO_EMIT": 0})
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", str(cfg))
        with _snapshot():
            ct._load_overrides()
            assert ct.GLOBAL_MIN_CONVICTION_TO_EMIT == 0


class TestLookupBehaviour:
    """Pin the runtime lookup semantics, independent of the L3 plumbing."""

    def test_unknown_strategy_returns_default(self):
        with _patched("strategy-trend-follow", ("hl:X", "1d"), 5):
            v = ct.lookup_min_conviction("strategy-liquidity-sweep", "hl:X", "1d")
        assert v == ct.GLOBAL_MIN_CONVICTION_TO_EMIT

    def test_unknown_ticker_returns_default(self):
        """A ticker patched into trend-follow must not leak to a different
        strategy, and the same ticker on a different interval must fall
        through. Both subcases resolve to the global default."""
        with _patched("strategy-trend-follow", ("hl:NOT_IN_TABLE", "4h"), 9):
            v_liq = ct.lookup_min_conviction("strategy-liquidity-sweep", "hl:NOT_IN_TABLE", "4h")
            v_int = ct.lookup_min_conviction("strategy-trend-follow", "hl:NOT_IN_TABLE", "1h")
        assert v_liq == ct.GLOBAL_MIN_CONVICTION_TO_EMIT
        assert v_int == ct.GLOBAL_MIN_CONVICTION_TO_EMIT

    def test_unknown_interval_returns_default(self):
        """Even if (ticker) is overridden for 4h, asking for the same
        ticker on a different interval must fall through."""
        with _patched("strategy-trend-follow", ("hl:X", "4h"), 7):
            v = ct.lookup_min_conviction("strategy-trend-follow", "hl:X", "1d")
        assert v == ct.GLOBAL_MIN_CONVICTION_TO_EMIT

    def test_override_round_trip(self):
        """Mutating then restoring an entry must leave the table
        unchanged between calls."""
        with _patched("strategy-trend-follow", ("hl:TEMP", "1d"), 8):
            assert ct.lookup_min_conviction("strategy-trend-follow", "hl:TEMP", "1d") == 8
        assert ct.lookup_min_conviction("strategy-trend-follow", "hl:TEMP", "1d") == ct.GLOBAL_MIN_CONVICTION_TO_EMIT

    def test_zero_value_is_opt_out(self):
        """A threshold of 0 must round-trip through the lookup, signalling
        that the consumer (the L3) should disable its filter."""
        with _patched("strategy-trend-follow", ("hl:OPTOUT", "1d"), 0):
            assert ct.lookup_min_conviction("strategy-trend-follow", "hl:OPTOUT", "1d") == 0

    def test_returns_int_not_bool(self):
        """Lookup must return an int, not the singleton ``True``/``False``
        or any other type that might confuse downstream arithmetic."""
        with _patched("strategy-trend-follow", ("hl:INTV", "1d"), 4):
            v = ct.lookup_min_conviction("strategy-trend-follow", "hl:INTV", "1d")
        assert isinstance(v, int) and not isinstance(v, bool)


def _write_cfg(tmp_path, payload: dict) -> Path:
    p = tmp_path / "ct.json"
    p.write_text(json.dumps(payload))
    return p


@contextmanager
def _patched(strategy_name: str, key: tuple[str, str], value: int) -> Iterator[None]:
    """Mutate the central table to set ``(strategy_name, ticker, interval)``
    to ``value`` for the duration of the ``with`` block, restoring on exit."""
    table = ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY
    original = table.get(strategy_name, {}).get(key)
    bucket = table.setdefault(strategy_name, {})
    bucket[key] = value
    try:
        yield
    finally:
        if original is None:
            bucket.pop(key, None)
            if not bucket:
                table.pop(strategy_name, None)
        else:
            bucket[key] = original


@contextmanager
def _snapshot() -> Iterator[None]:
    """Snapshot the module state, restore on exit.

    Used by env-var tests that may mutate ``GLOBAL_MIN_CONVICTION_TO_EMIT``
    and ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY``. ``os.environ`` is left
    alone — ``monkeypatch`` handles that.
    """
    saved_global = ct.GLOBAL_MIN_CONVICTION_TO_EMIT
    saved_table: dict[str, dict[tuple[str, str], int]] = {
        k: dict(v) for k, v in ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.items()
    }
    try:
        yield
    finally:
        ct.GLOBAL_MIN_CONVICTION_TO_EMIT = saved_global
        ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.clear()
        ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.update(saved_table)
