"""Per-fix fixture for the ``MIN_CONVICTION_TO_EMIT`` gate on
``strategy-liquidity-sweep`` (bead market-skills-96y).

Mirrors the pattern from
``tests/test_strategy_trend_follow_conviction_gate.py`` (the hem gate).
Three cases: default preserves legacy (1 = no-op because the conviction
formula's natural floor on integer L2 confidences is 1); raising the
threshold drops low-conviction ideas in-place; setting threshold = 0 opt-out.

The gate exists because, per the per-band backtest evidence (96y
description, see also 7eq notes), ``conviction`` is information-only: the
backtest-engine's FillSimulator doesn't read it. Without a gate, the
conviction formula choice is invariant in backtest Sharpe. The gate makes
conviction load-bearing so per-ticker thresholds become a data-driven
decision rather than ceremony.
"""

from __future__ import annotations

import importlib.util
import os


def _load_lib():
    lib_path = os.path.join(
        os.path.dirname(__file__), "..", "skills", "strategy-liquidity-sweep", "lib.py"
    )
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
        "market-volume": _stub(
            "V", {"volume_ratio": 1.5, "obv_trend": "rising"}
        ),
    }


def _install(monkeypatch, canned):
    import analysis.skill_loader as sl

    monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))


class TestConvictionGate:
    """Mirrors the trend-follow gate tests but applied to liq-sweep."""

    def test_default_one_preserves_legacy(self):
        """Default MIN=1 must keep all ideas (formula's natural floor is 1).
        This is the documented shipping policy."""
        mod = _load_lib()
        assert mod.MIN_CONVICTION_TO_EMIT == 1, (
            f"MIN_CONVICTION_TO_EMIT default must be 1 (legacy); got "
            f"{mod.MIN_CONVICTION_TO_EMIT}. Raise via a follow-up commit "
            f"after per-ticker validation."
        )

    def test_filter_drops_low_conviction_in_place(self, monkeypatch):
        """With sweep=2, accum=1 and mode=current: raw=2+1//2=2+0=2.
        With sweep=1, accum=1 and mode=current: raw=1+1//2=1+0=1.
        Use conf=(1,1) under mode='current' so the natural conviction is 1,
        well below gate=4. Pre-fix (no gate), the idea would have been
        returned regardless of conviction; post-fix the gate must drop it.
        """
        canned = _stub_pair(sweep_conf=1, accum_conf=1)
        _install(monkeypatch, canned)
        mod = _load_lib()
        original = mod.MIN_CONVICTION_TO_EMIT
        mod.MIN_CONVICTION_TO_EMIT = 4
        try:
            ideas = mod.analyze(
                _make_candles(), ticker="T", conviction_mode="current"
            )
        finally:
            mod.MIN_CONVICTION_TO_EMIT = original

        assert ideas["ideas"] == [], (
            f"conf=(1,1) → conviction=1 (current); gate=4 must drop it. "
            f"Got {ideas['ideas']}"
        )

    def test_filter_passes_high_conviction_in_place(self, monkeypatch):
        """conf=(5,5) under any mode caps at 5. With gate=4, the high-conviction
        idea must survive."""
        canned = _stub_pair(sweep_conf=5, accum_conf=5)
        _install(monkeypatch, canned)
        mod = _load_lib()
        original = mod.MIN_CONVICTION_TO_EMIT
        mod.MIN_CONVICTION_TO_EMIT = 4
        try:
            ideas = mod.analyze(
                _make_candles(), ticker="T", conviction_mode="current"
            )
        finally:
            mod.MIN_CONVICTION_TO_EMIT = original

        assert ideas["ideas"], (
            f"conf=(5,5) → conviction=5; gate=4 must keep it. Got {ideas['ideas']}"
        )
        assert ideas["ideas"][0]["conviction"] == 5

    def test_opt_out_via_zero(self, monkeypatch):
        """Setting MIN=0 should bypass the filter entirely (the post-processing
        filter is gated on MIN > 1). This guards the opt-out escape hatch."""
        canned = _stub_pair(sweep_conf=1, accum_conf=1)
        _install(monkeypatch, canned)
        mod = _load_lib()
        original = mod.MIN_CONVICTION_TO_EMIT
        mod.MIN_CONVICTION_TO_EMIT = 0
        try:
            ideas = mod.analyze(
                _make_candles(), ticker="T", conviction_mode="current"
            )
        finally:
            mod.MIN_CONVICTION_TO_EMIT = original

        # current mode with conf=(1,1) → conviction=1; gate=0 = no filter → emits.
        assert ideas["ideas"], (
            f"Opt-out (MIN=0) must restore legacy emit-all; got {ideas['ideas']}"
        )
        assert ideas["ideas"][0]["conviction"] == 1
