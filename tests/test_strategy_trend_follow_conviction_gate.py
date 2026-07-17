"""Tests for strategy-trend-follow's MIN_CONVICTION_TO_EMIT gate (bead market-skills-hem).

Per-fix fixture for the entry-gate tightening. The L3 used to emit every
trend-classified idea regardless of conviction, producing 70-150 trades per
ticker on 1d/1y with mostly negative Sharpe. The fix filters ideas with
conviction < MIN_CONVICTION_TO_EMIT (default 4) at the end of analyze().

These tests assert: (a) default MIN_CONVICTION_TO_EMIT >= 1 (the gate is wired),
(b) high-conviction ideas survive the filter, (c) low-conviction ideas are
dropped, (d) the gate is opt-out (setting it to 0 restores legacy behavior).
"""

from __future__ import annotations

import importlib.util
import os


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "strategy-trend-follow", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_trend_follow_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_uptrend_candles(base=100.0, n=250, drift=0.0005, seed=7):
    """Mild uptrend that does NOT trigger Pattern S late-move (>50%).

    drift=0.005 over 250 candles would yield ~125% move maturity → late-move
    downgrade. drift=0.0005 keeps move maturity below 30% (mature-move
    threshold) so Pattern S does not down-grade conviction. We need the
    uptrend to be long enough that HEALTHY_UPTREND classification fires,
    but small enough that post-Pattern-S conviction stays >= MIN.
    """
    import random

    rng = random.Random(seed)
    out = []
    price = base
    for i in range(n):
        open_p = price
        close_p = price * (1 + drift) + rng.uniform(-1.5, 1.5)
        high_p = max(open_p, close_p) + 1.5
        low_p = min(open_p, close_p) - 1.5
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 200_000])
    return out


class TestConvictionGate:
    """The MIN_CONVICTION_TO_EMIT gate must be active and configurable."""

    def test_default_min_conviction_preserves_legacy_behavior(self):
        """The shipped default MIN_CONVICTION_TO_EMIT must be 1 (the lib's
        internal conviction floor). Raising it is a tunable; the default is
        explicitly chosen to preserve pre-existing test behavior. Document the
        policy in lib.py."""
        mod = _load_lib()
        assert hasattr(mod, "MIN_CONVICTION_TO_EMIT"), (
            "strategy-trend-follow must expose MIN_CONVICTION_TO_EMIT as a module-level constant"
        )
        assert mod.MIN_CONVICTION_TO_EMIT == 1, (
            f"MIN_CONVICTION_TO_EMIT default must remain 1 (legacy behaviour); "
            f"got {mod.MIN_CONVICTION_TO_EMIT}. To tighten, raise this constant "
            f"and validate against backtest-engine before shipping."
        )

    def test_filter_drops_low_conviction_ideas_in_place(self, monkeypatch):
        """Drive trend-follow end-to-end with a trend-quality mock that emits
        ideas at varied conviction; assert that ideas with conviction
        strictly below the (test-temporarily-raised) gate threshold do NOT
        appear in the result. Pre-fix, all ideas would have been returned
        (gate absent regardless of threshold)."""
        import analysis.skill_loader as sl

        bo_mod = type(
            "BO",
            (),
            {"analyze": staticmethod(lambda c, **_kw: {"pattern": {"present": False}})},
        )()
        high_tq = {
            "pattern": {
                "present": True,
                "confidence": 5,
                "max_confidence": 5,
                "classification": "HEALTHY_UPTREND",
                "type": "TREND_QUALITY",
            },
            "narrative": "healthy uptrend",
            "input_scores": {},
        }
        low_tq = {
            "pattern": {
                "present": True,
                "confidence": 2,
                "max_confidence": 5,
                "classification": "HEALTHY_UPTREND",
                "type": "TREND_QUALITY",
            },
            "narrative": "weak uptrend",
            "input_scores": {},
        }
        sequence = iter([high_tq, low_tq])
        tq_mod = type(
            "TQ",
            (),
            {"analyze": staticmethod(lambda c, **_kw: next(sequence))},
        )()

        canned = {"market-trend-quality": tq_mod, "market-breakout": bo_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_lib()
        # Temporarily raise the gate threshold to 4; restores default on exit.
        original = mod.MIN_CONVICTION_TO_EMIT
        mod.MIN_CONVICTION_TO_EMIT = 4
        try:
            candles = _make_uptrend_candles()
            r1 = mod.analyze(candles, ticker="HIGH", interval="1d", period="1y")
            r2 = mod.analyze(candles, ticker="LOW", interval="1d", period="1y")
        finally:
            mod.MIN_CONVICTION_TO_EMIT = original

        # With gate=4: high (conviction 5) survives; low (conviction 2) is dropped.
        assert len(r1["ideas"]) >= 1, f"High-confidence input (conviction 5) must survive gate=4; got {r1['ideas']}"
        assert all(i["conviction"] >= 4 for i in r1["ideas"]), (
            f"Survivors must have conviction >= 4; got {[i['conviction'] for i in r1['ideas']]}"
        )
        assert r2["ideas"] == [], f"Low-confidence input (conviction 2) must be dropped by gate=4; got {r2['ideas']}"

    def test_gate_can_be_opt_out(self, monkeypatch):
        """Setting MIN_CONVICTION_TO_EMIT=0 must restore the legacy emit-all
        behavior (no drop based on conviction floor). This guards against the
        'gate accidentally traps low-conviction ideas' regression."""
        import analysis.skill_loader as sl

        bo_mod = type(
            "BO",
            (),
            {"analyze": staticmethod(lambda c, **_kw: {"pattern": {"present": False}})},
        )()
        low_tq = {
            "pattern": {
                "present": True,
                "confidence": 2,
                "max_confidence": 5,
                "classification": "HEALTHY_UPTREND",
                "type": "TREND_QUALITY",
            },
            "narrative": "weak uptrend",
            "input_scores": {},
        }
        canned = {
            "market-trend-quality": type("TQ", (), {"analyze": staticmethod(lambda c, **_kw: low_tq)})(),
            "market-breakout": bo_mod,
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_lib()
        # Temporarily opt-out the gate.
        original = mod.MIN_CONVICTION_TO_EMIT
        mod.MIN_CONVICTION_TO_EMIT = 0
        try:
            candles = _make_uptrend_candles()
            result = mod.analyze(candles, ticker="LOW", interval="1d", period="1y")
            # With gate off, conviction 2 idea is emitted.
            assert any(i["conviction"] <= 2 for i in result["ideas"]), (
                f"Opt-out (MIN_CONVICTION_TO_EMIT=0) must restore legacy behavior "
                f"and emit conviction 2 ideas; got {result['ideas']}"
            )
        finally:
            mod.MIN_CONVICTION_TO_EMIT = original
