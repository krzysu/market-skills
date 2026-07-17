"""Tests for position-watchdog lib.py — pure evaluator logic, no I/O.

After the lib/formatter split, these tests assert on the structured
event dicts the evaluator emits (not on pre-formatted strings). The
string-rendering side of the contract is covered in
``test_position_watchdog_formatter.py``.
"""

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


TICKER_WATCH = {
    "name": "<PRIVATE_PERP>",
    "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
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
    "monitor_provider": "kraken:ZECEUR",
    "levels": [
        {"type": "zone", "low": 500, "high": 510, "label": "T2 limit zone", "emoji": "🟢"},
        {"type": "zone", "low": 558, "high": 588, "label": "T3 reclaim", "emoji": "🟡"},
        {"type": "zone", "low": 588, "high": 99999, "label": "T4 continuation", "emoji": "🟠"},
        {"type": "invalidation", "below": 486},
    ],
}


def _events_of(events, etype):
    return [e for e in events if e["type"] == etype]


def test_stop_breach_alerts_once():
    events, state = evaluate_levels(TICKER_WATCH, 48.0, None)
    stops = _events_of(events, "stop")
    assert len(stops) == 1
    assert stops[0]["current_price"] == 48.0
    assert stops[0]["stop_price"] == 49.71
    assert state["alerted_levels"][_level_id({"type": "stop", "price": 49.71})] == "fired"

    events2, _ = evaluate_levels(TICKER_WATCH, 47.0, state)
    assert _events_of(events2, "stop") == []


def test_tp_ladder_in_priority_order():
    state = None
    events, state = evaluate_levels(TICKER_WATCH, 90.0, state)
    tps = _events_of(events, "tp")
    assert len(tps) == 1
    assert tps[0]["tp_price"] == 88.21

    events, state = evaluate_levels(TICKER_WATCH, 102.0, state)
    tps = _events_of(events, "tp")
    assert len(tps) == 1
    assert tps[0]["tp_price"] == 100.58

    events, state = evaluate_levels(TICKER_WATCH, 120.0, state)
    tps = _events_of(events, "tp")
    assert len(tps) == 1
    assert tps[0]["tp_price"] == 119.14


def test_tp_exit_qty_calculated():
    events, _ = evaluate_levels(TICKER_WATCH, 90.0, None)
    tp1 = _events_of(events, "tp")[0]
    # 1.66 * 33 / 100 = 0.5478 → 4dp; 2dp display is the formatter's job.
    assert tp1["exit_pct"] == 33
    assert tp1["position_size"] == 1.66
    assert abs(tp1["qty"] - 0.5478) < 1e-6


def test_tp_without_exit_pct_omits_qty():
    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "entry_price": 60.15,
        "position_size": 1.66,
        "levels": [{"type": "tp", "price": 80.0}],
    }
    events, _ = evaluate_levels(watch, 81.0, None)
    tp = _events_of(events, "tp")[0]
    assert tp["exit_pct"] is None
    assert tp["qty"] is None


def test_drop_warnings_escalate():
    state = None
    events, state = evaluate_levels(TICKER_WATCH, 57.0, state)
    drops = _events_of(events, "drop")
    assert len(drops) == 1
    assert drops[0]["threshold_pct"] == -5
    assert drops[0]["severity"] == "warn"

    events, state = evaluate_levels(TICKER_WATCH, 53.0, state)
    drops = _events_of(events, "drop")
    assert any(d["threshold_pct"] == -10 and d["severity"] == "critical" for d in drops)


def test_recovery_after_two_ticks_above_entry():
    state = {
        "alerted_levels": {
            _level_id({"type": "drop", "pct": -5}): "fired",
            _level_id({"type": "drop", "pct": -10}): "fired",
        },
        "above_entry_streak": 0,
        "prev_price": 53.0,
    }
    events, state = evaluate_levels(TICKER_WATCH, 61.0, state)
    assert _events_of(events, "recovery") == []
    assert state["above_entry_streak"] == 1

    events, state = evaluate_levels(TICKER_WATCH, 61.5, state)
    rec = _events_of(events, "recovery")
    assert len(rec) == 1
    assert rec[0]["current_price"] == 61.5
    assert rec[0]["entry_price"] == 60.15


