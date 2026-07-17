"""Per-fix fixture for strategy-trend-follow's per-(ticker, interval)
conviction gate (beads market-skills-hem and market-skills-oin).

The pre-``oin`` fixture pinned
``MIN_CONVICTION_TO_EMIT`` as a module-level integer default. The
``oin`` refactor moved both the default and the per-(ticker, interval)
overrides into ``analysis.conviction_thresholds``; this file was rewritten
to test through that module so the lib stops exposing a bare int.

Cases:

- :class:`TestLookup`: per-strategy independence, default fallback,
  override precedence, ``GLOBAL_MIN_CONVICTION_TO_EMIT`` contract.
- :class:`TestGateEndToEnd`: the gate actually drops low-conviction
  ideas (set threshold to ``>= 2`` via the central table, drive the
  pipeline through canned L2 stubs, assert emit-set shrinks).
- :class:`TestOptOut`: returning ``0`` from the lookup disables the
  filter entirely (legacy emit-all).

These tests intentionally mutate the central
``MIN_CONVICTION_TO_EMIT_BY_STRATEGY`` table and restore it on exit
so production callers see a pristine module after the run.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from contextlib import contextmanager

import analysis.conviction_thresholds as ct


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "strategy-trend-follow", "lib.py")
    spec = importlib.util.spec_from_file_location("strategy_trend_follow_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_uptrend_candles(base=100.0, n=250, drift=0.0005, seed=7):
    """Mild uptrend that does not trigger Pattern S late-move (>50%).

    drift=0.005 over 250 candles would yield ~125% move maturity →
    late-move downgrade. drift=0.0005 keeps move maturity below 30%
    (mature-move threshold) so Pattern S does not down-grade conviction.
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


@contextmanager
def _patched_threshold(strategy_name: str, ticker: str, interval: str, value: int) -> Iterator[None]:
    """Mutate the central table to set ``(strategy_name, ticker, interval)``
    to ``value`` for the duration of the ``with`` block, restoring on exit."""
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
        """Global default must stay at the legacy no-op floor so behaviour
        for unknown (ticker, interval) pairs is unchanged. The shipped
        source ships with this default; the env var override can change
        it (see test_conviction_thresholds.TestEnvVarLoading)."""
        assert ct.GLOBAL_MIN_CONVICTION_TO_EMIT == 1, (
            f"GLOBAL_MIN_CONVICTION_TO_EMIT must remain 1 (legacy no-op); got "
            f"{ct.GLOBAL_MIN_CONVICTION_TO_EMIT}. Raising this constant would "
            f"affect every strategy / (ticker, interval) without an explicit "
            f"override. To raise per-(ticker, interval), set GLOBAL_MIN_CONVICTION_TO_EMIT "
            f"or add entries via the JSON at $MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH."
        )

    def test_unknown_ticker_falls_through_to_global_default(self):
        """Anything not in the table must resolve to the global default
        (1 = no-op), preserving the legacy emit-all behaviour."""
        assert (
            ct.lookup_min_conviction("strategy-trend-follow", "hl:NEVER_SEEN", "1d") == ct.GLOBAL_MIN_CONVICTION_TO_EMIT
        )

    def test_unknown_strategy_falls_through_to_global_default(self):
        """An unknown strategy name must not crash — it just returns the
        global default. Strategies opt in by populating their bucket."""
        assert (
            ct.lookup_min_conviction("strategy-does-not-exist", "hl:<PRIVATE_PERP>", "4h")
            == ct.GLOBAL_MIN_CONVICTION_TO_EMIT
        )

    def test_per_strategy_tables_are_independent(self):
        """Setting trend-follow's (ticker, interval) to a non-default value
        must NOT leak into liq-sweep's lookup of the same key."""
        with _patched_threshold("strategy-trend-follow", "hl:<PRIVATE_PERP>", "4h", 99):
            v = ct.lookup_min_conviction("strategy-liquidity-sweep", "hl:<PRIVATE_PERP>", "4h")
        assert v == ct.GLOBAL_MIN_CONVICTION_TO_EMIT, (
            "liq-sweep must NOT inherit trend-follow's threshold; each strategy carries its own bucket"
        )

    def test_override_then_fallback_round_trip(self):
        """Adding then removing an override must restore the global default."""
        with _patched_threshold("strategy-trend-follow", "hl:TEMP", "1h", 5):
            assert ct.lookup_min_conviction("strategy-trend-follow", "hl:TEMP", "1h") == 5
        assert ct.lookup_min_conviction("strategy-trend-follow", "hl:TEMP", "1h") == ct.GLOBAL_MIN_CONVICTION_TO_EMIT


