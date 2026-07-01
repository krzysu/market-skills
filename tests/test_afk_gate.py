"""Tests for analysis.risk.afk — three asymmetric hard gates.

The AFK layer is independent of risk-engine: it sits on top of any
advisory verdict and provides three binary gates (position cap / sleep
window / circuit breaker) that fire REJECT without LLM judgment.

State persistence (circuit-breaker.json) is exercised via tmp_path so
the suite doesn't touch the live ``$XDG_DATA_HOME`` directory.
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest


def _load_afk():
    """Load analysis.risk.afk from the installable package."""
    from analysis.risk import afk as afk_mod

    return afk_mod


def _intent(**overrides):
    base = {
        "intent_id": "afk-test",
        "venue": "kraken",
        "pair": "HYPEUSD",
        "side": "buy",
        "order_type": "limit",
        "volume": 1.5,
        "limit_price": 60.0,
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────── position cap


class TestPositionCap:
    def test_under_cap_approved(self):
        afk = _load_afk()
        ctx = afk.AFKContext(total_value=10000.0, base_ccy="USD")
        v = afk.check_position_cap(_intent(volume=1.0, limit_price=60), ctx, max_pct=10.0)
        # notional=60, total=10000 → 0.6% < 10% cap
        assert v["status"] == "APPROVED"
        assert v["gate"] == "position_cap"

    def test_over_cap_rejects(self):
        afk = _load_afk()
        ctx = afk.AFKContext(total_value=1000.0, base_ccy="USD")
        # notional = 1.5 * 600 = 900 = 90% of 1000. Cap=5%.
        v = afk.check_position_cap(_intent(volume=1.5, limit_price=600), ctx, max_pct=5.0)
        assert v["status"] == "REJECT"
        assert "5.0%" in v["reason"]
        assert "90" in v["reason"]
        assert v["detail"]["pct"] == pytest.approx(90.0)
        assert v["detail"]["max_pct"] == 5.0

    def test_no_limit_price_skips_cap(self):
        afk = _load_afk()
        ctx = afk.AFKContext(total_value=10000.0, base_ccy="USD")
        v = afk.check_position_cap(_intent(volume=1.0, limit_price=None), ctx, max_pct=5.0)
        assert v["status"] == "APPROVED"
        assert "no limit_price" in v["reason"] or v["reason"] == "no objection"

    def test_no_portfolio_value_fails_open(self):
        afk = _load_afk()
        ctx = afk.AFKContext(total_value=0.0, base_ccy="USD")
        v = afk.check_position_cap(_intent(volume=1.0, limit_price=600), ctx, max_pct=5.0)
        assert v["status"] == "APPROVED"
        assert "cap not enforceable" in v["reason"] or v["reason"] == "no objection"

    def test_sell_side_skips_cap(self):
        afk = _load_afk()
        ctx = afk.AFKContext(total_value=1000.0, base_ccy="USD")
        v = afk.check_position_cap(_intent(side="sell", volume=100.0, limit_price=60), ctx, max_pct=5.0)
        assert v["status"] == "APPROVED"
        # Sell side carries an explanatory note in detail (the helper
        # returns APPROVED with the default "no objection" reason).
        assert v["detail"].get("side") == "sell"
        assert "no held position" in v["detail"].get("note", "")


# ────────────────────────────────────────────────────────────────── sleep window


class TestSleepWindow:
    def test_outside_window_approved(self):
        afk = _load_afk()
        noon = _dt.datetime(2026, 6, 29, 12, 0, tzinfo=_dt.UTC)
        v = afk.check_sleep_window(start_hour=2, end_hour=6, now=noon)
        assert v["status"] == "APPROVED"
        assert v["detail"]["current_hour_utc"] == 12

    def test_inside_window_rejects(self):
        afk = _load_afk()
        three_am = _dt.datetime(2026, 6, 29, 3, 0, tzinfo=_dt.UTC)
        v = afk.check_sleep_window(start_hour=2, end_hour=6, now=three_am)
        assert v["status"] == "REJECT"
        assert "03" in v["reason"]
        assert "[02:00, 06:00)" in v["reason"]

    def test_window_start_inclusive(self):
        afk = _load_afk()
        two_am = _dt.datetime(2026, 6, 29, 2, 0, tzinfo=_dt.UTC)
        v = afk.check_sleep_window(start_hour=2, end_hour=6, now=two_am)
        assert v["status"] == "REJECT"

    def test_window_end_exclusive(self):
        afk = _load_afk()
        six_am = _dt.datetime(2026, 6, 29, 6, 0, tzinfo=_dt.UTC)
        v = afk.check_sleep_window(start_hour=2, end_hour=6, now=six_am)
        assert v["status"] == "APPROVED"

    def test_misconfigured_window_fails_open(self):
        afk = _load_afk()
        noon = _dt.datetime(2026, 6, 29, 12, 0, tzinfo=_dt.UTC)
        # start == end → misconfigured → approve with note in detail.
        v = afk.check_sleep_window(start_hour=6, end_hour=6, now=noon)
        assert v["status"] == "APPROVED"
        assert "misconfigured" in v["detail"].get("reason", "")


# ────────────────────────────────────────────────────────────────── circuit breaker


class TestCircuitBreaker:
    def test_initial_state_approved(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load()
        v = afk.check_circuit_breaker(_intent(), state)
        assert v["status"] == "APPROVED"

    def test_trips_after_threshold(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)

        state.record("HYPEUSD")
        v1 = afk.check_circuit_breaker(_intent(), state)
        assert v1["status"] == "APPROVED"

        state.record("HYPEUSD")
        v2 = afk.check_circuit_breaker(_intent(), state)
        assert v2["status"] == "REJECT"
        assert "tripped" in v2["reason"]
        assert "HYPEUSD" in v2["reason"]
        assert "manual reset" in v2["reason"]

    def test_threshold_default_two(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        # Default threshold = 2.
        state = afk.CircuitBreakerState.load()
        state.record("BTCUSD")
        state.record("BTCUSD")
        v = afk.check_circuit_breaker(_intent(pair="BTCUSD"), state)
        assert v["status"] == "REJECT"

    def test_pair_normalisation(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        state.record("hype-usd")
        state.record("HYPEUSD")
        v = afk.check_circuit_breaker(_intent(pair="HYPEUSD"), state)
        # Both writes map to the same bare ticker → 2 hits → tripped.
        assert v["status"] == "REJECT"

    def test_reset_clears_state(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        state.record("HYPEUSD")
        state.record("HYPEUSD")
        assert state.is_tripped("HYPEUSD")
        state.record("HYPEUSD", reset=True)
        # reset=True wipes the entry.
        v = afk.check_circuit_breaker(_intent(), state)
        assert v["status"] == "APPROVED"

    def test_state_persists_to_disk(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = afk.default_circuit_breaker_path()
        assert path is not None
        # First process: trip the breaker.
        s1 = afk.CircuitBreakerState.load(threshold=2)
        s1.record("HYPEUSD")
        s1.record("HYPEUSD")
        s1.persist()
        assert os.path.exists(path)
        # Second process: read from disk and verify the trip persisted.
        s2 = afk.CircuitBreakerState.load(threshold=2)
        assert s2.is_tripped("HYPEUSD")
        v = afk.check_circuit_breaker(_intent(), s2)
        assert v["status"] == "REJECT"

    def test_manual_reset_helper(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        s = afk.CircuitBreakerState.load(threshold=2)
        s.record("HYPEUSD")
        s.record("HYPEUSD")
        # Helper clears one pair.
        afk.reset_circuit_breaker("HYPEUSD")
        s2 = afk.CircuitBreakerState.load(threshold=2)
        assert not s2.is_tripped("HYPEUSD")

    def test_other_pair_unaffected(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        s = afk.CircuitBreakerState.load(threshold=2)
        s.record("HYPEUSD")
        s.record("HYPEUSD")
        # BTCUSD never recorded → approved.
        v = afk.check_circuit_breaker(_intent(pair="BTCUSD"), s)
        assert v["status"] == "APPROVED"


# ────────────────────────────────────────────────────────────────── vet_afk (composition)


class TestVetAfkComposition:
    def test_clean_intent_approved(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        ctx = afk.AFKContext(total_value=10000.0, base_ccy="USD")
        noon = _dt.datetime(2026, 6, 29, 12, 0, tzinfo=_dt.UTC)
        v = afk.vet_afk(_intent(volume=1.0, limit_price=60), ctx, state, now=noon)
        assert v["status"] == "APPROVED"
        assert v["gate"] == "passed"
        assert "all AFK gates cleared" in v["reason"]

    def test_position_cap_short_circuits(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        ctx = afk.AFKContext(total_value=1000.0, base_ccy="USD")
        noon = _dt.datetime(2026, 6, 29, 12, 0, tzinfo=_dt.UTC)
        v = afk.vet_afk(_intent(volume=10.0, limit_price=600), ctx, state, max_pct=5.0, now=noon)
        # notional = 6000 = 600% of 1000 → reject on cap
        assert v["status"] == "REJECT"
        assert v["gate"] == "position_cap"

    def test_sleep_window_short_circuits(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        ctx = afk.AFKContext(total_value=10000.0, base_ccy="USD")
        three_am = _dt.datetime(2026, 6, 29, 3, 0, tzinfo=_dt.UTC)
        v = afk.vet_afk(_intent(volume=0.01, limit_price=60), ctx, state, now=three_am)
        assert v["status"] == "REJECT"
        assert v["gate"] == "sleep_window"

    def test_circuit_breaker_short_circuits(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        state.record("HYPEUSD")
        state.record("HYPEUSD")
        ctx = afk.AFKContext(total_value=10000.0, base_ccy="USD")
        noon = _dt.datetime(2026, 6, 29, 12, 0, tzinfo=_dt.UTC)
        v = afk.vet_afk(_intent(), ctx, state, now=noon)
        assert v["status"] == "REJECT"
        assert v["gate"] == "circuit_breaker"

    def test_position_cap_evaluated_before_sleep_window(self, monkeypatch, tmp_path):
        """Both gates would REJECT — but cap wins because it's first.

        The order is intentional: the position cap is the cheapest to
        evaluate and is the rule most likely to fire on a real order,
        so it gets the early-exit slot.
        """
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        state = afk.CircuitBreakerState.load(threshold=2)
        ctx = afk.AFKContext(total_value=1000.0, base_ccy="USD")
        three_am = _dt.datetime(2026, 6, 29, 3, 0, tzinfo=_dt.UTC)
        v = afk.vet_afk(
            _intent(volume=10.0, limit_price=600),
            ctx,
            state,
            max_pct=5.0,
            now=three_am,
        )
        assert v["gate"] == "position_cap"

    def test_env_vars_override_defaults(self, monkeypatch, tmp_path):
        afk = _load_afk()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("AFK_MAX_POSITION_PCT", "2.0")
        monkeypatch.setenv("AFK_SLEEP_WINDOW_START_HOUR_UTC", "0")
        monkeypatch.setenv("AFK_SLEEP_WINDOW_END_HOUR_UTC", "23")
        monkeypatch.setenv("AFK_CIRCUIT_BREAKER_THRESHOLD", "1")
        state = afk.CircuitBreakerState.load()  # reads AFK_CIRCUIT_BREAKER_THRESHOLD
        assert state.threshold == 1
        ctx = afk.AFKContext(total_value=10000.0, base_ccy="USD")
        noon = _dt.datetime(2026, 6, 29, 12, 0, tzinfo=_dt.UTC)
        # Cap=2% → notional=10% → REJECT
        v = afk.vet_afk(_intent(volume=1.5, limit_price=600), ctx, state, now=noon)
        assert v["status"] == "REJECT"
        assert v["gate"] == "position_cap"


# ────────────────────────────────────────────────────────────────── public API


class TestPublicAPI:
    def test_reexported_from_package(self):
        from analysis.risk import afk as afk_mod

        for name in [
            "AFKContext",
            "AFKVerdict",
            "CircuitBreakerState",
            "check_position_cap",
            "check_sleep_window",
            "check_circuit_breaker",
            "vet_afk",
            "reset_circuit_breaker",
            "default_circuit_breaker_path",
        ]:
            assert hasattr(afk_mod, name), f"missing public name: {name}"

    def test_afk_listed_in_all(self):
        from analysis.risk import afk as afk_mod

        for name in ["vet_afk", "CircuitBreakerState", "AFKContext"]:
            assert name in afk_mod.__all__, f"missing from __all__: {name}"