def test_first_tick_at_same_price_as_state_drop_does_not_re_alert():
    state = {
        "alerted_levels": {
            _level_id({"type": "drop", "pct": -5}): "fired",
        },
        "above_entry_streak": 0,
        "prev_price": 57.0,
    }
    events, _ = evaluate_levels(TICKER_WATCH, 57.0, state)
    assert _events_of(events, "drop") == []


def test_drop_levels_skipped_when_no_entry_price():
    watch_no_entry = {k: v for k, v in TICKER_WATCH.items() if k != "entry_price"}
    watch_no_entry["levels"] = [lv for lv in watch_no_entry["levels"] if lv["type"] != "recovery"]
    events, _ = evaluate_levels(watch_no_entry, 53.0, None)
    assert _events_of(events, "drop") == []


def test_drop_pct_must_be_negative():
    """Drop pct must be negative. The lib evaluates `pct_from_entry <= pct_threshold`
    literally, so a positive value (e.g. +5) fires on small upward moves (gain <= +5%)
    rather than on actual drops. SKILL.md mandates negative values; this test pins
    the canonical (negative) usage and the event shape that flows from it.
    """
    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "entry_price": 60.15,
        "levels": [
            {"type": "drop", "pct": -5},
            {"type": "drop", "pct": -10},
        ],
    }
    events, _ = evaluate_levels(watch, 57.0, None)
    drops = _events_of(events, "drop")
    assert any(d["threshold_pct"] == -5 and d["severity"] == "warn" for d in drops)
    assert not any(d["threshold_pct"] == -10 for d in drops)

    events, _ = evaluate_levels(watch, 53.0, None)
    drops = _events_of(events, "drop")
    assert any(d["threshold_pct"] == -10 and d["severity"] == "critical" for d in drops)


def test_drop_event_carries_pct_from_entry():
    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "entry_price": 100.0,
        "levels": [{"type": "drop", "pct": -5}],
    }
    events, _ = evaluate_levels(watch, 93.0, None)
    drop = _events_of(events, "drop")[0]
    assert abs(drop["pct_from_entry"] - (-7.0)) < 1e-6


def test_zone_entry_alerts_on_transition():
    state = {"alerted_levels": {}, "above_entry_streak": 0, "prev_price": 520.0}
    events, state = evaluate_levels(ZEC_WATCH, 505.0, state)
    zones = _events_of(events, "zone")
    assert any(z["label"] == "T2 limit zone" for z in zones)

    events, _ = evaluate_levels(ZEC_WATCH, 507.0, state)
    assert not any(z["label"] == "T2 limit zone" for z in _events_of(events, "zone"))


def test_zone_does_not_re_alert_while_inside():
    state = {
        "alerted_levels": {_level_id(ZEC_WATCH["levels"][0]): "fired"},
        "above_entry_streak": 0,
        "prev_price": 502.0,
    }
    events, _ = evaluate_levels(ZEC_WATCH, 505.0, state)
    assert events == []


def test_invalidation_alerts_once():
    state = {"alerted_levels": {}, "above_entry_streak": 0, "prev_price": 500.0}
    events, state = evaluate_levels(ZEC_WATCH, 480.0, state)
    invs = _events_of(events, "invalidation")
    assert len(invs) == 1
    assert invs[0]["below_price"] == 486
    assert invs[0]["current_price"] == 480.0

    events, _ = evaluate_levels(ZEC_WATCH, 481.0, state)
    assert _events_of(events, "invalidation") == []


def test_invalidation_is_sticky_does_not_re_alert():
    state = {
        "alerted_levels": {_level_id({"type": "invalidation", "below": 486}): "fired"},
        "above_entry_streak": 0,
        "prev_price": 480.0,
    }
    no_zones_watch = {
        "name": "ZEC",
        "monitor_provider": "kraken:ZECEUR",
        "levels": [{"type": "invalidation", "below": 486}],
    }
    events, _ = evaluate_levels(no_zones_watch, 500.0, state)
    assert _events_of(events, "invalidation") == []
    events2, _ = evaluate_levels(no_zones_watch, 470.0, state)
    assert _events_of(events2, "invalidation") == []


