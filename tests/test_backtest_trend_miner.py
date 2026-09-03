"""Tests for skills/backtest-trend-miner — pure analysis + missing-file fallback.

Covers the bead's acceptance criteria:
  1. key parse split on "×"
  2. sharpe_series building from runs.jsonl (ordering, cap, insufficient_data)
  3. trend_slope computation (monotonic up -> +, monotonic down -> −)
  4. downtrend / improving flag logic (and no-flag when baseline absent)
  5. missing-file fallback -> AXI empty_state with a help line, no crash
  6. no ticker symbols hardcoded in the skill source
"""

from __future__ import annotations

import importlib.util
import json
import os
import statistics
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILL_DIR = os.path.join(_REPO_ROOT, "skills", "backtest-trend-miner")


def _load_lib():
    lib_path = os.path.join(_SKILL_DIR, "lib.py")
    spec = importlib.util.spec_from_file_location("backtest_trend_miner_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_run():
    run_path = os.path.join(_SKILL_DIR, "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("backtest_trend_miner_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_lib = _load_lib()
_run = _load_run()

SEP = "\u00d7"  # ×


def _key(interval, strategy, ticker):
    return f"{interval}{SEP}{strategy}{SEP}{ticker}"


def _result(sharpe, *, trades=20, insufficient=False):
    return {
        "strategy": "strategy-trend-follow",
        "ticker": "kraken:BTCUSD",
        "strategy_sharpe": sharpe,
        "trades": trades,
        "bars": 500,
        "provider": "kraken",
        "insufficient_data": insufficient,
    }


def _run_record(ts, results):
    return {
        "ts": ts,
        "strategies": ["strategy-trend-follow"],
        "tickers": ["BTCUSD"],
        "results": results,
        "errors": [],
        "insufficient_data": [],
    }


def _state(avg_sharpe_7n, key):
    return {"baseline": {key: {"history": [], "avg_sharpe_7n": avg_sharpe_7n, "n_samples": 7}}}


# -- split_key -----------------------------------------------------------------


def test_split_key_splits_three_parts():
    assert _lib.split_key(_key("1d", "strategy-trend-follow", "BTCUSD")) == (
        "1d",
        "strategy-trend-follow",
        "BTCUSD",
    )


def test_split_key_rejects_malformed():
    assert _lib.split_key("1d×strategy") is None
    assert _lib.split_key("") is None
    assert _lib.split_key("a×b×c×d") is None
    assert _lib.split_key("××") is None  # empty parts


# -- trend_slope ---------------------------------------------------------------


def test_trend_slope_monotonic_up_positive():
    assert _lib.trend_slope([1.0, 1.1, 1.2, 1.3, 1.4]) > 0


def test_trend_slope_monotonic_down_negative():
    assert _lib.trend_slope([1.4, 1.3, 1.2, 1.1, 1.0]) < 0


def test_trend_slope_flat_zero():
    assert _lib.trend_slope([0.5, 0.5, 0.5, 0.5]) == 0.0


def test_trend_slope_short_or_empty_zero():
    assert _lib.trend_slope([]) == 0.0
    assert _lib.trend_slope([1.0]) == 0.0


def test_trend_slope_equals_per_run_delta_for_linear_series():
    # Perfectly linear -> slope is exactly the per-run step.
    assert _lib.trend_slope([1.0, 1.5, 2.0, 2.5]) == 0.5
    assert _lib.trend_slope([2.5, 2.0, 1.5, 1.0]) == -0.5


# -- volatility ----------------------------------------------------------------


def test_volatility_empty_zero():
    assert _lib.volatility([]) == 0.0


def test_volatility_population_std():
    assert _lib.volatility([1.0, 2.0, 3.0]) == statistics.pstdev([1.0, 2.0, 3.0])


# -- analyze -------------------------------------------------------------------


def test_analyze_builds_sharpe_series_in_ts_order():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [
        _run_record("2026-08-01", {key: _result(1.0)}),
        _run_record("2026-08-02", {key: _result(1.2)}),
        _run_record("2026-08-03", {key: _result(0.9)}),
        _run_record("2026-08-04", {key: _result(1.1)}),
        _run_record("2026-08-05", {key: _result(1.3)}),
    ]
    payload = _lib.analyze(runs, _state(1.5, key), min_runs=5)
    entries = payload["analysis"]["1d"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["interval"] == "1d"
    assert entry["n_runs"] == 5
    assert entry["n_valid"] == 5
    assert entry["sharpe_latest"] == 1.3
    assert [s["sharpe"] for s in entry["sharpe_series"]] == [1.0, 1.2, 0.9, 1.1, 1.3]
    assert [s["ts"] for s in entry["sharpe_series"]] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
    ]


def test_analyze_series_sorted_by_ts_regardless_of_file_order():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [
        _run_record("2026-08-03", {key: _result(0.9)}),
        _run_record("2026-08-01", {key: _result(1.0)}),
        _run_record("2026-08-05", {key: _result(1.3)}),
        _run_record("2026-08-02", {key: _result(1.2)}),
        _run_record("2026-08-04", {key: _result(1.1)}),
    ]
    payload = _lib.analyze(runs, min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert [s["ts"] for s in entry["sharpe_series"]] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
    ]
    assert [s["sharpe"] for s in entry["sharpe_series"]] == [1.0, 1.2, 0.9, 1.1, 1.3]
    assert entry["sharpe_latest"] == 1.3  # latest by ts, not file order


def test_analyze_caps_sharpe_series_at_14():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [_run_record(f"ts-{i:02d}", {key: _result(1.0 + i * 0.01)}) for i in range(20)]
    payload = _lib.analyze(runs, min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert len(entry["sharpe_series"]) == 14
    assert entry["sharpe_latest"] == round(1.0 + 19 * 0.01, 6)


def test_analyze_min_runs_filter_keeps_combos_but_excludes_analysis():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [_run_record(f"ts-{i}", {key: _result(1.0)}) for i in range(4)]
    payload = _lib.analyze(runs, min_runs=5)
    assert len(payload["combos"]) == 1
    assert payload["analysis"] == {}


def test_analyze_downtrend_flag():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    sharpes = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]
    runs = [_run_record(f"ts-{i}", {key: _result(s)}) for i, s in enumerate(sharpes)]
    payload = _lib.analyze(runs, _state(1.5, key), min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert entry["trend_slope"] < _lib.DOWN_SLOPE
    assert entry["downtrend"] is True
    assert entry["improving"] is False
    assert payload["flags"]["decay"] == [entry]
    assert payload["flags"]["improving"] == []


def test_analyze_improving_flag():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    sharpes = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    runs = [_run_record(f"ts-{i}", {key: _result(s)}) for i, s in enumerate(sharpes)]
    payload = _lib.analyze(runs, _state(0.2, key), min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert entry["trend_slope"] > _lib.UP_SLOPE
    assert entry["improving"] is True
    assert entry["downtrend"] is False
    assert payload["flags"]["improving"] == [entry]
    assert payload["flags"]["decay"] == []


def test_analyze_no_baseline_suppresses_flags():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    sharpes = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    runs = [_run_record(f"ts-{i}", {key: _result(s)}) for i, s in enumerate(sharpes)]
    payload = _lib.analyze(runs, None, min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert entry["avg_sharpe_7n"] is None
    assert entry["downtrend"] is False
    assert entry["improving"] is False
    assert payload["flags"] == {"decay": [], "improving": [], "stable": []}


def test_analyze_excludes_insufficient_data_from_series():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [
        _run_record("ts-0", {key: _result(1.0)}),
        _run_record("ts-1", {key: _result(0.0, insufficient=True)}),  # 0.0 placeholder dropped
        _run_record("ts-2", {key: _result(1.2)}),
        _run_record("ts-3", {key: _result(1.1)}),
        _run_record("ts-4", {key: _result(1.3)}),
    ]
    payload = _lib.analyze(runs, _state(1.5, key), min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert entry["n_runs"] == 5  # appears in 5 runs ...
    assert [s["sharpe"] for s in entry["sharpe_series"]] == [1.0, 1.2, 1.1, 1.3]  # ... 4 valid


def test_analyze_min_trades_is_min_across_valid_runs():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [
        _run_record("ts-0", {key: _result(1.0, trades=30)}),
        _run_record("ts-1", {key: _result(1.1, trades=8)}),
        _run_record("ts-2", {key: _result(1.2, trades=25)}),
        _run_record("ts-3", {key: _result(1.3, trades=12)}),
        _run_record("ts-4", {key: _result(1.4, trades=20)}),
    ]
    payload = _lib.analyze(runs, _state(1.0, key), min_runs=5)
    assert payload["analysis"]["1d"][0]["min_trades"] == 8


def test_analyze_stable_flag_requires_valid_observations_and_low_volatility():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [_run_record(f"ts-{i}", {key: _result(1.0)}) for i in range(7)]
    payload = _lib.analyze(runs, _state(1.0, key), min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert entry["n_runs"] == 7
    assert entry["n_valid"] == 7
    assert entry["volatility"] == 0.0
    assert payload["flags"]["stable"] == [entry]


def test_analyze_stable_flag_ignores_appearances_without_valid_sharpes():
    key = _key("1d", "strategy-trend-follow", "BTCUSD")
    runs = [_run_record(f"ts-{i}", {key: _result(1.0, insufficient=(i > 0))}) for i in range(7)]
    payload = _lib.analyze(runs, _state(1.0, key), min_runs=5)
    entry = payload["analysis"]["1d"][0]
    assert entry["n_runs"] == 7  # >=7 appearances ...
    assert entry["n_valid"] == 1  # ... but only 1 valid sharpe observation
    assert entry["volatility"] == 0.0  # single point -> zero std
    assert payload["flags"]["stable"] == []  # not flagged stable


def test_analyze_flag_entries_carry_interval():
    key_1d = _key("1d", "strategy-trend-follow", "BTCUSD")
    key_4h = _key("4h", "strategy-trend-follow", "BTCUSD")
    runs = [_run_record(f"ts-{i}", {key_1d: _result(1.0), key_4h: _result(0.5)}) for i in range(7)]
    payload = _lib.analyze(runs, _state(1.0, key_1d), min_runs=5)
    for interval, entries in payload["analysis"].items():
        assert all(entry["interval"] == interval for entry in entries)
    stable = payload["flags"]["stable"]
    assert len(stable) == 2
    assert {entry["interval"] for entry in stable} == {"1d", "4h"}
    assert all(entry["strategy"] == "strategy-trend-follow" and entry["ticker"] == "BTCUSD" for entry in stable)


def test_analyze_decay_sorted_by_slope_ascending():
    key_a = _key("1d", "strategy-trend-follow", "AAA")
    key_b = _key("1d", "strategy-trend-follow", "BBB")
    runs = [_run_record(f"ts-{i}", {key_a: _result(1.0 - i * 0.05), key_b: _result(2.0 - i * 0.15)}) for i in range(6)]
    state = {
        "baseline": {
            key_a: {"history": [], "avg_sharpe_7n": 2.0, "n_samples": 7},
            key_b: {"history": [], "avg_sharpe_7n": 3.0, "n_samples": 7},
        }
    }
    payload = _lib.analyze(runs, state, min_runs=5)
    decay = payload["flags"]["decay"]
    slopes = [e["trend_slope"] for e in decay]
    assert slopes == sorted(slopes)  # ascending = steepest decay first
    assert decay[0]["ticker"] == "BBB"  # steeper decline first


def test_analyze_intervals_filter():
    key_1d = _key("1d", "strategy-trend-follow", "BTCUSD")
    key_4h = _key("4h", "strategy-trend-follow", "BTCUSD")
    runs = [
        _run_record("ts-0", {key_1d: _result(1.0), key_4h: _result(0.5)}),
        _run_record("ts-1", {key_1d: _result(1.1), key_4h: _result(0.6)}),
        _run_record("ts-2", {key_1d: _result(1.2), key_4h: _result(0.7)}),
        _run_record("ts-3", {key_1d: _result(1.3), key_4h: _result(0.8)}),
        _run_record("ts-4", {key_1d: _result(1.4), key_4h: _result(0.9)}),
    ]
    payload = _lib.analyze(runs, min_runs=5, intervals=["1d"])
    assert sorted(c["interval"] for c in payload["combos"]) == ["1d"]
    assert set(payload["analysis"]) == {"1d"}


def test_analyze_empty_runs_returns_empty_payload():
    payload = _lib.analyze([], None)
    assert payload == {"combos": [], "analysis": {}, "flags": {"decay": [], "improving": [], "stable": []}}


# -- missing-file fallback -----------------------------------------------------


def test_missing_file_fallback_emits_empty_state(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(_run, "_resolve_out_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["backtest-trend-miner", "--json"])
    rc = _run.main()
    captured = capsys.readouterr()
    assert rc == 0
    env = json.loads(captured.out)
    assert env["data"] is None
    assert env["count"] == 0
    assert env["errors"] == ["no runs.jsonl data found"]
    assert env["help"]  # at least one help line


def test_load_runs_and_state_missing_files(monkeypatch, tmp_path):
    assert _run._load_runs(tmp_path) == []
    assert _run._load_state(tmp_path) == {}


# -- no hardcoded tickers ------------------------------------------------------


def test_no_ticker_symbols_hardcoded_in_source():
    for rel in ("lib.py", "scripts/run.py", "SKILL.md"):
        path = os.path.join(_SKILL_DIR, rel)
        with open(path) as fh:
            text = fh.read()
        for symbol in ("BTCUSD", "ETHUSD", "SOLUSD", "AAPL", "HYPEUSD"):
            assert symbol not in text, f"{rel} hardcodes {symbol}"
