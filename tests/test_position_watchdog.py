"""Tests for position-watchdog lib.py — pure evaluator logic, no I/O."""

import datetime as dt
import importlib.util
import json
import os

_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
    "position-watchdog",
)
_LIB_PATH = os.path.join(_SKILLS_DIR, "lib.py")
_spec = importlib.util.spec_from_file_location("position_watchdog_lib", _LIB_PATH)
_pw_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pw_lib)


_level_id = _pw_lib._level_id
evaluate_levels = _pw_lib.evaluate_levels
evaluate_signals = _pw_lib.evaluate_signals


HYPE_WATCH = {
    "name": "HYPE",
    "provider": "kraken:HYPEEUR",
    "entry_price": 60.15,
    "position_size": 1.66,
    "levels": [
        {"type": "stop", "price": 49.71},
        {"type": "tp", "price": 88.21, "exit_pct": 33},
        {"type": "tp", "price": 100.58, "exit_pct": 33},
        {"type": "tp", "price": 119.14, "exit_pct": 34},
        {"type": "drop", "pct": -5},
        {"type": "drop", "pct": -10},
        {"type": "recovery"},
    ],
}


ZEC_WATCH = {
    "name": "ZEC",
    "provider": "kraken:ZECEUR",
    "levels": [
        {"type": "zone", "low": 500, "high": 510, "label": "T2 limit zone", "emoji": "🟢"},
        {"type": "zone", "low": 558, "high": 588, "label": "T3 reclaim", "emoji": "🟡"},
        {"type": "zone", "low": 588, "high": 99999, "label": "T4 continuation", "emoji": "🟠"},
        {"type": "invalidation", "below": 486},
    ],
}


def test_stop_breach_alerts_once():
    alerts, state = evaluate_levels(HYPE_WATCH, 48.0, None)
    assert any("STOP BREACHED" in a for a in alerts)
    assert state["alerted_levels"][_level_id({"type": "stop", "price": 49.71})] == "fired"

    alerts2, _ = evaluate_levels(HYPE_WATCH, 47.0, state)
    assert not any("STOP BREACHED" in a for a in alerts2)


def test_tp_ladder_in_priority_order():
    state = None
    alerts, state = evaluate_levels(HYPE_WATCH, 90.0, state)
    assert any("TP hit (€88.21)" in a for a in alerts)

    alerts, state = evaluate_levels(HYPE_WATCH, 102.0, state)
    assert any("TP hit (€100.58)" in a for a in alerts)

    alerts, state = evaluate_levels(HYPE_WATCH, 120.0, state)
    assert any("TP hit (€119.14)" in a for a in alerts)


def test_tp_exit_qty_calculated():
    alerts, _ = evaluate_levels(HYPE_WATCH, 90.0, None)
    tp1 = [a for a in alerts if "TP hit (€88.21)" in a][0]
    assert "0.55 HYPE" in tp1
    assert "~33%" in tp1


def test_drop_warnings_escalate():
    state = None
    alerts, state = evaluate_levels(HYPE_WATCH, 57.0, state)
    assert any("−5.0% from entry" in a for a in alerts)
    assert not any("−10.0% from entry" in a for a in alerts)

    alerts, state = evaluate_levels(HYPE_WATCH, 53.0, state)
    assert any("−10.0% from entry" in a for a in alerts)


def test_recovery_after_two_ticks_above_entry():
    state = {
        "alerted_levels": {
            _level_id({"type": "drop", "pct": -5}): "fired",
            _level_id({"type": "drop", "pct": -10}): "fired",
        },
        "above_entry_streak": 0,
        "prev_price": 53.0,
    }
    alerts, state = evaluate_levels(HYPE_WATCH, 61.0, state)
    assert not any("recovered" in a for a in alerts)
    assert state["above_entry_streak"] == 1

    alerts, state = evaluate_levels(HYPE_WATCH, 61.5, state)
    assert any("recovered" in a for a in alerts)