def test_no_levels_returns_empty():
    events, state = evaluate_levels({"name": "EMPTY"}, 100.0, None)
    assert events == []
    assert state == {}


def test_signal_below_conviction_threshold_silent():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 4, "cooldown_hours": 0}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 3, "entry_price": 60.0, "stop_loss": 55.0}]}
    events, _ = evaluate_signals(sg_watch, ideas, None)
    assert events == []


def test_signal_alerts_at_or_above_threshold():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    events, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(events) == 1
    sig = events[0]
    assert sig["type"] == "signal"
    assert sig["strategy"] == "trend-follow"
    assert sig["direction"] == "long"
    assert sig["conviction"] == 4
    assert sig["entry_price"] == 60.0
    assert sig["stop_loss"] == 55.0


def test_signal_passes_through_extended_idea_fields():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    ideas = {
        "trend-follow": [
            {
                "direction": "long",
                "conviction": 4,
                "entry_price": 60.0,
                "entry_range": [59.5, 60.5],
                "stop_loss": 55.0,
                "take_profit": [67.5, 72.5, 80.0],
                "reasoning": "Healthy uptrend",
                "source_skills": ["market-trend-quality"],
                "entry_type": "limit",
            }
        ]
    }
    events, _ = evaluate_signals(sg_watch, ideas, None)
    sig = events[0]
    assert sig["entry_range"] == [59.5, 60.5]
    assert sig["take_profit"] == [67.5, 72.5, 80.0]
    assert sig["reasoning"] == "Healthy uptrend"
    assert sig["source_skills"] == ["market-trend-quality"]
    assert sig["entry_type"] == "limit"


def test_signal_defaults_extended_fields_when_missing():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    events, _ = evaluate_signals(sg_watch, ideas, None)
    sig = events[0]
    assert sig["entry_range"] == []
    assert sig["take_profit"] == []
    assert sig["reasoning"] == ""
    assert sig["source_skills"] == []
    assert sig["entry_type"] == "limit"


def test_signal_cooldown_prevents_re_alert():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    now = dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=dt.UTC)
    events1, state1 = evaluate_signals(sg_watch, ideas, None, now=now)
    assert len(events1) == 1

    later = now + dt.timedelta(hours=1)
    events2, _ = evaluate_signals(sg_watch, ideas, state1, now=later)
    assert events2 == []


def test_signal_cooldown_expires():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    now = dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=dt.UTC)
    _, state = evaluate_signals(sg_watch, ideas, None, now=now)

    later = now + dt.timedelta(hours=3)
    events, _ = evaluate_signals(sg_watch, ideas, state, now=later)
    assert len(events) == 1


def test_multiple_strategies_in_signal_block():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
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
    events, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(events) == 1
    assert events[0]["strategy"] == "trend-follow"


def test_signal_direction_filter_drops_mismatched_ideas():
    sg_watch = {
        "name": "ETH",
        "signals": [
            {
                "strategies": ["trend-follow", "mean-reversion"],
                "min_conviction": 3,
                "cooldown_hours": 0,
                "direction": "long",
            }
        ],
    }
    ideas = {
        "trend-follow": [{"direction": "short", "conviction": 5, "entry_price": 60.0, "stop_loss": 65.0}],
        "mean-reversion": [{"direction": "long", "conviction": 4, "entry_price": 58.0, "stop_loss": 55.0}],
    }
    events, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(events) == 1
    assert events[0]["strategy"] == "mean-reversion"
    assert events[0]["direction"] == "long"
    assert not any(e["strategy"] == "trend-follow" for e in events)


def test_signal_direction_filter_is_case_insensitive():
    sg_watch = {
        "name": "ETH",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0, "direction": "LONG"}],
    }
    ideas = {"trend-follow": [{"direction": "long", "conviction": 4, "entry_price": 60.0, "stop_loss": 55.0}]}
    events, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(events) == 1


