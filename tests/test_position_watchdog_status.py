"""Tests for position-watchdog --status mode.

Spec: SPEC-2026-07-08-position-watchdog-status-mode.md.

Covers:
  1. --status with a watch in a zone emits the zone attribution + next-zone hint + pct from entry.
  2. --status with two fired drop levels lists both thresholds in the rendered block.
  3. --status handles per-watch fetch failure (live price returns None) without crashing.
  4. --status skips watches with enabled: false.
  5. --status renders the above_entry_streak annotation when state has a non-zero streak.
  6. --status treats stale state (>24h old) as empty; streak from stale state is NOT rendered.
  7. --status with a watch that has no entry_price omits the % from entry clause.
  8. Per-watch smoke test over six real-config snippets (ETH, HYPE, NEAR, PAXG, VVV, ZEC).

Two test categories:
  * CLI/round-trip tests (3, 4) — invoke ``run.main()`` end-to-end via the
    ``MARKET_SKILLS_WATCHDOG_STATE_DIR`` env var and a mocked ``_current_price``.
  * Pure-formatter / lib tests (1, 2, 5, 6, 7, 8) — call ``_status_summary``
    or ``format_as_default_status`` directly with hand-built fixtures.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS_DIR = os.path.join(_REPO_ROOT, "skills", "position-watchdog")
_RUN_PATH = os.path.join(_SKILLS_DIR, "scripts", "run.py")
_LIB_PATH = os.path.join(_SKILLS_DIR, "lib.py")
_FMT_PATH = os.path.join(_SKILLS_DIR, "formatter.py")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ctx(
    name: str = "VVV",
    primary_quote: str = "USD",
    monitor_provider: str = "hl:VVVUSD",
    price: float | None = 10.39,
) -> dict:
    return {
        "name": name,
        "price": price,
        "primary_quote": primary_quote,
        "monitor_provider": monitor_provider,
        "format_style": "default",
    }


def _vvv_state() -> dict:
    """Per-watch state matching the spec example: drops fired, streak=3, prev_price set."""
    return {
        "name": "VVV",
        "levels": {
            "alerted_levels": {
                "drop:-10": "fired",
                "drop:-20": "fired",
                "recovery": "fired",
            }
        },
        "alerted_levels": {
            "drop:-10": "fired",
            "drop:-20": "fired",
            "recovery": "fired",
        },
        "above_entry_streak": 3,
        "prev_price": 10.50,
        "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
    }


def _vvv_config() -> dict:
    """VVV-style config: zones, invalidation, drops. Used by zone and drop tests."""
    return {
        "name": "VVV",
        "enabled": True,
        "monitor_provider": "hl:VVVUSD",
        "interval": "4h",
        "period": "6mo",
        "entry_price": 15.73,
        "position_size": 100,
        "levels": [
            {"type": "invalidation", "below": 8.00},
            {"type": "zone", "low": 7.50, "high": 9.00, "label": "T1 add zone", "emoji": "🟢"},
            {"type": "zone", "low": 9.50, "high": 11.50, "label": "T2 wait zone (no add)", "emoji": "🟡"},
            {"type": "drop", "pct": -10},
            {"type": "drop", "pct": -20},
        ],
    }


# ---------------------------------------------------------------------------
# 1. --status emits zone attribution + next-zone hint + pct from entry
# ---------------------------------------------------------------------------


def test_status_renders_zone_state() -> None:
    """VVV @ $10.39 → line includes the active T2 zone, T1 next-zone hint, pct."""
    lib = _load_module("pw_status_lib_1", _LIB_PATH)
    fmt = _load_module("pw_status_fmt_1", _FMT_PATH)

    event = lib._status_summary(
        name="VVV",
        config=_vvv_config(),
        state=_vvv_state(),
        current_price=10.39,
    )
    line = fmt.format_as_default_status(event, _ctx())

    assert line.startswith("[VVV] @ $10.39"), line
    assert "🟡 T2 wait zone (no add)" in line, line
    assert "above T1 add zone" in line, line
    assert ("$7.50–$9.00" in line) or ("$7.50-$9.00" in line), line
    assert "invalid <$8.00" in line, line
    assert "−33.9% from entry $15.73" in line, line
    assert line.endswith("; above entry streak=3"), line


# ---------------------------------------------------------------------------
# 2. Fired drop thresholds appear in the rendered block
# ---------------------------------------------------------------------------


def test_status_renders_drop_state() -> None:
    """Two fired drop levels (-10, -20) at price=75 from entry=$100 → both surface."""
    lib = _load_module("pw_status_lib_2", _LIB_PATH)
    fmt = _load_module("pw_status_fmt_2", _FMT_PATH)

    state = {
        "alerted_levels": {"drop:-10": "fired", "drop:-20": "fired"},
        "above_entry_streak": 0,
        "prev_price": 75.0,
    }
    config = {
        "name": "DROPTEST",
        "enabled": True,
        "monitor_provider": "hl:DROPTEST",
        "entry_price": 100.0,
        "levels": [
            {"type": "drop", "pct": -10},
            {"type": "drop", "pct": -20},
        ],
    }
    event = lib._status_summary(name="DROPTEST", config=config, state=state, current_price=75.0)
    line = fmt.format_as_default_status(event, _ctx(name="DROPTEST", price=75.0, monitor_provider="hl:DROPTEST"))

    assert "−25.0% from entry $100.00" in line, line
    assert "−10.0%" in line, line
    assert "−20.0%" in line, line


# ---------------------------------------------------------------------------
# 3. Fetch failure renders <fetch failed> fallback without crashing
# ---------------------------------------------------------------------------


def _write_watches_json(path, watches: list[dict]) -> None:
    with open(path, "w") as f:
        json.dump({"watches": watches}, f)


def test_status_handles_fetch_failure(monkeypatch, tmp_path, capsys) -> None:
    """All enabled watches fail to fetch in --status mode → lines print with <fetch failed> fallback; exit 2."""
    run_mod = _load_module(f"pw_status_run_3_{os.urandom(4).hex()}", _RUN_PATH)

    cfg = tmp_path / "watches.json"
    watches = [
        {"name": "ETH", "enabled": True, "monitor_provider": "kraken:ETHUSD", "levels": []},
        {"name": "HYPE", "enabled": True, "monitor_provider": "kraken:HYPEUSD", "levels": []},
    ]
    _write_watches_json(cfg, watches)

    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--status"])

    monkeypatch.setattr(run_mod, "_current_price", lambda *_args, **_kwargs: None)
    exit_code = run_mod.main()
    captured = capsys.readouterr()

    assert exit_code == 2, captured.err
    assert "<fetch failed>" in captured.out
    assert "[ETH]" in captured.out
    assert "[HYPE]" in captured.out


# ---------------------------------------------------------------------------
# 4. --status skips watches with enabled: false
# ---------------------------------------------------------------------------


def test_status_skips_disabled_watches(monkeypatch, tmp_path, capsys) -> None:
    """One of three watches is disabled → only two status lines render."""
    run_mod = _load_module(f"pw_status_run_4_{os.urandom(4).hex()}", _RUN_PATH)

    cfg = tmp_path / "watches.json"
    watches = [
        {"name": "A", "enabled": True, "monitor_provider": "kraken:AUSD", "levels": []},
        {"name": "B", "enabled": False, "monitor_provider": "kraken:BUSD", "levels": []},
        {"name": "C", "enabled": True, "monitor_provider": "kraken:CUSD", "levels": []},
    ]
    _write_watches_json(cfg, watches)

    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--status"])

    monkeypatch.setattr(run_mod, "_current_price", lambda *_args, **_kwargs: 100.0)
    exit_code = run_mod.main()
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    out_lines = [ln for ln in captured.out.splitlines() if ln.startswith("[")]
    assert len(out_lines) == 2, out_lines
    assert "[A]" in captured.out
    assert "[C]" in captured.out
    assert "[B]" not in captured.out


# ---------------------------------------------------------------------------
# 5. --status renders above_entry_streak annotation
# ---------------------------------------------------------------------------


def test_status_renders_above_entry_streak() -> None:
    """State has above_entry_streak=3 → line ends with '; above entry streak=3'. Streak=0 emits no annotation."""
    lib = _load_module("pw_status_lib_5", _LIB_PATH)
    fmt = _load_module("pw_status_fmt_5", _FMT_PATH)

    config = _vvv_config()
    state = _vvv_state()
    event = lib._status_summary(name="VVV", config=config, state=state, current_price=10.39)
    line = fmt.format_as_default_status(event, _ctx())

    assert line.endswith("; above entry streak=3"), line

    no_streak_state = dict(state)
    no_streak_state["above_entry_streak"] = 0
    event_no_streak = lib._status_summary(name="VVV", config=config, state=no_streak_state, current_price=10.39)
    line_no_streak = fmt.format_as_default_status(event_no_streak, _ctx())
    assert "above entry streak=" not in line_no_streak, line_no_streak


# ---------------------------------------------------------------------------
# 6. Stale state treated as empty — streak from stale state is NOT rendered
# ---------------------------------------------------------------------------


def test_status_ignores_stale_state(monkeypatch, tmp_path) -> None:
    """State _updated_at is 25h ago → streak is treated as 0; no streak annotation in CLI output."""
    run_mod = _load_module(f"pw_status_run_6_{os.urandom(4).hex()}", _RUN_PATH)

    cfg = tmp_path / "watches.json"
    watches = [{"name": "VVV", "enabled": True, "monitor_provider": "hl:VVVUSD", "levels": []}]
    _write_watches_json(cfg, watches)

    stale_ts = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)).isoformat()
    (tmp_path / "VVV_state.json").write_text(
        json.dumps(
            {
                "name": "VVV",
                "alerted_levels": {"drop:-10": "fired"},
                "above_entry_streak": 7,
                "prev_price": 10.50,
                "_updated_at": stale_ts,
            }
        )
    )

    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--status"])
    monkeypatch.setattr(run_mod, "_current_price", lambda *_args, **_kwargs: 10.39)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    rc = run_mod.main()
    out = buf.getvalue()

    assert rc == 0, out
    assert "[VVV]" in out
    assert "above entry streak=" not in out, f"stale streak leaked into output: {out!r}"


# ---------------------------------------------------------------------------
# 7. No entry_price → no % from entry clause
# ---------------------------------------------------------------------------


def test_status_no_entry_price_omits_pct_block() -> None:
    """Watch with levels but no entry_price → no 'from entry' clause appears; zone + invalidation still render."""
    lib = _load_module("pw_status_lib_7", _LIB_PATH)
    fmt = _load_module("pw_status_fmt_7", _FMT_PATH)

    config = {
        "name": "ZEC",
        "enabled": True,
        "monitor_provider": "kraken:ZECUSD",
        "levels": [
            {"type": "zone", "low": 500, "high": 510, "label": "T2 limit zone", "emoji": "🟢"},
            {"type": "invalidation", "below": 486},
        ],
    }
    event = lib._status_summary(name="ZEC", config=config, state={}, current_price=505.0)
    line = fmt.format_as_default_status(event, _ctx(name="ZEC", price=505.0, monitor_provider="kraken:ZECUSD"))

    assert "from entry" not in line, line
    assert "🟢 T2 limit zone" in line, line
    assert "invalid <$486.00" in line, line


# ---------------------------------------------------------------------------
# 8. Per-watch smoke test over 6 real-config snippets
# ---------------------------------------------------------------------------


_HYPE_FIXTURE = {
    "name": "HYPE",
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

_NEAR_FIXTURE = {
    "name": "NEAR",
    "entry_price": 4.20,
    "position_size": 50.0,
    "levels": [
        {"type": "stop", "price": 3.50},
        {"type": "tp", "price": 6.00, "exit_pct": 50},
        {"type": "tp", "price": 8.00, "exit_pct": 50},
    ],
}

_PAXG_FIXTURE = {
    "name": "PAXG",
    "entry_price": 2400.0,
    "position_size": 0.5,
    "levels": [
        {"type": "invalidation", "below": 2200.0},
        {"type": "zone", "low": 2150.0, "high": 2250.0, "label": "T1 add", "emoji": "🟢"},
    ],
}

_VVV_FIXTURE = _vvv_config()

_ETH_FIXTURE = {
    "name": "ETH",
    "entry_price": 1973.28,
    "position_size": 0.5,
    "levels": [
        {"type": "invalidation", "below": 1500.0},
        {"type": "zone", "low": 1500.0, "high": 1550.0, "label": "T1 zone", "emoji": "🟢"},
    ],
}

_ZEC_FIXTURE = {
    "name": "ZEC",
    "entry_price": None,
    "levels": [
        {"type": "zone", "low": 500, "high": 510, "label": "T2 limit zone", "emoji": "🟢"},
        {"type": "invalidation", "below": 486},
    ],
}


def _render_one(config: dict, state: dict, current_price: float | None) -> str:
    lib = _load_module("pw_status_lib_per_{}".format(config["name"]), _LIB_PATH)
    fmt = _load_module("pw_status_fmt_per_{}".format(config["name"]), _FMT_PATH)
    event = lib._status_summary(name=config["name"], config=config, state=state, current_price=current_price)
    return fmt.format_as_default_status(event, _ctx(name=config["name"], price=current_price))


def test_status_per_watch_hype() -> None:
    line = _render_one(_HYPE_FIXTURE, {}, 68.35)
    assert "[HYPE] @ $68.35" in line
    assert "no active zone" in line
    assert "+13.6% from entry $60.15" in line


def test_status_per_watch_near() -> None:
    line = _render_one(_NEAR_FIXTURE, {}, 4.50)
    assert "[NEAR] @ $4.50" in line
    assert "no active zone" in line


def test_status_per_watch_paxg_in_zone() -> None:
    line = _render_one(_PAXG_FIXTURE, {}, 2200.0)
    assert "[PAXG] @ $2200.00" in line
    assert "🟢 T1 add" in line


def test_status_per_watch_vvv() -> None:
    line = _render_one(_VVV_FIXTURE, _vvv_state(), 10.39)
    assert "[VVV] @ $10.39" in line
    assert "🟡 T2 wait zone (no add)" in line


def test_status_per_watch_eth_in_zone() -> None:
    line = _render_one(_ETH_FIXTURE, {}, 1525.0)
    assert "[ETH] @ $1525.00" in line
    assert "🟢 T1 zone" in line


def test_status_per_watch_zec_no_entry() -> None:
    line = _render_one(_ZEC_FIXTURE, {}, 505.0)
    assert "[ZEC] @ $505.00" in line
    assert "from entry" not in line
    assert "🟢 T2 limit zone" in line
