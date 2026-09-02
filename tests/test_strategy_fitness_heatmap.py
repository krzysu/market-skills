"""Tests for the strategy-fitness-heatmap skill (review finding MINOR).

The heatmap orchestrates watchlist + l3_strategies registry + backtest-engine
walk-forward replay + FillSimulator + compute metrics + FetchOnce-per-ticker
semantics. These tests pin that orchestration end-to-end without touching the
network: ``fetch_ohlc`` and ``load_skill`` are monkeypatched on the loaded run
module, while the real ``backtest-engine/lib.py`` is loaded via importlib so
the WalkForwardRunner / FillSimulator / compute / buy_and_hold_benchmark
pipeline is exercised for real.

Covers the seven cases required by the review:

  1. empty ticker list
  2. empty strategy list (l3_strategies patched to [])
  3. missing backtest-engine skill (load_skill -> None for "backtest-engine")
  4. fetch_ohlc failure (ticker returns no candles)
  5. insufficient candles (fewer candles than the interval warmup)
  6. strategy not found (load_skill -> None for a strategy name)
  7. synthetic candle set producing a known Sharpe value

All tests are deterministic and network-free.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import random
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_run_module():
    """Load skills/strategy-fitness-heatmap/scripts/run.py dynamically."""
    run_path = os.path.join(REPO_ROOT, "skills", "strategy-fitness-heatmap", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("strategy_fitness_heatmap_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bt_lib():
    """Load skills/backtest-engine/lib.py dynamically (the real engine)."""
    lib_path = os.path.join(REPO_ROOT, "skills", "backtest-engine", "lib.py")
    spec = importlib.util.spec_from_file_location("bt_lib_for_heatmap", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RUN = _load_run_module()
_BT = _load_bt_lib()


def _make_candles(n=250, seed=42, base=100.0, drift=0.001, noise=0.01) -> list[list]:
    """Deterministic synthetic OHLC uptrend: [[ts, o, h, l, c, v], ...].

    Closes follow ``open * (1 + drift + uniform(-noise, noise))`` so the series
    trends up with variance (a buy-and-hold benchmark over this set produces a
    finite, positive Sharpe). High/low are widened past max/min(open, close) so
    every bar is valid OHLC.
    """
    rng = random.Random(seed)
    price = base
    out: list[list] = []
    for i in range(n):
        open_p = price
        ret = drift + rng.uniform(-noise, noise)
        close_p = open_p * (1 + ret)
        high_p = max(open_p, close_p) + abs(rng.uniform(0, noise))
        low_p = min(open_p, close_p) - abs(rng.uniform(0, noise))
        price = close_p
        out.append([i * 86400, open_p, high_p, low_p, close_p, 100_000])
    return out


class _NoIdeaStrategy:
    """Strategy that never fires an idea -> no trades -> Sharpe 0.0 (known)."""

    def analyze(self, candles, *, ticker, interval="1d", period="1y", asset_class=None):
        return {"ideas": [], "narrative": "no ideas"}


class _RaisingStrategy:
    """Strategy whose analyze always raises -> per-combo error, Sharpe 0.0."""

    def analyze(self, candles, *, ticker, interval="1d", period="1y", asset_class=None):
        raise RuntimeError("boom")


def _install_load_skill(mod, monkeypatch, *, bt_lib, strategies_map) -> None:
    """Patch ``mod.load_skill`` to return ``bt_lib`` for backtest-engine and canned strategies."""

    def _fake_load(name):
        if name == "backtest-engine":
            return bt_lib
        return strategies_map.get(name)

    monkeypatch.setattr(mod, "load_skill", _fake_load)


def _install_fetch_ohlc(mod, monkeypatch, candles) -> None:
    """Patch ``mod.fetch_ohlc`` to return ``candles`` regardless of args."""
    monkeypatch.setattr(mod, "fetch_ohlc", lambda *a, **kw: candles)


def _run_main(mod, monkeypatch, *argv) -> int:
    """Invoke ``mod.main()`` with a synthesized argv, returning the exit code."""
    monkeypatch.setattr(sys, "argv", ["run.py", *argv])
    try:
        mod.main()
        return 0
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0


def _envelope(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# --- Case 1: empty ticker list ------------------------------------------------


def test_empty_ticker_list(capsys, monkeypatch):
    """No tickers -> zero rows, zero details, count=0, no errors."""
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"fake-strategy": _NoIdeaStrategy()})
    _install_fetch_ohlc(_RUN, monkeypatch, _make_candles())
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "--strategies", "fake-strategy")
    assert rc == 0
    env = _envelope(capsys)
    assert env["errors"] == []
    assert env["count"] == 0
    data = env["data"]
    assert data["tickers"] == []
    assert data["strategies"] == ["fake-strategy"]
    for iv in ("1d", "4h"):
        matrix = data["intervals"][iv]["matrix"]
        assert matrix["tickers"] == []
        assert matrix["strategies"] == ["fake-strategy"]
        assert matrix["values"] == []
        assert data["intervals"][iv]["details"] == []


# --- Case 2: empty strategy list ----------------------------------------------


def test_empty_strategy_list(capsys, monkeypatch):
    """l3_strategies patched to [] -> zero columns, one empty row per ticker, count=0."""
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={})
    _install_fetch_ohlc(_RUN, monkeypatch, [])
    monkeypatch.setattr(_RUN, "l3_strategies", lambda: [])
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD")
    assert rc == 0
    env = _envelope(capsys)
    assert env["errors"] == []
    assert env["count"] == 0
    data = env["data"]
    assert data["strategies"] == []
    assert data["tickers"] == ["BTCUSD"]
    for iv in ("1d", "4h"):
        matrix = data["intervals"][iv]["matrix"]
        assert matrix["tickers"] == ["BTCUSD"]
        assert matrix["strategies"] == []
        assert matrix["values"] == [[]]  # one row per ticker, zero columns
        assert data["intervals"][iv]["details"] == []


# --- Case 3: missing backtest-engine skill ------------------------------------


def test_missing_backtest_engine_skill(capsys, monkeypatch):
    """load_skill -> None for backtest-engine -> envelope data=None, exit code 2."""
    monkeypatch.setattr(_RUN, "load_skill", lambda name: None)
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "fake-strategy")
    assert rc == 2
    env = _envelope(capsys)
    assert env["data"] is None
    assert env["count"] == 0
    assert env["errors"] == ["backtest-engine skill not found"]
    assert isinstance(env["help"], list) and env["help"]


# --- Case 4: fetch_ohlc failure (ticker returns no candles) -------------------


def test_fetch_ohlc_returns_no_candles(capsys, monkeypatch):
    """fetch_ohlc -> [] -> every combo carries the no-candles error, Sharpe 0.0."""
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"fake-strategy": _NoIdeaStrategy()})
    _install_fetch_ohlc(_RUN, monkeypatch, [])
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "fake-strategy")
    assert rc == 0
    env = _envelope(capsys)
    data = env["data"]
    for iv in ("1d", "4h"):
        details = data["intervals"][iv]["details"]
        assert len(details) == 1
        d = details[0]
        assert d["ticker"] == "BTCUSD"
        assert d["strategy"] == "fake-strategy"
        assert d["interval"] == iv
        assert d["sharpe"] == 0.0
        assert d["bars"] == 0
        assert d["trade_count"] == 0
        assert d["error"] == "fetch_ohlc returned no candles"
        assert d["metrics"]["trade_count"] == 0
        assert d["metrics"]["sharpe"] == 0.0
        # Benchmark also collapses to the zero shape when there are no candles.
        assert d["benchmark"]["sharpe"] == 0.0
        assert data["intervals"][iv]["matrix"]["values"] == [[0.0]]
    # Deduped error surfaces at the envelope level (one per interval, deduped).
    assert env["errors"] == ["fetch_ohlc returned no candles"]
    assert env["count"] == 2  # 1 strategy * 1 ticker * 2 intervals


# --- Case 5: insufficient candles (fewer than warmup) -------------------------


def test_insufficient_candles(capsys, monkeypatch):
    """100 candles < warmup for both 1d (200) and 4h (500) -> 'insufficient candles'."""
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"fake-strategy": _NoIdeaStrategy()})
    _install_fetch_ohlc(_RUN, monkeypatch, _make_candles(n=100))
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "fake-strategy")
    assert rc == 0
    env = _envelope(capsys)
    data = env["data"]
    for iv in ("1d", "4h"):
        details = data["intervals"][iv]["details"]
        assert len(details) == 1
        d = details[0]
        assert d["sharpe"] == 0.0
        assert d["bars"] == 100
        assert d["trade_count"] == 0
        assert d["error"] == "insufficient candles"
        assert d["metrics"]["sharpe"] == 0.0
        assert d["metrics"]["trade_count"] == 0
        # Benchmark collapses to the zero shape on insufficient candles too.
        assert d["benchmark"]["sharpe"] == 0.0
        assert data["intervals"][iv]["matrix"]["values"] == [[0.0]]
    assert env["errors"] == ["insufficient candles"]


# --- Case 6: strategy not found (load_skill -> None for a strategy) ------------


def test_strategy_not_found(capsys, monkeypatch):
    """backtest-engine loads but the strategy does not -> 'strategy X not found', Sharpe 0.0."""
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={})
    _install_fetch_ohlc(_RUN, monkeypatch, _make_candles(n=250))
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "ghost-strategy")
    assert rc == 0
    env = _envelope(capsys)
    data = env["data"]
    for iv in ("1d", "4h"):
        details = data["intervals"][iv]["details"]
        assert len(details) == 1
        d = details[0]
        assert d["strategy"] == "ghost-strategy"
        assert d["sharpe"] == 0.0
        assert d["trade_count"] == 0
        assert d["bars"] == 250
        assert d["error"] == "strategy 'ghost-strategy' not found"
        assert d["metrics"]["sharpe"] == 0.0
        assert d["metrics"]["trade_count"] == 0
        assert data["intervals"][iv]["matrix"]["values"] == [[0.0]]
        # Benchmark is still computed from the real candles and is finite.
        assert math.isfinite(d["benchmark"]["sharpe"])
    assert env["errors"] == ["strategy 'ghost-strategy' not found"]


# --- Case 7: synthetic candle set producing a known Sharpe value --------------


def test_synthetic_known_sharpe(capsys, monkeypatch):
    """A no-idea strategy over a synthetic uptrend produces the known Sharpe 0.0.

    The no-idea strategy fires zero ideas -> ``_run_strategy`` builds an empty
    trade list -> ``_build_equity_curve`` returns ``[_BASE_CAPITAL]`` (one
    point) -> ``compute`` returns ``sharpe=0.0`` (fewer than two daily
    returns). That value is asserted *exactly* below for both intervals, which
    is the "known Sharpe value" guarantee. The benchmark over the same
    uptrending candles is a real buy-and-hold curve with a positive total
    return and a finite Sharpe, proving the compute path ran end-to-end on the
    real candles.

    600 bars clears both warmups (1d=200, 4h=500) so neither interval short-
    circuits to the "insufficient candles" branch.
    """
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    candles = _make_candles(n=600, seed=7, base=100.0, drift=0.002, noise=0.005)
    _install_fetch_ohlc(_RUN, monkeypatch, candles)
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "no-idea")
    assert rc == 0
    env = _envelope(capsys)
    assert env["errors"] == []
    assert env["count"] == 2  # 1 strategy * 1 ticker * 2 intervals
    data = env["data"]
    for iv in ("1d", "4h"):
        matrix = data["intervals"][iv]["matrix"]
        assert matrix["tickers"] == ["BTCUSD"]
        assert matrix["strategies"] == ["no-idea"]
        assert matrix["values"] == [[0.0]]  # known Sharpe: no trades -> 0.0
        details = data["intervals"][iv]["details"]
        assert len(details) == 1
        d = details[0]
        assert d["strategy"] == "no-idea"
        assert d["interval"] == iv
        assert d["error"] is None
        assert d["sharpe"] == 0.0  # known value, asserted exactly
        assert d["metrics"]["sharpe"] == 0.0
        assert d["metrics"]["trade_count"] == 0
        assert d["bars"] == 600
        # Buy-and-hold benchmark over the uptrend: positive return, finite Sharpe.
        bench = d["benchmark"]
        assert bench["trade_count"] == 0
        assert bench["total_return"] > 0.0
        assert math.isfinite(bench["sharpe"])
        assert bench["sharpe"] > 0.0


def test_synthetic_heatmap_is_deterministic(capsys, monkeypatch):
    """Two runs over the same synthetic candles produce byte-identical envelopes.

    Pins the determinism guarantee from the SKILL.md: the FillSimulator and
    walk-forward runner are stateless, so once ``fetch_ohlc`` returns a fixed
    candle set every downstream Sharpe is reproducible.
    """
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    candles = _make_candles(n=600, seed=11, base=100.0, drift=0.002, noise=0.005)
    _install_fetch_ohlc(_RUN, monkeypatch, candles)

    first = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "no-idea")
    out1 = capsys.readouterr().out
    second = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "no-idea")
    out2 = capsys.readouterr().out

    assert first == 0 == second
    assert out1 == out2  # byte-identical JSON across runs
    assert json.loads(out1)["data"]["intervals"]["1d"]["matrix"]["values"] == [[0.0]]


# --- Bonus: per-combo exception is captured, not propagated --------------------


def test_strategy_exception_becomes_per_combo_error(capsys, monkeypatch):
    """A strategy whose analyze raises -> Sharpe 0.0 + error string, no crash.

    Pins the ``try/except`` branch in ``_run_strategy``: the heatmap must
    continue past a failing combo and record the exception text rather than
    aborting the whole grid.
    """
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"boom": _RaisingStrategy()})
    # 600 bars so the run reaches _run_strategy (past the insufficient check).
    _install_fetch_ohlc(_RUN, monkeypatch, _make_candles(n=600, seed=3))
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "boom")
    assert rc == 0
    env = _envelope(capsys)
    data = env["data"]
    for iv in ("1d", "4h"):
        details = data["intervals"][iv]["details"]
        assert len(details) == 1
        d = details[0]
        assert d["strategy"] == "boom"
        assert d["sharpe"] == 0.0
        assert d["metrics"]["sharpe"] == 0.0
        assert d["metrics"]["trade_count"] == 0
        assert d["error"] is not None
        assert d["error"].startswith("RuntimeError: ")
        assert data["intervals"][iv]["matrix"]["values"] == [[0.0]]
    assert len(env["errors"]) == 1
    assert env["errors"][0].startswith("RuntimeError: ")


# --- Bonus: FetchOnce semantics — candles fetched once per (ticker, interval) --


def test_candles_fetched_once_per_ticker_per_interval(capsys, monkeypatch):
    """One fetch_ohlc call per (ticker, interval), reused across all strategies.

    The heatmap's FetchOnce contract: with 1 ticker and 2 strategies over 2
    intervals, fetch_ohlc is called exactly 2 times (once per interval), not
    2*2=4 times. This is the per-ticker network-cost optimization the SKILL.md
    documents.
    """
    calls: list[tuple] = []

    def _tracking_fetch(ticker, interval="1d", period="1y", source=None):
        calls.append((ticker, interval, period))
        return _make_candles(n=600, seed=5)

    _install_load_skill(
        _RUN,
        monkeypatch,
        bt_lib=_BT,
        strategies_map={"no-idea": _NoIdeaStrategy(), "also-no-idea": _NoIdeaStrategy()},
    )
    monkeypatch.setattr(_RUN, "fetch_ohlc", _tracking_fetch)
    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "no-idea", "also-no-idea")
    assert rc == 0
    # 2 intervals * 1 ticker = 2 fetches; strategies reuse the cached candles.
    assert len(calls) == 2
    intervals_seen = sorted(c[1] for c in calls)
    assert intervals_seen == ["1d", "4h"]
    env = _envelope(capsys)
    assert env["count"] == 4  # 2 strategies * 1 ticker * 2 intervals