def test_signal_no_direction_filter_keeps_both_directions():
    sg_watch = {
        "name": "<PRIVATE_PERP>",
        "signals": [{"strategies": ["trend-follow", "mean-reversion"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    ideas = {
        "trend-follow": [{"direction": "short", "conviction": 4, "entry_price": 60.0, "stop_loss": 65.0}],
        "mean-reversion": [{"direction": "long", "conviction": 4, "entry_price": 58.0, "stop_loss": 55.0}],
    }
    events, _ = evaluate_signals(sg_watch, ideas, None)
    assert len(events) == 2


def test_level_id_is_stable_across_calls():
    a = _level_id({"type": "stop", "price": 49.71})
    b = _level_id({"type": "stop", "price": 49.71})
    assert a == b
    c = _level_id({"type": "stop", "price": 50.00})
    assert a != c


def test_event_has_iso_triggered_at():
    """All emitted events stamp ``triggered_at`` as an ISO string."""
    fixed = dt.datetime(2026, 6, 20, 12, 0, 0, tzinfo=dt.UTC)
    events, _ = evaluate_levels(TICKER_WATCH, 48.0, None, now=fixed)
    assert events, "expected at least one event from stop breach"
    for e in events:
        assert "triggered_at" in e
        # round-trips through fromisoformat
        parsed = dt.datetime.fromisoformat(e["triggered_at"])
        assert parsed == fixed


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
    state_file = tmp_path / "<PRIVATE_PERP>_state.json"
    state_file.write_text(json.dumps(stale_state))
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    def _fake_price(_provider, **_kwargs):
        return 48.0

    monkeypatch.setattr(_run_mod, "_current_price", _fake_price)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "entry_price": 60.15,
        "position_size": 1.66,
        "levels": [{"type": "stop", "price": 49.71}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert alerts == []
    assert new_state["levels"]["alerted_levels"][_level_id({"type": "stop", "price": 49.71})] == "fired"
    # Per-fix: confirm the seeded fixture is actually loaded by _state_path(),
    # not silently treated as missing. The runtime reads
    # ``<sanitized(name)>_state.json``; if the fixture filename doesn't match,
    # this test silently exercises the missing-state path instead of the
    # stale-state path it claims to test.
    assert _run_mod._load_state("<PRIVATE_PERP>") is not None, (
        "fixture file at _state_path(<PRIVATE_PERP>) must be loadable — "
        "rename to '<PRIVATE_PERP>_state.json' if this assertion fails"
    )


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
    state_file = tmp_path / "<PRIVATE_PERP>_state.json"
    state_file.write_text(json.dumps(stale_state))
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    monkeypatch.setattr(_run_mod, "_current_price", lambda _p, **_kwargs: 100.0)
    monkeypatch.setattr(
        _run_mod,
        "_run_strategies",
        lambda _strategies, _provider, **_kwargs: {
            "trend-follow": [{"direction": "long", "conviction": 5, "entry_price": 100.0, "stop_loss": 90.0}],
        },
    )

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 4}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert alerts == []
    assert "trend-follow:long" in new_state["signals"]["last_signal_alert_at"]
    # Per-fix: confirm the seeded fixture is actually loaded by _state_path();
    # otherwise the test silently exercises the missing-state path.
    assert _run_mod._load_state("<PRIVATE_PERP>") is not None


def test_env_var_overrides_default_config(monkeypatch, tmp_path):
    """MARKET_SKILLS_WATCHDOG_PATH should override the default config path
    when --config is not provided on the CLI."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location("position_watchdog_env1", _run_path)
    _env_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules["position_watchdog_env1"] = _env_mod
    _run_spec.loader.exec_module(_env_mod)

    cfg = tmp_path / "watches.json"
    cfg.write_text('{"watches": []}')
    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_PATH", str(cfg))
    monkeypatch.setattr(sys, "argv", ["run.py"])

    assert _env_mod.main() == 0


def test_env_var_overrides_default_state_dir(monkeypatch, tmp_path):
    """MARKET_SKILLS_WATCHDOG_STATE_DIR should override the default state dir
    when --state-dir is not provided."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location("position_watchdog_env2", _run_path)
    _env_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules["position_watchdog_env2"] = _env_mod
    _run_spec.loader.exec_module(_env_mod)

    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))
    cfg = tmp_path / "watches.json"
    cfg.write_text('{"watches": []}')
    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_PATH", str(cfg))
    monkeypatch.setattr(sys, "argv", ["run.py"])

    assert _env_mod.main() == 0
    # DATA_DIR was wired from the env-derived state dir
    assert _env_mod.DATA_DIR == str(tmp_path)


def test_cli_flag_overrides_env_var(monkeypatch, tmp_path):
    """Explicit --config on the CLI must win over MARKET_SKILLS_WATCHDOG_PATH."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location("position_watchdog_env3", _run_path)
    _env_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules["position_watchdog_env3"] = _env_mod
    _run_spec.loader.exec_module(_env_mod)

    cfg_env = tmp_path / "env.json"
    cfg_env.write_text("{}")
    cfg_flag = tmp_path / "flag.json"
    cfg_flag.write_text('{"watches": []}')

    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_PATH", str(cfg_env))
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg_flag)])

    assert _env_mod.main() == 0


def test_env_var_missing_config_fatal(monkeypatch, tmp_path):
    """When the env var points at a missing config, main() should exit 1."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location("position_watchdog_env4", _run_path)
    _env_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules["position_watchdog_env4"] = _env_mod
    _run_spec.loader.exec_module(_env_mod)

    missing = tmp_path / "does_not_exist.json"
    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_PATH", str(missing))
    monkeypatch.setattr(sys, "argv", ["run.py"])

    assert _env_mod.main() == 1


def _load_run_mod(spec_name: str):
    """Re-import scripts/run.py with a unique spec name to avoid module cache issues."""
    import sys

    _run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    _run_spec = importlib.util.spec_from_file_location(spec_name, _run_path)
    _run_mod = importlib.util.module_from_spec(_run_spec)
    sys.modules[spec_name] = _run_mod
    _run_spec.loader.exec_module(_run_mod)
    return _run_mod


def test_four_h_default_for_interval(monkeypatch, tmp_path):
    """A watch without interval/period fields must default to 4h / 6mo for both
    the live-price tick and L3 strategy evaluation."""
    _run_mod = _load_run_mod("position_watchdog_itv_default")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    captured: dict = {"price": None, "strategies": None}

    def fake_current_price(provider, **kwargs):
        captured["price"] = (provider, kwargs.get("interval"), kwargs.get("period"))
        return 100.0

    def fake_run_strategies(strategies, provider, **kwargs):
        captured["strategies"] = (strategies, provider, kwargs.get("interval"), kwargs.get("period"))
        return {}

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)
    monkeypatch.setattr(_run_mod, "_run_strategies", fake_run_strategies)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "levels": [{"type": "stop", "price": 49.71}],
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert captured["price"] == ("kraken:<PRIVATE_PERP>EUR", "4h", "6mo")
    assert captured["strategies"] == (["trend-follow"], "kraken:<PRIVATE_PERP>EUR", "4h", "6mo")
    assert new_state is not None


def test_per_watch_interval_override(monkeypatch, tmp_path):
    """A watch that sets interval and period must pass those values (not the
    defaults) into both _current_price and _run_strategies."""
    _run_mod = _load_run_mod("position_watchdog_itv_override")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    captured: dict = {"price": None, "strategies": None}

    def fake_current_price(provider, **kwargs):
        captured["price"] = (provider, kwargs.get("interval"), kwargs.get("period"))
        return 200.0

    def fake_run_strategies(strategies, provider, **kwargs):
        captured["strategies"] = (strategies, provider, kwargs.get("interval"), kwargs.get("period"))
        return {}

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)
    monkeypatch.setattr(_run_mod, "_run_strategies", fake_run_strategies)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "interval": "1h",
        "period": "3mo",
        "levels": [{"type": "stop", "price": 49.71}],
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    now = dt.datetime.now(dt.UTC)
    _run_mod._process_watch(watch, dry_run=False, now=now)

    assert captured["price"] == ("kraken:<PRIVATE_PERP>EUR", "1h", "3mo")
    assert captured["strategies"] == (["trend-follow"], "kraken:<PRIVATE_PERP>EUR", "1h", "3mo")


