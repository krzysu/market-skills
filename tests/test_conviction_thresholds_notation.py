"""Per-fix fixture for the conviction-gate ticker-notation mismatch.

The thresholds table is written keyed on ``provider:ticker`` (the
backtest pipeline normalises raw symbols into e.g. ``kraken:PENDLEUSD``),
but the emit paths (``run-all-l3``, ``l3-conviction-scan``) hand the
strategy whatever ticker the runner holds — for Kraken pairs that is the
bare symbol (``PENDLEUSD``). The lookup was an exact
``(ticker, interval)`` match, so every ``kraken:*`` floor silently
missed and fell through to ``GLOBAL_MIN_CONVICTION_TO_EMIT`` (=1, the
no-op): setups the nightly backtest explicitly rejected (floor 99 ==
negative Sharpe) reached the surface tradeable.

Pre-fix failures in this file:

- :class:`TestDirectLookupNotation` — ``lookup_min_conviction`` with a
  bare ``BTCUSD`` / separator ``BTC-USD`` query against a stored
  ``kraken:BTCUSD`` key returned the global default instead of the
  stored floor.
- :class:`TestRunAllL3EndToEnd` /
  :class:`TestConvictionScanEndToEnd` — an idea emitted for the bare
  ``PENDLEUSD`` survived a floor of 99 written under
  ``kraken:PENDLEUSD`` (both runners).
- :class:`TestFallthroughAccounting` — the fall-through accounting
  helpers did not exist.
- :class:`TestAmbiguity` — the notation-preference/ambiguity rules did
  not exist.

The fix canonicalises the lookup key on the read side only; the stored
table is never re-keyed.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import types
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

import analysis.signals.conviction_thresholds as ct
import analysis.skill_loader as sl
from analysis.contracts import finalize_ideas

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _hermetic_module(monkeypatch):
    """Give every test the shipped module state, whatever the ambient env.

    Mirrors the fixture in ``tests/test_conviction_thresholds.py``:
    ``analysis.signals.conviction_thresholds`` loads overrides at import
    time (explicit env var, or the ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR``
    fallback), so a shell with either exported would pre-populate the
    table with private asset refs. Delete both path vars (plus the gate
    kill switch), reload the module so every test starts from the shipped
    empty state and fresh fall-through counters, then restore the ambient
    table/global on exit.
    """
    saved_global = ct.GLOBAL_MIN_CONVICTION_TO_EMIT
    saved_table: dict[str, dict[tuple[str, str], int]] = {
        k: dict(v) for k, v in ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.items()
    }
    monkeypatch.delenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", raising=False)
    monkeypatch.delenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", raising=False)
    monkeypatch.delenv("MARKET_SKILLS_CONVICTION_GATE", raising=False)
    importlib.reload(ct)
    yield
    ct.GLOBAL_MIN_CONVICTION_TO_EMIT = saved_global
    ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.clear()
    ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.update(saved_table)


@contextmanager
def _patched(strategy_name: str, key: tuple[str, str], value: int) -> Iterator[None]:
    """Set ``(strategy_name, ticker, interval)`` to ``value`` in the central
    table for the duration of the ``with`` block, restoring on exit."""
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


def _load_lib(skill_name: str):
    """Load ``skills/<skill_name>/lib.py`` as a fresh module.

    Loaded inside the ``monkeypatch`` window on
    ``analysis.skill_loader.load_skill`` (see :func:`_fake_loader`), so
    the lib's ``from analysis.skill_loader import load_skill`` captures
    the fake and every ``load_skill(...)`` call inside the lib resolves
    to the canned strategies.
    """
    lib_path = os.path.join(_REPO_ROOT, "skills", skill_name, "lib.py")
    spec = importlib.util.spec_from_file_location(f"notation_{skill_name.replace('-', '_')}_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stub_strategy_module(strategy_name: str) -> types.SimpleNamespace:
    """Build a canned L3 strategy module emitting one conviction=3 idea.

    The idea goes through the real :func:`finalize_ideas` pipeline —
    exactly the shape every real strategy lib uses — so the conviction
    gate inside it runs against the live threshold table with whatever
    ticker the caller handed down.
    """

    def analyze(candles, *, ticker, interval="1d", period="1y", asset_class=None):
        idea = {
            "pair": ticker,
            "direction": "long",
            "conviction": 3,
            "version": "v3",
            "entry_type": "limit",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "take_profit": [110.0, 120.0, 130.0],
            "reasoning": "stub",
            "source_skills": [],
        }
        ideas, rejection = finalize_ideas([idea], strategy_name=strategy_name, ticker=ticker, interval=interval)
        return {"ideas": ideas, "narrative": rejection or "stub idea"}

    return types.SimpleNamespace(analyze=analyze)


def _fake_loader():
    """Build a ``load_skill`` replacement for the end-to-end fixtures.

    The canned stub serves every strategy name; the requested
    ``run-all-l3`` lib is loaded fresh so its from-import binds to this
    fake (making its internal per-strategy loads resolve to the stub).
    """
    canned = {"strategy-accumulation-swing": _stub_strategy_module("strategy-accumulation-swing")}

    def fake(name):
        if name == "run-all-l3":
            return _load_lib("run-all-l3")
        return canned.get(name)

    return fake


_STRATEGY = "strategy-accumulation-swing"


class TestDirectLookupNotation:
    """Direct lookup equivalence across ticker notations."""

    def test_kraken_keyed_floor_binds_for_all_notations(self):
        """Bare ``BTCUSD``, ``BTC-USD`` and ``BTC/USD`` must bind to a
        stored ``kraken:BTCUSD`` key — the smoking gun from the bead
        (bare query returned 1 while the written floor was 99)."""
        with _patched(_STRATEGY, ("kraken:BTCUSD", "1d"), 99):
            for ticker in ("kraken:BTCUSD", "BTCUSD", "BTC-USD", "BTC/USD"):
                floor = ct.lookup_min_conviction(_STRATEGY, ticker, "1d")
                assert floor == 99, (
                    f"lookup({_STRATEGY!r}, {ticker!r}, '1d') must bind to the kraken:BTCUSD floor 99; got {floor}"
                )

    def test_prefix_match_is_case_insensitive(self):
        with _patched(_STRATEGY, ("kraken:BTCUSD", "1d"), 99):
            assert ct.lookup_min_conviction(_STRATEGY, "KRAKEN:btcusd", "1d") == 99


class TestHlRegressionGuard:
    """``hl:``-keyed floors must keep working (qualified and bare)."""

    def test_hl_key_binds_for_qualified_ticker(self):
        with _patched(_STRATEGY, ("hl:LIT", "1d"), 5):
            assert ct.lookup_min_conviction(_STRATEGY, "hl:LIT", "1d") == 5

    def test_hl_key_binds_for_bare_ticker(self):
        """The bare-coin emit path (Hyperliquid keeps its prefix, but a
        bare query must still bind via canonical matching)."""
        with _patched(_STRATEGY, ("hl:LIT", "1d"), 5):
            assert ct.lookup_min_conviction(_STRATEGY, "LIT", "1d") == 5


class TestIntervalIsolation:
    def test_same_symbol_other_interval_falls_through(self):
        """The same ticker on a different interval must still fall
        through to the global default."""
        with _patched(_STRATEGY, ("kraken:BTCUSD", "1d"), 99):
            floor = ct.lookup_min_conviction(_STRATEGY, "BTCUSD", "4h")
        assert floor == ct.GLOBAL_MIN_CONVICTION_TO_EMIT


class TestAmbiguity:
    """Same-symbol candidates that disagree must never be guessed."""

    def test_conflicting_same_symbol_candidates_fall_through(self, monkeypatch):
        monkeypatch.setattr(ct, "GLOBAL_MIN_CONVICTION_TO_EMIT", 3)
        ct.reset_fallthrough_stats()
        with _patched(_STRATEGY, ("kraken:BTCUSD", "1d"), 99), _patched(_STRATEGY, ("hl:BTCUSD", "1d"), 5):
            floor = ct.lookup_min_conviction(_STRATEGY, "BTCUSD", "1d")
        assert floor == 3, "ambiguous same-symbol candidates must fall through, not guess"
        stats = ct.fallthrough_stats()
        assert stats["by_key"].get((_STRATEGY, "BTCUSD", "1d")) == 1

    def test_query_prefix_preference_resolves_conflict(self):
        with _patched(_STRATEGY, ("kraken:BTCUSD", "1d"), 99), _patched(_STRATEGY, ("hl:BTCUSD", "1d"), 5):
            assert ct.lookup_min_conviction(_STRATEGY, "kraken:BTC-USD", "1d") == 99
            assert ct.lookup_min_conviction(_STRATEGY, "hl:BTC-USD", "1d") == 5


class TestFallthroughAccounting:
    """The step-3 fall-through must be observable, not silent."""

    def test_unmatched_counts_and_returns_global(self, monkeypatch):
        monkeypatch.setattr(ct, "GLOBAL_MIN_CONVICTION_TO_EMIT", 4)
        ct.reset_fallthrough_stats()
        with _patched(_STRATEGY, ("hl:KNOWN", "1d"), 7):
            floor = ct.lookup_min_conviction(_STRATEGY, "hl:UNMATCHED", "1d")
        assert floor == 4
        stats = ct.fallthrough_stats()
        assert stats["total"] >= 1
        assert stats["by_key"].get((_STRATEGY, "hl:UNMATCHED", "1d")) == 1

    def test_exact_hit_does_not_count_as_fallthrough(self):
        ct.reset_fallthrough_stats()
        with _patched(_STRATEGY, ("hl:EXACT", "1d"), 7):
            assert ct.lookup_min_conviction(_STRATEGY, "hl:EXACT", "1d") == 7
        assert ct.fallthrough_stats()["total"] == 0

    def test_canonical_hit_does_not_count_as_fallthrough(self):
        ct.reset_fallthrough_stats()
        with _patched(_STRATEGY, ("kraken:BTCUSD", "1d"), 99):
            assert ct.lookup_min_conviction(_STRATEGY, "BTCUSD", "1d") == 99
        assert ct.fallthrough_stats()["total"] == 0

    def test_gate_off_returns_shipped_default_without_counting(self, monkeypatch):
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_GATE", "off")
        ct.reset_fallthrough_stats()
        with _patched(_STRATEGY, ("hl:GATEOFF", "1d"), 99):
            assert ct.lookup_min_conviction(_STRATEGY, "hl:GATEOFF", "1d") == 1
        assert ct.fallthrough_stats()["total"] == 0


class TestRunAllL3EndToEnd:
    """The floor must bind through the real run-all-l3 pipeline."""

    def test_kraken_keyed_floor_drops_bare_ticker_idea(self, monkeypatch):
        """Written floor ``("kraken:PENDLEUSD", "1d") = 99``, emit path
        hands the strategy the bare ``PENDLEUSD`` — the idea must be
        dropped (pre-fix it surfaced with the no-op global floor 1)."""
        monkeypatch.setattr(sl, "load_skill", _fake_loader())
        runall = _load_lib("run-all-l3")
        with _patched(_STRATEGY, ("kraken:PENDLEUSD", "1d"), 99):
            envelope = runall.analyze("PENDLEUSD", [[1, 100, 101, 99, 100, 1000]], interval="1d", period="1y")
        result = envelope["strategies"][_STRATEGY]
        assert result["ideas"] == [], (
            f"kraken:PENDLEUSD 1d floor 99 must drop the bare-ticker idea through run-all-l3; got {result['ideas']}"
        )

    def test_qualified_ticker_still_binds(self, monkeypatch):
        """Control: the qualified ``provider:ticker`` notation keeps
        dropping the idea (exact match — must never regress)."""
        monkeypatch.setattr(sl, "load_skill", _fake_loader())
        runall = _load_lib("run-all-l3")
        with _patched(_STRATEGY, ("kraken:PENDLEUSD", "1d"), 99):
            envelope = runall.analyze("kraken:PENDLEUSD", [[1, 100, 101, 99, 100, 1000]], interval="1d", period="1y")
        assert envelope["strategies"][_STRATEGY]["ideas"] == []

    def test_bare_ticker_emits_when_no_floor(self, monkeypatch):
        """Control: without a floor entry the global default (1 = no-op)
        still lets the idea through — the gate only tightens when an
        entry binds."""
        monkeypatch.setattr(sl, "load_skill", _fake_loader())
        runall = _load_lib("run-all-l3")
        envelope = runall.analyze("PENDLEUSD", [[1, 100, 101, 99, 100, 1000]], interval="1d", period="1y")
        result = envelope["strategies"][_STRATEGY]
        assert result["ideas"], "global default 1 must keep the conviction=3 idea"
        assert result["ideas"][0]["conviction"] == 3


class TestConvictionScanEndToEnd:
    """The floor must bind through the real l3-conviction-scan path."""

    def test_kraken_keyed_floor_drops_row_from_scan(self, monkeypatch):
        """Basket resolves to the bare symbol; the ``kraken:``-keyed
        floor must remove the row entirely."""
        monkeypatch.setattr(sl, "load_skill", _fake_loader())
        scan_mod = _load_lib("l3-conviction-scan")
        monkeypatch.setattr(scan_mod, "by_category", lambda basket, path=None: ["PENDLEUSD"])
        monkeypatch.setattr(scan_mod, "fetch_ohlc", lambda *a, **kw: [[1, 100, 101, 99, 100, 1000]])
        monkeypatch.setattr(scan_mod, "metadata_for", lambda ticker, path=None: {"label": "P", "tier": 2})
        with _patched(_STRATEGY, ("kraken:PENDLEUSD", "1d"), 99):
            rows = scan_mod.scan(["tier_2"], interval="1d", period="1y")
        assert rows == [], f"kraken:PENDLEUSD 1d floor 99 must drop the row via scan(); got {rows}"

    def test_bare_ticker_row_emits_when_no_floor(self, monkeypatch):
        """Control: without a binding floor the scan surfaces the row
        (proves the machinery, not the floor, is under test)."""
        monkeypatch.setattr(sl, "load_skill", _fake_loader())
        scan_mod = _load_lib("l3-conviction-scan")
        monkeypatch.setattr(scan_mod, "by_category", lambda basket, path=None: ["PENDLEUSD"])
        monkeypatch.setattr(scan_mod, "fetch_ohlc", lambda *a, **kw: [[1, 100, 101, 99, 100, 1000]])
        monkeypatch.setattr(scan_mod, "metadata_for", lambda ticker, path=None: {"label": "P", "tier": 2})
        rows = scan_mod.scan(["tier_2"], interval="1d", period="1y")
        assert len(rows) == 1, f"global default (1) must surface the row; got {rows}"
        assert rows[0]["ticker"] == "PENDLEUSD"
        assert rows[0]["strategy"] == _STRATEGY
        assert rows[0]["conviction"] == 3
