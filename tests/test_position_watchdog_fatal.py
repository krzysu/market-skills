"""Tests for position-watchdog sustained-failure FATAL trigger.

Regression for BUGS-2026-07-08-1 — the old `fetch_failures == enabled_count`
check tripped on a single 1-tick API blip and converted a healthy
position-watchdog cron run into a 3am failure alert. The fix uses a
rolling per-watch failure window (``fetch_failures_window``) and only
trips FATAL when every enabled watch has ≥3 failures in the last 5 ticks.

The 4 tests cover:
  1. Single-tick full failure → no FATAL, exit 0, stderr `[WARN]`.
  2. 3+ consecutive full-failure ticks → FATAL, exit 1, stderr `FATAL`.
  3. Partial failure (5 of 6) → no FATAL, exit 0.
  4. Recovery resets the sustained counter — no FATAL after a successful tick.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS_DIR = os.path.join(_REPO_ROOT, "skills", "position-watchdog")
_RUN_PATH = os.path.join(_SKILLS_DIR, "scripts", "run.py")


def _load_run_module(name: str):
    """Re-import scripts/run.py with a unique spec name to avoid module cache collisions."""
    spec = importlib.util.spec_from_file_location(name, _RUN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_watches_config(path, names: list[str]) -> None:
    """Write a 1-line watches.json with the given watch names (all enabled, no levels)."""
    watches = [{"name": n, "monitor_provider": f"kraken:{n}USD", "enabled": True, "levels": []} for n in names]
    with open(path, "w") as f:
        json.dump({"watches": watches}, f)


def _write_state(path, name: str, fetch_failures_window: list[bool]) -> None:
    """Write a per-watch state file with the given failure window."""
    state = {
        "name": name,
        "levels": {},
        "signals": {},
        "fetch_failures_window": list(fetch_failures_window),
        "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def _run_main(monkeypatch, tmp_path, *, watch_names: list[str], price_map: dict[str, float | None]):
    """Invoke run.main() with a tmp config + state dir, mocked _current_price.

    ``price_map``: per-watch price value. ``None`` simulates a fetch failure
    for that watch.

    The state dir is injected via the ``MARKET_SKILLS_WATCHDOG_STATE_DIR``
    env var rather than ``monkeypatch.setattr(DATA_DIR, ...)`` because
    ``main()`` re-assigns ``DATA_DIR`` from ``args.state_dir`` at
    runtime, which would clobber a direct monkeypatch.
    """
    run_mod = _load_run_module(f"position_watchdog_fatal_{os.urandom(4).hex()}")

    cfg = tmp_path / "watches.json"
    _write_watches_config(cfg, watch_names)
    monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg)])

    def fake_price(provider, **_kwargs):
        # provider is `kraken:<TICKER>USD`; match by the bare token
        bare = provider.split(":", 1)[1].replace("USD", "")
        return price_map.get(bare)

    monkeypatch.setattr(run_mod, "_current_price", fake_price)
    return run_mod.main()


# ---------------------------------------------------------------------------
# 1. Single-tick failure — no FATAL
# ---------------------------------------------------------------------------


def test_single_tick_failure_does_not_fatal(monkeypatch, tmp_path, capsys):
    """All 6 watches fail in tick 1 with empty history → exit 0, [WARN], no FATAL.

    Regression for the original 2026-07-08 06:00 cron blip that woke the
    operator for a single transient outage.
    """
    names = ["ETH", "<PRIVATE_PERP>", "NEAR", "PAXG", "<PRIVATE_AI>", "ZEC"]
    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        watch_names=names,
        price_map={n: None for n in names},
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    # "FATAL: " (with colon) is the actual FATAL message; "suppressing FATAL"
    # is the warn-path message and should be present.
    assert "FATAL: " not in captured.err
    assert "suppressing FATAL" in captured.err
    assert "sustained=False" in captured.err


# ---------------------------------------------------------------------------
# 2. Sustained failure — trips FATAL
# ---------------------------------------------------------------------------


def test_sustained_failure_triggers_fatal(monkeypatch, tmp_path, capsys):
    """3 consecutive all-6-failure ticks → exit 1, stderr FATAL.

    State is pre-populated with 2 prior all-failure ticks (each watch has
    [True, True] in its window); the current tick appends a third True,
    pushing every watch to threshold (3) within the lookback (5).
    """
    names = ["ETH", "<PRIVATE_PERP>", "NEAR", "PAXG", "<PRIVATE_AI>", "ZEC"]

    # Pre-seed state files: each watch has 2 prior failures, then this tick fails.
    for n in names:
        _write_state(tmp_path / f"{n}_state.json", n, [True, True])

    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        watch_names=names,
        price_map={n: None for n in names},
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "FATAL: " in captured.err
    assert "≥3 of last 5" in captured.err

    # After this tick, every watch's window should be [True, True, True].
    for n in names:
        with open(tmp_path / f"{n}_state.json") as f:
            state = json.load(f)
        assert state["fetch_failures_window"] == [True, True, True]


# ---------------------------------------------------------------------------
# 3. Partial failure — no FATAL
# ---------------------------------------------------------------------------


def test_partial_failure_does_not_fatal(monkeypatch, tmp_path, capsys):
    """5 of 6 watches fail in tick 1 → exit 0, no FATAL.

    Even with sustained history, the all-watches condition is not met
    (one watch succeeded) so the FATAL branch is not entered.
    """
    names = ["ETH", "<PRIVATE_PERP>", "NEAR", "PAXG", "<PRIVATE_AI>", "ZEC"]
    # Pre-seed: 2 all-fail ticks so the others would qualify for sustained
    for n in names:
        _write_state(tmp_path / f"{n}_state.json", n, [True, True])
    # This tick: ZEC succeeds, others fail
    prices = {n: (100.0 if n == "ZEC" else None) for n in names}

    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        watch_names=names,
        price_map=prices,
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "FATAL: " not in captured.err


# ---------------------------------------------------------------------------
# 4. Recovery — successful ticks drop the rolling count below threshold
# ---------------------------------------------------------------------------


def test_recovery_drops_rolling_count_below_threshold(monkeypatch, tmp_path, capsys):
    """1 all-fail + 1 success + 1 all-fail → no FATAL.

    With 3-of-5 calibration, a single successful tick in the middle of a
    streak drops the trailing True count to 2 of 5, below threshold, so
    the next all-fail tick does not trip FATAL. The recovery is recorded
    in the per-watch window so the operator can see the watch came back.
    """
    names = ["ETH", "<PRIVATE_PERP>", "NEAR", "PAXG", "<PRIVATE_AI>", "ZEC"]

    # Tick 1: all fail. Window becomes [T].
    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        watch_names=names,
        price_map={n: None for n in names},
    )
    assert exit_code == 0
    for n in names:
        with open(tmp_path / f"{n}_state.json") as f:
            state = json.load(f)
        assert state["fetch_failures_window"] == [True]

    # Tick 2: all succeed. Window becomes [T, F].
    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        watch_names=names,
        price_map={n: 100.0 for n in names},
    )
    assert exit_code == 0
    for n in names:
        with open(tmp_path / f"{n}_state.json") as f:
            state = json.load(f)
        assert state["fetch_failures_window"] == [True, False]

    # Tick 3: all fail again. Window becomes [T, F, T]. Only 2 of last 5
    # are True → below threshold → no FATAL.
    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        watch_names=names,
        price_map={n: None for n in names},
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "FATAL: " not in captured.err
    for n in names:
        with open(tmp_path / f"{n}_state.json") as f:
            state = json.load(f)
        assert state["fetch_failures_window"] == [True, False, True]


# ---------------------------------------------------------------------------
# Unit tests for the sustained-failure helper
# ---------------------------------------------------------------------------


def test_all_watches_failed_sustained_helper(monkeypatch, tmp_path):
    """Helper returns True only when every watch has ≥threshold trailing True."""
    run_mod = _load_run_module("position_watchdog_fatal_helper")
    monkeypatch.setattr(run_mod, "DATA_DIR", str(tmp_path))

    _write_state(tmp_path / "A_state.json", "A", [True, True, True, False, False])
    _write_state(tmp_path / "B_state.json", "B", [True, True, True, False, True])

    # Both have 3 trailing True within the last 5 → sustained.
    assert run_mod._all_watches_failed_sustained(["A", "B"]) is True

    _write_state(tmp_path / "B_state.json", "B", [True, True, True, False, False])
    # A still has 3 trailing True; B has only 2 (3rd-from-end is True,
    # 2 trailing Falses, then the True at index 2 — but we only look at
    # the last 5 booleans which is [True, True, True, False, False] → 3 True).
    # Wait, both should still be sustained. Let me re-verify the threshold.
    # threshold=3, lookback=5. A's window is [T,T,T,F,F] → last 5 → 3 True. sustained.
    # B's window is now [T,T,T,F,F] → 3 True. sustained.
    assert run_mod._all_watches_failed_sustained(["A", "B"]) is True

    _write_state(tmp_path / "A_state.json", "A", [True, True, False, False, False])
    # A has 1 True in last 5 → below threshold → not sustained.
    assert run_mod._all_watches_failed_sustained(["A", "B"]) is False


def test_all_watches_failed_sustained_empty_list(monkeypatch, tmp_path):
    """No enabled watches → not sustained (vacuously true otherwise, but we treat as False)."""
    run_mod = _load_run_module("position_watchdog_fatal_empty")
    monkeypatch.setattr(run_mod, "DATA_DIR", str(tmp_path))
    assert run_mod._all_watches_failed_sustained([]) is False


def test_record_fetch_outcome_appends_and_caps(monkeypatch, tmp_path):
    """Window grows on each call, capped at FETCH_FAILURES_LOOKBACK."""
    run_mod = _load_run_module("position_watchdog_fatal_record")
    monkeypatch.setattr(run_mod, "DATA_DIR", str(tmp_path))

    lookback = run_mod.FETCH_FAILURES_LOOKBACK

    # 7 failures: window should be the trailing 5
    for i in range(lookback + 2):
        run_mod._record_fetch_outcome("X", fetch_failed=True)
    with open(tmp_path / "X_state.json") as f:
        state = json.load(f)
    assert state["fetch_failures_window"] == [True] * lookback

    # One success → trailing False
    run_mod._record_fetch_outcome("X", fetch_failed=False)
    with open(tmp_path / "X_state.json") as f:
        state = json.load(f)
    assert state["fetch_failures_window"] == [True, True, True, True, False]