def test_invalid_interval_prints_friendly_error(monkeypatch, tmp_path, capsys):
    """A bad interval must not crash the tick — _process_watch should print a
    friendly stderr message and return ([], None) so the caller skips the watch."""
    _run_mod = _load_run_mod("position_watchdog_itv_bad_interval")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    def fake_current_price(*_a, **_kw):
        raise AssertionError("_current_price must not be called when timeframe is invalid")

    def fake_run_strategies(*_a, **_kw):
        raise AssertionError("_run_strategies must not be called when timeframe is invalid")

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)
    monkeypatch.setattr(_run_mod, "_run_strategies", fake_run_strategies)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "interval": "2x",
        "period": "6mo",
        "levels": [{"type": "stop", "price": 49.71}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert alerts == []
    assert new_state is None

    captured = capsys.readouterr()
    assert "<PRIVATE_PERP>" in captured.err
    assert "'2x'" in captured.err
    assert "invalid timeframe" in captured.err


def test_invalid_period_skips_watch(monkeypatch, tmp_path, capsys):
    """A bad period must not crash the tick — _process_watch should print a
    friendly stderr message and return ([], None) so the caller skips the watch."""
    _run_mod = _load_run_mod("position_watchdog_itv_bad_period")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))

    def fake_current_price(*_a, **_kw):
        raise AssertionError("_current_price must not be called when timeframe is invalid")

    def fake_run_strategies(*_a, **_kw):
        raise AssertionError("_run_strategies must not be called when timeframe is invalid")

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)
    monkeypatch.setattr(_run_mod, "_run_strategies", fake_run_strategies)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "interval": "4h",
        "period": "12mo",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    now = dt.datetime.now(dt.UTC)
    alerts, new_state = _run_mod._process_watch(watch, dry_run=False, now=now)

    assert alerts == []
    assert new_state is None

    captured = capsys.readouterr()
    assert "<PRIVATE_PERP>" in captured.err
    assert "'12mo'" in captured.err
    assert "invalid timeframe" in captured.err


