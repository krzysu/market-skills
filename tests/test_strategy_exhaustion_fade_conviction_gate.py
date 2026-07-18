"""Per-fix fixture for strategy-exhaustion-fade's per-(ticker, interval)
conviction gate (beads market-skills-6th and market-skills-oin).

Mirrors ``tests/test_strategy_trend_follow_conviction_gate.py`` with the
exhaustion-fade specifics. Before ``6th`` this strategy emitted every
analyzed idea unfiltered, so the centralised threshold table could not
tune it. The gate now drops low-conviction ideas at the end of
``analyze()`` via :func:`lookup_min_conviction`.

Cases:

- :class:`TestGateEndToEnd`: the global default (``1`` = no-op) preserves
  the legacy emit-all behaviour; raising the lookup above the natural
  conviction floor drops the low-conviction idea end-to-end; a per-(ticker,
  interval) override shifts the pipeline vs. an unknown ticker (the same
  lib instance reads the central table dynamically on each ``analyze()``
  call, proving the gate is not captured at import time).
- :class:`TestOptOut`: returning ``0`` from the lookup disables the filter
  entirely (legacy emit-all).

The fixture mutates the central ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY``
table and restores it on exit; production callers see a pristine module
after the run.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from contextlib import contextmanager

import analysis.conviction_thresholds as ct


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "strategy-exhaustion-fade", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_exhaustion_fade_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=250, base=100.0, half_range=1.5, lock_close=100.3, seed=42):
    """Build [ts, open, high, low, close, volume] candles with the final
    close pinned via ``lock_close`` so price sits at/above the canned
    resistance (``100.0``) and the short blowoff entry condition
    (``price >= resistance * 0.98``) fires deterministically.

    ``half_range=1.5`` ensures ATR-derived stops clear the 2% swing-minimum
    floor so the surviving idea is not dropped by the stop-distance filter
    before the conviction gate runs.
    """
    import random

    rng = random.Random(seed)
    out = []
    price = base
    for i in range(n):
        open_p = price
        close_p = price + rng.uniform(-1.0, 1.0)
        high_p = max(open_p, close_p) + half_range
        low_p = min(open_p, close_p) - half_range
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 200_000])
    if lock_close is not None:
        prev_close = out[-2][4]
        out[-1] = [
            (n - 1) * 86400,
            prev_close,
            max(prev_close, lock_close) + 0.1,
            min(prev_close, lock_close) - 0.1,
            lock_close,
            200_000,
        ]
    return out


def _stub(name, payload):
    return type(name, (), {"analyze": staticmethod(lambda c, **_kw: payload)})()


def _canned(exh_conf: int = 3):
    """Build canned L2 stubs that fire one short blowoff-fade idea.

    ``BLOWOFF_TOP`` classification + resistance (``100.0``) + positive
    trend score unlocks the short branch. With ``exh_conf=3`` the L3
    conviction formula is ``min(5, 3) = 3`` — a low-conviction idea that a
    raised threshold (``>= 4``) must drop.
    """
    return {
        "market-exhaustion": _stub(
            "E",
            {
                "pattern": {
                    "present": True,
                    "confidence": exh_conf,
                    "max_confidence": 5,
                    "classification": "BLOWOFF_TOP",
                    "type": "EXHAUSTION",
                },
                "narrative": "stub",
                "input_scores": {},
            },
        ),
        "market-s-r": _stub("S", {"nearest_support": None, "nearest_resistance": 100.0}),
        "market-trend": _stub("T", {"score": 1}),
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


_STRATEGY = "strategy-exhaustion-fade"


class TestGateEndToEnd:
    """The gate must drop low-conviction ideas through the live lib call."""

    def test_default_global_one_preserves_legacy_emit_all(self, monkeypatch):
        """Global default (``1`` = no-op) must let every analyzed idea
        through — the legacy emit-all behaviour the gate is opt-in to
        change."""
        _install(monkeypatch, _canned())
        mod = _load_lib()
        result = mod.analyze(_make_candles(), ticker="hl:NORMAL", interval="1d", period="1y")
        assert result["ideas"], f"Global default (1) must keep the surviving idea; got {result['ideas']}"
        assert result["ideas"][0]["conviction"] == 3, (
            f"Stub produces conviction=3 (exh 3); got {result['ideas'][0]['conviction']}"
        )

    def test_raised_threshold_drops_low_conviction_idea(self, monkeypatch):
        """A per-(ticker, interval) override of MIN=5 must drop the
        conviction=3 idea end-to-end."""
        _install(monkeypatch, _canned())
        mod = _load_lib()
        with _patched_threshold(_STRATEGY, "hl:HIGHGATE", "1d", 5):
            result = mod.analyze(_make_candles(), ticker="hl:HIGHGATE", interval="1d", period="1y")
        assert result["ideas"] == [], f"Override MIN=5 must drop conv=3 idea; got {result['ideas']}"

    def test_override_shifts_pipeline_vs_unknown_ticker(self, monkeypatch):
        """A per-(ticker, interval) override of MIN=5 must change the lib's
        filter behaviour vs. an unknown ticker (default = 1). The same lib
        module is loaded once; the threshold is read dynamically on each
        ``analyze()`` call, so mutating the central table shifts the output
        — proving the gate is not captured at import time."""
        _install(monkeypatch, _canned())
        mod = _load_lib()
        candles = _make_candles()
        with _patched_threshold(_STRATEGY, "hl:OVERRIDE", "4h", 5):
            r_override = mod.analyze(candles, ticker="hl:OVERRIDE", interval="4h", period="1y")
        r_default = mod.analyze(candles, ticker="hl:UNKNOWN", interval="4h", period="1y")
        assert r_override["ideas"] == [], f"MIN=5 should drop conv=3 idea; got {r_override['ideas']}"
        assert r_default["ideas"], "Unknown ticker 4h must fall back to global default (1) and emit the idea"


class TestOptOut:
    """The opt-out path (MIN=0) must disable the filter entirely."""

    def test_zero_threshold_restores_legacy_emit_all(self, monkeypatch):
        """Setting the lookup to 0 for a specific (ticker, interval) must
        disable the gate for that combination (``>= 0`` matches all)."""
        _install(monkeypatch, _canned())
        mod = _load_lib()
        with _patched_threshold(_STRATEGY, "hl:OPTOUT", "1d", 0):
            result = mod.analyze(_make_candles(), ticker="hl:OPTOUT", interval="1d", period="1y")
        assert result["ideas"], f"Opt-out (lookup = 0) must restore legacy emit-all; got {result['ideas']}"
        assert result["ideas"][0]["conviction"] == 3