class TestGateEndToEnd:
    """The gate must drop low-conviction ideas through the live lib call."""

    @staticmethod
    def _install(monkeypatch, *, trend_confidence: int):
        """Patch ``analysis.skill_loader.load_skill`` to return canned
        breakout (always absent) + canned trend-quality stubs."""
        import analysis.skill_loader as sl

        bo_mod = type(
            "BO",
            (),
            {"analyze": staticmethod(lambda c, **_kw: {"pattern": {"present": False}})},
        )()
        tq_payload = {
            "pattern": {
                "present": True,
                "confidence": trend_confidence,
                "max_confidence": 5,
                "classification": "HEALTHY_UPTREND",
                "type": "TREND_QUALITY",
            },
            "narrative": "stub",
            "input_scores": {},
        }
        tq_mod = type(
            "TQ",
            (),
            {"analyze": staticmethod(lambda c, **_kw: tq_payload)},
        )()
        canned = {"market-trend-quality": tq_mod, "market-breakout": bo_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

    def test_drops_low_conviction_via_table_override(self, monkeypatch):
        """A per-(ticker, interval) override of MIN=5 must drop a
        confidence=3 trend-quality input. Demonstrates the ``oin``
        plumbing works end-to-end."""
        self._install(monkeypatch, trend_confidence=3)
        # Patch BEFORE _load_lib() because lib binds load_skill at import time.
        mod = _load_lib()
        with _patched_threshold("strategy-trend-follow", "hl:HIGHGATE", "1d", 5):
            result = mod.analyze(
                _make_uptrend_candles(),
                ticker="hl:HIGHGATE",
                interval="1d",
                period="1y",
            )
        assert result["ideas"] == [], f"Override MIN=5 must drop conv=3 trend-quality idea; got {result['ideas']}"

    def test_passes_high_conviction_via_global_default(self, monkeypatch):
        """Global default (1) must let every analyzed idea through —
        the legacy emit-all behaviour that the gate is opt-in to change."""
        self._install(monkeypatch, trend_confidence=4)
        mod = _load_lib()
        result = mod.analyze(
            _make_uptrend_candles(),
            ticker="hl:NORMAL",
            interval="1d",
            period="1y",
        )
        assert result["ideas"], "With the global default (1 = no-op), the gate must NOT suppress any analyzed idea"
        assert all(i["conviction"] >= 1 for i in result["ideas"])

    def test_table_override_shifts_pipeline(self, monkeypatch):
        """A per-(ticker, interval) override of MIN=4 must change the
        lib's filter behaviour vs. an unknown ticker (default = 1). Drive
        the same fixture on both and compare. Replaces the pre-czr
        "<PRIVATE_PERP> 4h = 4" shipped-override test — the source no longer
        carries hardcoded entries, so the override is patched here."""
        self._install(monkeypatch, trend_confidence=3)
        mod = _load_lib()
        candles = _make_uptrend_candles()
        with _patched_threshold("strategy-trend-follow", "hl:OVERRIDE", "4h", 4):
            r_override = mod.analyze(candles, ticker="hl:OVERRIDE", interval="4h", period="1y")
        # Outside the override, global default (1) keeps the idea.
        r_default = mod.analyze(candles, ticker="hl:UNKNOWN", interval="4h", period="1y")
        assert r_override["ideas"] == [], f"MIN=4 should drop conf=3 idea; got {r_override['ideas']}"
        assert r_default["ideas"], "Unknown ticker 4h must fall back to global default (1) and emit the idea"


class TestOptOut:
    """The opt-out path (MIN=0) must disable the filter entirely."""

    def test_zero_threshold_restores_legacy_emit_all(self, monkeypatch):
        """Setting the lookup to 0 for a specific (ticker, interval)
        must disable the gate for that combination. Demonstrates that
        the gate stays opt-out."""
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
            "market-trend-quality": type(
                "TQ",
                (),
                {"analyze": staticmethod(lambda c, **_kw: low_tq)},
            )(),
            "market-breakout": bo_mod,
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_lib()
        with _patched_threshold("strategy-trend-follow", "hl:OPTOUT", "1d", 0):
            result = mod.analyze(
                _make_uptrend_candles(),
                ticker="hl:OPTOUT",
                interval="1d",
                period="1y",
            )
        assert result["ideas"], f"Opt-out (lookup = 0) must restore legacy emit-all; got {result['ideas']}"
        assert any(i["conviction"] <= 2 for i in result["ideas"])