def test_validate_watch_requires_monitor_provider():
    """A watch without ``monitor_provider`` is a schema error."""
    _run_mod = _load_run_mod("position_watchdog_v_no_provider")
    errs = _run_mod._validate_watch({"name": "X", "enabled": True, "levels": [{"type": "stop", "price": 1}]})
    assert any("monitor_provider" in e for e in errs)


def test_validate_watch_rejects_legacy_provider_field():
    """The legacy ``provider`` field is no longer accepted — only ``monitor_provider``."""
    _run_mod = _load_run_mod("position_watchdog_v_legacy_rejected")
    errs = _run_mod._validate_watch(
        {"name": "X", "enabled": True, "provider": "kraken:<PRIVATE_PERP>EUR", "levels": [{"type": "stop", "price": 1}]}
    )
    assert any("monitor_provider" in e for e in errs)


def test_validate_watch_rejects_bad_monitor_provider_format():
    """``monitor_provider`` must be in ``provider:ticker`` notation."""
    _run_mod = _load_run_mod("position_watchdog_v_bad_mp")
    errs = _run_mod._validate_watch(
        {
            "name": "X",
            "enabled": True,
            "monitor_provider": "no-colon",
            "levels": [{"type": "stop", "price": 1}],
        }
    )
    assert any("monitor_provider" in e and "':'" in e for e in errs)


def test_validate_watch_accepts_monitor_only():
    """A monitor-only watch (no execution_provider) validates cleanly."""
    _run_mod = _load_run_mod("position_watchdog_v_monitor_only")
    errs = _run_mod._validate_watch(
        {
            "name": "<PRIVATE_PERP>",
            "enabled": True,
            "monitor_provider": "kraken:<PRIVATE_PERP>USD",
            "levels": [{"type": "stop", "price": 49.71}],
        }
    )
    assert errs == []