def test_first_tick_at_same_price_as_state_drop_does_not_re_alert():
    state = {
        "alerted_levels": {
            _level_id({"type": "drop", "pct": -5}): "fired",
        },
        "above_entry_streak": 0,
        "prev_price": 57.0,
    }
    alerts, _ = evaluate_levels(HYPE_WATCH, 57.0, state)
    assert not any("−5.0%" in a for a in alerts)


def test_drop_levels_skipped_when_no_entry_price():
    watch_no_entry = {k: v for k, v in HYPE_WATCH.items() if k != "entry_price"}
    watch_no_entry["levels"] = [lv for lv in watch_no_entry["levels"] if lv["type"] != "recovery"]
    alerts, _ = evaluate_levels(watch_no_entry, 53.0, None)
    assert not any("from entry" in a for a in alerts)


def test_drop_pct_must_be_negative():
    """Drop pct must be negative. The lib evaluates `pct_from_entry <= pct_threshold`
    literally, so a positive value (e.g. +5) fires on small upward moves (gain <= +5%)
    rather than on actual drops. SKILL.md mandates negative values; this test pins
    the canonical (negative) usage and the alert format that flows from it.
    """
    watch = {
        "name": "HYPE",
        "provider": "kraken:HYPEEUR",
        "entry_price": 60.15,
        "levels": [
            {"type": "drop", "pct": -5},
            {"type": "drop", "pct": -10},
        ],
    }
    alerts, _ = evaluate_levels(watch, 57.0, None)
    assert any("−5.0% from entry" in a for a in alerts)
    assert not any("−10.0% from entry" in a for a in alerts)

    alerts, _ = evaluate_levels(watch, 53.0, None)
    assert any("−10.0% from entry" in a for a in alerts)


def test_zone_entry_alerts_on_transition():
    state = {"alerted_levels": {}, "above_entry_streak": 0, "prev_price": 520.0}
    alerts, state = evaluate_levels(ZEC_WATCH, 505.0, state)
    assert any("T2 limit zone" in a for a in alerts)

    alerts, _ = evaluate_levels(ZEC_WATCH, 507.0, state)
    assert not any("T2 limit zone" in a for a in alerts)


def test_zone_does_not_re_alert_while_inside():
    state = {
        "alerted_levels": {_level_id(ZEC_WATCH["levels"][0]): "fired"},
        "above_entry_streak": 0,
        "prev_price": 502.0,
    }
    alerts, _ = evaluate_levels(ZEC_WATCH, 505.0, state)
    assert alerts == []


def test_invalidation_alerts_once():
    state = {"alerted_levels": {}, "above_entry_streak": 0, "prev_price": 500.0}
    alerts, state = evaluate_levels(ZEC_WATCH, 480.0, state)
    assert any("INVALIDATION" in a for a in alerts)

    alerts, _ = evaluate_levels(ZEC_WATCH, 481.0, state)
    assert not any("INVALIDATION" in a for a in alerts)


def test_invalidation_is_sticky_does_not_re_alert():
    state = {
        "alerted_levels": {_level_id({"type": "invalidation", "below": 486}): "fired"},
        "above_entry_streak": 0,
        "prev_price": 480.0,
    }
    no_zones_watch = {
        "name": "ZEC",
        "provider": "kraken:ZECEUR",
        "levels": [{"type": "invalidation", "below": 486}],
    }
    alerts, _ = evaluate_levels(no_zones_watch, 500.0, state)
    assert not any("INVALIDATION" in a for a in alerts)
    alerts2, _ = evaluate_levels(no_zones_watch, 470.0, state)
    assert not any("INVALIDATION" in a for a in alerts2)


def test_no_levels_returns_empty():
    alerts, state = evaluate_levels({"name": "EMPTY"}, 100.0, None)
    assert alerts == []
    assert state == {}


def test_signal_below_conviction_threshold_silent():
    sg_watch = {
        "name": "HYPE",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 4, "cooldown_hours": 0}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 3, "entry_price": 60.0, "stop_loss": 55.0}]}
    alerts, _ = evaluate_signals(sg_watch, ideas, None)
    assert alerts == []


