"""Per-fix fixture for strategy-liquidity-sweep's per-(ticker, interval)
conviction gate (beads market-skills-96y and market-skills-oin).

The pre-``oin`` fixture pinned ``MIN_CONVICTION_TO_EMIT`` as a
module-level integer default. The ``oin`` refactor moved both the
default and the per-(ticker, interval) overrides into
``analysis.conviction_thresholds``; this file was rewritten to test
through that module so the lib stops exposing a bare int.

Cases mirror the trend-follow gate test file with the liq-sweep
specifics:

- :class:`TestLookup`: liq-sweep bucket is empty by design until journal
  evidence lands; lookup must return the global default for every
  (ticker, interval).
- :class:`TestGateEndToEnd`: driving the pipeline with two canned L2
  inputs and raising the lookup above the natural conviction floor must
  drop the lower-conviction idea end-to-end. Covers both formulas via
  ``conviction_mode`` kwarg.
- :class:`TestOptOut`: returning ``0`` from the lookup disables the
  filter entirely (legacy emit-all).

The fixture mutates the central table and restores it on exit; callers
that read the table outside a ``with`` block see a pristine module.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from contextlib import contextmanager

import analysis.conviction_thresholds as ct


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "strategy-liquidity-sweep", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_liquidity_sweep_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=200, base=100.0):
    import random

    rng = random.Random(7)
    out = []
    price = base
    for i in range(n):
        open_p = price
        close_p = price + rng.uniform(-1.0, 1.0)
        high_p = max(open_p, close_p) + 1.5
        low_p = min(open_p, close_p) - 1.5
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 200_000])
    return out


def _stub(name, payload):
    return type(name, (), {"analyze": staticmethod(lambda c, **_kw: payload)})()


def _stub_pair(sweep_conf: int, accum_conf: int):
    """Build canned L2 stubs with given confidences + a permissive volume."""
    return {
        "market-liquidity-sweep": _stub(
            "S",
            {
                "pattern": {
                    "present": True,
                    "confidence": sweep_conf,
                    "max_confidence": 5,
                    "classification": "FRESH",
                    "type": "LIQUIDITY_SWEEP",
                },
                "narrative": "stub",
                "input_scores": {},
            },
        ),
        "market-accumulation": _stub(
            "A",
            {
                "pattern": {
                    "present": True,
                    "confidence": accum_conf,
                    "max_confidence": 5,
                    "classification": "ACCUMULATING",
                    "type": "ACCUMULATION",
                },
                "narrative": "stub",
                "input_scores": {},
            },
        ),
        "market-volume": _stub("V", {"volume_ratio": 1.5, "obv_trend": "rising"}),
    }


def _install(monkeypatch, canned):
    import analysis.skill_loader as sl

    monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))


@contextmanager
def _patched_threshold(strategy_name: str, ticker: str, interval: str, value: int) -> Iterator[None]:
    table = ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY
    original = table.get(strategy_name, {}).get((ticker, interval))
    bucket = table.setdefault(strategy_name, {})
    bucket[(ticker, interval)] = value
    try:
        yield
    finally:
        if original is None:
            bucket.pop((ticker, interval), None)
            if not bucket:
                table.pop(strategy_name, None)
        else:
            bucket[(ticker, interval)] = original


class TestLookup:
    """The shared lookup table must keep its contract post-oin / post-czr."""

    def test_global_default_is_one(self):
        assert ct.GLOBAL_MIN_CONVICTION_TO_EMIT == 1, (
            f"GLOBAL_MIN_CONVICTION_TO_EMIT must remain 1 (legacy no-op); got "
            f"{ct.GLOBAL_MIN_CONVICTION_TO_EMIT}. Raising this constant would "
            f"affect every strategy / (ticker, interval) without an explicit "
            f"override."
        )

    def test_per_strategy_tables_are_independent(self):
        """Setting trend-follow's (ticker, interval) to a non-default value
        must NOT leak into liq-sweep's lookup of the same key. Replaces
        the pre-czr "liq-sweep's bucket is empty" pin — the bucket is
        now populated from $MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH at
        import time, so the empty-source assertion no longer holds in
        isolation. Patch-trend-follow-then-query-liq-sweep is the actual
        isolation contract."""
        with _patched_threshold("strategy-trend-follow", "hl:<PRIVATE_PERP>", "4h", 99):
            v = ct.lookup_min_conviction("strategy-liquidity-sweep", "hl:<PRIVATE_PERP>", "4h")
        assert v == ct.GLOBAL_MIN_CONVICTION_TO_EMIT, (
            "liq-sweep must NOT inherit trend-follow's threshold; each strategy carries its own bucket"
        )


class TestGateEndToEnd:
    """The gate must drop low-conviction ideas through the live lib call."""

    def test_low_conviction_dropped_via_table_override(self, monkeypatch):
        """Drive the lib with conf=(1, 1); conviction under mode=current
        is 1 and under max_plus_one is 2. An override of MIN=4 must drop
        both end-to-end. Demonstrates ``oin`` plumbing works."""
        canned = _stub_pair(sweep_conf=1, accum_conf=1)
        _install(monkeypatch, canned)
        mod = _load_lib()
        with _patched_threshold("strategy-liquidity-sweep", "hl:TIGHT", "4h", 4):
            r_current = mod.analyze(
                _make_candles(),
                ticker="hl:TIGHT",
                interval="4h",
                conviction_mode="current",
            )
            r_max = mod.analyze(
                _make_candles(),
                ticker="hl:TIGHT",
                interval="4h",
                conviction_mode="max_plus_one",
            )
        assert r_current["ideas"] == [], (
            f"Override MIN=4 must drop conf=(1,1) idea under current mode; got {r_current['ideas']}"
        )
        assert r_max["ideas"] == [], (
            f"Override MIN=4 must drop conf=(1,1) idea under max_plus_one; got {r_max['ideas']}"
        )

    def test_high_conviction_passes_via_global_default(self, monkeypatch):
        """conf=(5,5) caps at 5 under any mode. Global default (1) keeps it."""
        canned = _stub_pair(sweep_conf=5, accum_conf=5)
        _install(monkeypatch, canned)
        mod = _load_lib()
        result = mod.analyze(
            _make_candles(),
            ticker="hl:OPEN",
            interval="4h",
            conviction_mode="current",
        )
        assert result["ideas"], f"Global default (1) must keep all surviving ideas; got {result['ideas']}"
        assert result["ideas"][0]["conviction"] == 5

    def test_per_strategy_prevents_cross_strategy_leak(self, monkeypatch):
        """A trend-follow override for ticker X must NOT change liq-sweep
        behaviour on the same ticker. Catches the regression where both
        lib files end up reading the same dict key."""
        canned = _stub_pair(sweep_conf=1, accum_conf=1)
        _install(monkeypatch, canned)
        mod = _load_lib()
        # Apply ONLY to trend-follow's bucket.
        with _patched_threshold("strategy-trend-follow", "hl:<PRIVATE_PERP>", "4h", 99):
            result = mod.analyze(
                _make_candles(),
                ticker="hl:<PRIVATE_PERP>",
                interval="4h",
                conviction_mode="current",
            )
        # liq-sweep must still emit (its bucket is empty; default = 1 = no-op).
        assert result["ideas"], (
            f"liq-sweep must be unaffected by trend-follow's <PRIVATE_PERP> 4h override; got {result['ideas']}"
        )


class TestOptOut:
    """The opt-out path (MIN=0) must disable the filter entirely."""

    def test_zero_threshold_restores_legacy_emit_all(self, monkeypatch):
        """Setting the lookup to 0 for a specific (ticker, interval)
        must disable the gate for that combination."""
        canned = _stub_pair(sweep_conf=1, accum_conf=1)
        _install(monkeypatch, canned)
        mod = _load_lib()
        with _patched_threshold("strategy-liquidity-sweep", "hl:OPTOUT", "4h", 0):
            result = mod.analyze(
                _make_candles(),
                ticker="hl:OPTOUT",
                interval="4h",
                conviction_mode="current",
            )
        assert result["ideas"], f"Opt-out (lookup = 0) must restore legacy emit-all; got {result['ideas']}"
        # current mode with conf=(1,1) → conviction=1; gate=0 = no-op → emits.
        assert result["ideas"][0]["conviction"] == 1