def test_validate_watch_rejects_execution_provider():
    """``execution_provider`` is removed in this release — schema-rejected.

    Library is single-currency. Use a second watch if you want a different
    pair's view.
    """
    _run_mod = _load_run_mod("position_watchdog_v_split")
    errs = _run_mod._validate_watch(
        {
            "name": "<PRIVATE_PERP>",
            "enabled": True,
            "monitor_provider": "kraken:<PRIVATE_PERP>USD",
            "execution_provider": "kraken:<PRIVATE_PERP>EUR",
            "levels": [{"type": "stop", "price": 49.71}],
        }
    )
    assert any("execution_provider" in e for e in errs)


def test_process_watch_uses_monitor_provider_for_fetch_and_strategies(monkeypatch, tmp_path):
    """``monitor_provider`` drives the live tick and L3 strategy fetch.

    Single-currency library: only the monitor is fetched. There is no
    execution_provider anymore.
    """
    _run_mod = _load_run_mod("position_watchdog_pw_monitor")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)
    monkeypatch.setattr(_run_mod, "_run_strategies", lambda *_a, **_kw: {})

    captured: list[str] = []

    def fake_current_price(provider, **_kw):
        captured.append(provider)
        return 100.0

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>USD",
        "levels": [{"type": "stop", "price": 90.0}],
    }
    _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC))
    assert captured == ["kraken:<PRIVATE_PERP>USD"]


def test_process_watch_skips_tick_when_monitor_fetch_fails(monkeypatch, tmp_path, capsys):
    """Monitor fetch failure → tick is skipped, no alerts, stderr message.

    Library is single-currency. No fallback to a second provider.
    """
    _run_mod = _load_run_mod("position_watchdog_pw_monitor_fail")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)
    monkeypatch.setattr(_run_mod, "_run_strategies", lambda *_a, **_kw: {})

    def fake_current_price(provider, **_kw):
        return None

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>USD",
        "levels": [{"type": "stop", "price": 90.0}],
    }
    alerts, _ = _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC))
    assert alerts == []
    captured = capsys.readouterr()
    assert "fetch failed" in captured.err
    assert "kraken:<PRIVATE_PERP>USD" in captured.err


def test_process_watch_l3_uses_monitor_provider(monkeypatch, tmp_path):
    """L3 strategy evaluation is driven by ``monitor_provider`` only (same interval/period).
    Even when ``execution_provider`` is set, the L3 candle fetch and idea prices come from
    the monitor — the execution provider is a separate tick for display only."""
    _run_mod = _load_run_mod("position_watchdog_pw_l3_monitor")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)

    captured: dict = {"price": None, "strategies": None}

    def fake_current_price(provider, **_kw):
        return 48.0

    def fake_run_strategies(strategies, provider, **_kw):
        captured["strategies"] = (strategies, provider)
        return {}

    monkeypatch.setattr(_run_mod, "_current_price", fake_current_price)
    monkeypatch.setattr(_run_mod, "_run_strategies", fake_run_strategies)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>USD",
        "execution_provider": "kraken:<PRIVATE_PERP>EUR",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 0}],
    }
    _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC))
    assert captured["strategies"] == (["trend-follow"], "kraken:<PRIVATE_PERP>USD")


def test_run_strategies_passes_qualified_provider_ticker_to_analyze(monkeypatch, tmp_path):
    """Per-fix fixture for the watchdog L3 threshold-lookup bug.

    ``_run_strategies`` previously stripped the provider prefix before calling
    ``analyze(...)``, so a watch with ``monitor_provider='kraken:LIT'`` would
    see ``ticker='LIT'`` inside the strategy. That made
    ``lookup_min_conviction('strategy-trend-follow', ticker, interval)`` miss
    any configured gate keyed on ``provider:ticker`` — the lookup fell
    through to ``GLOBAL_MIN_CONVICTION_TO_EMIT`` (=1 = no-op). The fix passes
    the qualified ``provider_ticker`` through unchanged.
    """
    _run_mod = _load_run_mod("position_watchdog_l3_qualified")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)
    monkeypatch.setattr(_run_mod, "_current_price", lambda *_a, **_kw: 100.0)
    # _run_strategies calls fetch_ohlc and bails when candles are empty;
    # stub it with enough rows to drive the analyze() path.
    monkeypatch.setattr(
        _run_mod,
        "fetch_ohlc",
        lambda *_a, **_kw: [[i, 100.0, 101.0, 99.0, 100.5, 1000.0] for i in range(60)],
    )

    captured: list[dict] = []

    class _StubStrategy:
        def analyze(self, candles, *, ticker, **kwargs):
            captured.append({"ticker": ticker, "kwargs": kwargs})
            return {"ideas": []}

    monkeypatch.setattr(_run_mod, "load_skill", lambda name: _StubStrategy())

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>USD",
        "signals": [{"strategies": ["trend-follow"], "min_conviction": 1, "cooldown_hours": 0}],
    }
    _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC))

    assert captured, "strategy analyze() was not called"
    assert captured[0]["ticker"] == "kraken:<PRIVATE_PERP>USD", (
        f"strategy must receive the qualified provider:ticker so "
        f"lookup_min_conviction() can match configured gates; "
        f"got {captured[0]['ticker']!r}"
    )