def test_signal_alerts_at_or_above_threshold():
    sg_watch = {
        "name": "HYPE",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    alerts, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(alerts) == 1
    assert "trend-follow LONG conv=4" in alerts[0]
    assert "Entry €60.00" in alerts[0]


def test_signal_cooldown_prevents_re_alert():
    sg_watch = {
        "name": "HYPE",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    now = dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=dt.UTC)
    alerts1, state1 = evaluate_signals(sg_watch, ideas, None, now=now)
    assert len(alerts1) == 1

    later = now + dt.timedelta(hours=1)
    alerts2, _ = evaluate_signals(sg_watch, ideas, state1, now=later)
    assert alerts2 == []


def test_signal_cooldown_expires():
    sg_watch = {
        "name": "HYPE",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    now = dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=dt.UTC)
    _, state = evaluate_signals(sg_watch, ideas, None, now=now)

    later = now + dt.timedelta(hours=3)
    alerts, _ = evaluate_signals(sg_watch, ideas, state, now=later)
    assert len(alerts) == 1


def test_multiple_strategies_in_signal_block():
    sg_watch = {
        "name": "HYPE",
        "signals": [
            {
                "strategies": ["trend-follow", "mean-reversion"],
                "min_conviction": 3,
                "cooldown_hours": 0,
            }
        ],
    }
    ideas = {
        "trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}],
        "mean-reversion": [],
    }
    alerts, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(alerts) == 1
    assert "trend-follow" in alerts[0]


def test_level_id_is_stable_across_calls():
    a = _level_id({"type": "stop", "price": 49.71})
    b = _level_id({"type": "stop", "price": 49.71})
    assert a == b
    c = _level_id({"type": "stop", "price": 50.00})
    assert a != c


def test_stale_state_silent_first_tick_does_not_re_alert(monkeypatch, tmp_path):
    """When state is stale (>24h), the first tick must be silent even if conditions
    are still met — but state is rewritten so subsequent ticks see the fired levels."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location("position_watchdog_run", _run_path)
    _run_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules["position_watchdog_run"] = _run_mod
    _run_spec.loader.exec_module(_run_mod)

    stale_state = {
        "_updated_at": (dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)).isoformat(),
        "levels": {"alerted_levels": {}, "above_entry_streak": 0, "prev_price": None},
        "signals": {},
    }
    state_file = tmp_path / "HYPE_state.json"
    state_file.write_text(json.dumps(stale_state))
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    def _fake_price(_provider):
        return 48.0

    monkeypatch.setattr(_run_mod, "_current_price", _fake_price)

    watch = {
        "name": "HYPE",
        "provider": "kraken:HYPEEUR",
        "entry_price": 60.15,
        "position_size": 1.66,
        "levels": [{"type": "stop", "price": 49.71}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert alerts == []
    assert new_state["levels"]["alerted_levels"][_level_id({"type": "stop", "price": 49.71})] == "fired"


def test_stale_state_silent_for_signals_too(monkeypatch, tmp_path):
    """Stale first tick must also suppress signal alerts, but seed last_signal_alert_at
    so the cooldown window is honored on subsequent ticks."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location("position_watchdog_run2", _run_path)
    _run_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules["position_watchdog_run2"] = _run_mod
    _run_spec.loader.exec_module(_run_mod)

    stale_state = {
        "_updated_at": (dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)).isoformat(),
        "levels": {},
        "signals": {},
    }
    state_file = tmp_path / "HYPE_state.json"
    state_file.write_text(json.dumps(stale_state))
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    monkeypatch.setattr(_run_mod, "_current_price", lambda _p: 100.0)
    monkeypatch.setattr(
        _run_mod,
        "_run_strategies",
        lambda _strategies, _provider: {
            "trend-follow": [{"direction": "long", "conviction": 5, "entry_price": 100.0, "stop_loss": 90.0}],
        },
    )

    watch = {
        "name": "HYPE",
        "provider": "kraken:HYPEEUR",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 4}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert alerts == []
    assert "trend-follow:long" in new_state["signals"]["last_signal_alert_at"]