def test_bare_ticker_extracts_after_colon():
    _run_mod = _load_run_mod("position_watchdog_bare")
    assert _run_mod._bare_ticker("kraken:<PRIVATE_PERP>USD") == "<PRIVATE_PERP>USD"
    assert _run_mod._bare_ticker("hl:<PRIVATE_PERP>") == "<PRIVATE_PERP>"
    # No colon: return the whole string (shouldn't happen post-validation).
    assert _run_mod._bare_ticker("BARE") == "BARE"


def test_run_process_watch_default_style_is_compact_for_watches_json(monkeypatch, tmp_path):
    """_process_watch should default format_style to 'compact' for a watches.json
    config (so existing one-liner behavior is preserved without a config edit)."""
    _run_mod = _load_run_mod("position_watchdog_fmt_default")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)
    monkeypatch.setattr(_run_mod, "_run_strategies", lambda *_a, **_kw: {})
    monkeypatch.setattr(_run_mod, "_current_price", lambda *_a, **_kw: 48.0)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "levels": [{"type": "stop", "price": 49.71}],
    }
    cfg = "/tmp/watches.json"
    alerts, _ = _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC), config_path=cfg)
    # Compact style is the legacy one-liner.
    assert alerts, "expected at least one alert"
    assert all("\n" not in a for a in alerts), "compact alerts should be single-line"


def test_run_process_watch_default_style_is_default_for_open_positions(monkeypatch, tmp_path):
    """_process_watch should default format_style to 'default' (richer multi-line)
    when the config basename is open-positions.json."""
    _run_mod = _load_run_mod("position_watchdog_fmt_open")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)
    monkeypatch.setattr(_run_mod, "_run_strategies", lambda *_a, **_kw: {})
    monkeypatch.setattr(_run_mod, "_current_price", lambda *_a, **_kw: 48.0)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "levels": [{"type": "stop", "price": 49.71}],
    }
    cfg = "/tmp/open-positions.json"
    alerts, _ = _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC), config_path=cfg)
    assert alerts, "expected at least one alert"
    # default style stop uses different wording than compact.
    assert any("STOP BREACHED — <PRIVATE_PERP>" in a for a in alerts)


def test_run_process_watch_watch_level_format_style_overrides_default(monkeypatch, tmp_path):
    """A watch that sets format_style explicitly must win over the filename
    default (so an open-positions entry can be forced to compact)."""
    _run_mod = _load_run_mod("position_watchdog_fmt_override")
    monkeypatch.setattr(_run_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_run_mod, "_state_is_stale", lambda _s: False)
    monkeypatch.setattr(_run_mod, "_run_strategies", lambda *_a, **_kw: {})
    monkeypatch.setattr(_run_mod, "_current_price", lambda *_a, **_kw: 48.0)

    watch = {
        "name": "<PRIVATE_PERP>",
        "monitor_provider": "kraken:<PRIVATE_PERP>EUR",
        "format_style": "compact",
        "levels": [{"type": "stop", "price": 49.71}],
    }
    cfg = "/tmp/open-positions.json"
    alerts, _ = _run_mod._process_watch(watch, dry_run=False, now=dt.datetime.now(dt.UTC), config_path=cfg)
    assert all("\n" not in a for a in alerts)
