"""Tests for the strategy-fitness-heatmap skill (review finding MINOR).

The heatmap has two sources behind one AXI output shape:

- **default (nightly)**: reads the backtest-pipeline's ``fitness_matrix.json``
  + latest ``runs.jsonl`` run from ``$MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR``
  (repo-relative ``data/backtest-nightly/`` fallback) - zero network;
- **``--fresh``** (and the missing-artifact fallback): the full walk-forward
  replay + FillSimulator + compute grid over the registry x watchlist.

The fresh-grid tests pin that orchestration end-to-end without touching the
network: ``fetch_ohlc`` and ``load_skill`` are monkeypatched on the loaded run
module, while the real ``backtest-engine/lib.py`` is loaded via importlib so
the WalkForwardRunner / FillSimulator / compute / buy_and_hold_benchmark
pipeline is exercised for real. They all pass explicit ``--tickers`` /
``--strategies`` overrides (which imply ``--fresh``), so they always exercise
the fresh grid.

Covers the seven fresh-grid cases required by the review:

  1. empty ticker list
  2. empty strategy list (l3_strategies patched to [])
  3. missing backtest-engine skill (load_skill -> None for "backtest-engine")
  4. fetch_ohlc failure (ticker returns no candles)
  5. insufficient candles (fewer candles than the interval warmup)
  6. strategy not found (load_skill -> None for a strategy name)
  7. synthetic candle set producing a known Sharpe value

Plus the nightly-default-path cases:

  8. default reads fitness_matrix.json + latest runs.jsonl run (zero network)
  9. JSON null matrix cells are preserved, not coerced to 0.0
  10. missing / unreadable / empty artifacts degrade to the fresh grid
  11. --fresh (and grid overrides) force the recompute over nightly artifacts
  12. non-dict runs.jsonl results entry degrades to the no-result shape
  13. --help prints the skill's own flags (not the generic AXI usage)
  14. human mode surfaces the fallback note + errors on stderr

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


# --- Nightly default path ------------------------------------------------------
#
# The default (no --fresh, no --tickers/--strategies) reads the backtest
# pipeline's fitness_matrix.json + runs.jsonl from
# $MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR. Fixtures below mirror the real
# artifact shapes: values are rows=tickers x cols=strategies with JSON null
# cells, and runs.jsonl is oldest-first with the LATEST run as the last line.


_SEP = "\u00d7"  # × (U+00D7) — the runs.jsonl key separator


def _nightly_matrix_fixture() -> dict:
    """Synthetic fitness_matrix.json.

    Deliberately uses a strategy name that is NOT in the registry and a
    ticker that is NOT in the watchlist, pinning that the file's own
    tickers/strategies lists are the source of truth. Null cells line up
    with the run fixture: a combo absent from the latest run, and a combo
    flagged insufficient_data (the pipeline excludes insufficient combos
    from the matrix entirely), are both null in the real nightly matrix.
    """
    return {
        "intervals": {
            "1d": {
                "tickers": ["BTCUSD", "ZTESTUSD"],
                "strategies": ["strategy-trend-follow", "strategy-not-in-registry"],
                "values": [[1.23, None], [None, None]],
            },
            "4h": {
                "tickers": ["BTCUSD", "ZTESTUSD"],
                "strategies": ["strategy-trend-follow", "strategy-not-in-registry"],
                "values": [[0.11, None], [2.5, None]],
            },
        },
        "generated_at": "2026-09-03T00:52:22+00:00",
    }


def _nightly_run_fixture(ts: str, sharpe_offset: float = 0.0) -> dict:
    """One runs.jsonl line, mirroring the backtest-pipeline's run record."""

    def _res(strategy: str, ticker: str, sharpe: float, trades: int, *, insufficient: bool = False) -> dict:
        return {
            "strategy": strategy,
            "ticker": ticker if ":" in ticker else f"kraken:{ticker}",
            "asof": ts,
            "ideas": 3,
            "trades": trades,
            "strategy_sharpe": sharpe,
            "strategy_total_return": 0.42,
            "strategy_max_dd": -0.1,
            "strategy_profit_factor": 1.7,
            "benchmark_sharpe": 0.9,
            "benchmark_total_return": 0.3,
            "bars": 500,
            "windows": 400,
            "provider": "kraken",
            "insufficient_data": insufficient,
        }

    return {
        "ts": ts,
        "strategies": ["strategy-trend-follow", "strategy-not-in-registry"],
        "tickers": ["BTCUSD", "ZTESTUSD"],
        "results": {
            f"1d{_SEP}strategy-trend-follow{_SEP}BTCUSD": _res(
                "strategy-trend-follow", "BTCUSD", 1.23 + sharpe_offset, 12
            ),
            f"1d{_SEP}strategy-not-in-registry{_SEP}ZTESTUSD": _res(
                "strategy-not-in-registry", "ZTESTUSD", 0.0 + sharpe_offset, 4, insufficient=True
            ),
            f"4h{_SEP}strategy-trend-follow{_SEP}BTCUSD": _res(
                "strategy-trend-follow", "BTCUSD", 0.11 + sharpe_offset, 30
            ),
            f"4h{_SEP}strategy-trend-follow{_SEP}ZTESTUSD": _res(
                "strategy-trend-follow", "ZTESTUSD", 2.5 + sharpe_offset, 8
            ),
        },
        "errors": [f"1d{_SEP}strategy-not-in-registry{_SEP}BTCUSD"],
        "insufficient_data": [f"1d{_SEP}strategy-not-in-registry{_SEP}ZTESTUSD (bars=214, provider=kraken)"],
    }


def _write_nightly_artifacts(out_dir, *, matrix: dict | None, runs: list[dict] | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if matrix is not None:
        (out_dir / "fitness_matrix.json").write_text(json.dumps(matrix))
    if runs is not None:
        (out_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))


def _forbid_network_and_skills(mod, monkeypatch) -> None:
    """Default-path guard: any fetch or skill load means the nightly path leaked."""

    def _no_fetch(*a, **kw):
        raise AssertionError("fetch_ohlc called in the default nightly path (network leak)")

    def _no_load(name):
        raise AssertionError(f"load_skill({name!r}) called in the default nightly path")

    monkeypatch.setattr(mod, "fetch_ohlc", _no_fetch)
    monkeypatch.setattr(mod, "load_skill", _no_load)


def test_default_reads_nightly_artifacts(capsys, monkeypatch, tmp_path):
    """Default mode: matrix verbatim from fitness_matrix.json, details from the LATEST run.

    Two runs are written (oldest first); the oldest carries a +100 Sharpe
    offset, so any detail showing the un-offset value proves the LAST
    non-empty line is the source. fetch_ohlc / load_skill raise if called,
    proving zero network and no recompute.
    """
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    _write_nightly_artifacts(
        tmp_path,
        matrix=_nightly_matrix_fixture(),
        runs=[
            _nightly_run_fixture("2026-09-02T00:52:22+00:00", sharpe_offset=100.0),
            _nightly_run_fixture("2026-09-03T00:52:22+00:00"),
        ],
    )
    _forbid_network_and_skills(_RUN, monkeypatch)

    rc = _run_main(_RUN, monkeypatch, "--json")
    assert rc == 0
    env = _envelope(capsys)
    assert env["count"] == 8  # 2 tickers * 2 strategies * 2 intervals
    data = env["data"]

    # Matrix emitted exactly as read (tickers/strategies from the FILE, nulls kept).
    matrix_fx = _nightly_matrix_fixture()
    for iv in ("1d", "4h"):
        matrix = data["intervals"][iv]["matrix"]
        assert matrix["tickers"] == matrix_fx["intervals"][iv]["tickers"]
        assert matrix["strategies"] == matrix_fx["intervals"][iv]["strategies"]
        assert matrix["values"] == matrix_fx["intervals"][iv]["values"]
    # Flat lists stay consistent with the file (not the registry/watchlist).
    assert data["strategies"] == ["strategy-trend-follow", "strategy-not-in-registry"]
    assert data["tickers"] == ["BTCUSD", "ZTESTUSD"]
    assert data["config"]["source"] == "nightly"
    assert data["config"]["env_var"] == "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR"
    assert data["config"]["generated_at"] == "2026-09-03T00:52:22+00:00"
    assert data["config"]["run_ts"] == "2026-09-03T00:52:22+00:00"

    # details[0] = (1d, BTCUSD, strategy-trend-follow): enriched from the latest run.
    d = data["intervals"]["1d"]["details"][0]
    assert d["ticker"] == "BTCUSD"  # bare watchlist key, not the run's kraken:BTCUSD
    assert d["strategy"] == "strategy-trend-follow"
    assert d["interval"] == "1d"
    assert d["sharpe"] == 1.23  # latest run, not the +100-offset older run
    assert d["strategy_sharpe"] == 1.23
    assert d["trade_count"] == 12
    assert d["trades"] == 12
    assert d["profit_factor"] == 1.7
    assert d["max_dd"] == -0.1
    assert d["benchmark_sharpe"] == 0.9
    assert d["bars"] == 500
    assert d["provider"] == "kraken"
    assert d["insufficient_data"] is False
    assert d["error"] is None
    assert d["metrics"]["sharpe"] == 1.23
    assert d["metrics"]["trade_count"] == 12
    assert d["benchmark"]["sharpe"] == 0.9
    assert d["asof"] == "2026-09-03T00:52:22+00:00"

    # Combo absent from the latest run (matrix cell null): unknowns stay null.
    d = data["intervals"]["1d"]["details"][1]
    assert d["strategy"] == "strategy-not-in-registry"
    assert d["sharpe"] is None
    assert d["trade_count"] is None
    assert d["bars"] is None
    assert d["provider"] is None
    assert d["error"] == "no result in latest nightly run"

    # Combo flagged insufficient_data by the nightly: the pipeline excludes it
    # from the matrix (cell is JSON null), so the detail sharpe mirrors null
    # rather than the stored placeholder 0.0; the enriched metrics fields stay
    # populated from the stored run record.
    d = data["intervals"]["1d"]["details"][3]
    assert d["strategy"] == "strategy-not-in-registry"
    assert d["insufficient_data"] is True
    assert d["error"] == "insufficient_data"
    assert d["sharpe"] is None
    assert d["strategy_sharpe"] is None
    assert d["metrics"]["sharpe"] is None
    assert d["trade_count"] == 4
    assert d["bars"] == 500
    assert d["provider"] == "kraken"

    # 4h trend-follow rows enriched too.
    assert data["intervals"]["4h"]["details"][0]["sharpe"] == 0.11
    assert data["intervals"]["4h"]["details"][2]["sharpe"] == 2.5

    # Deduped per-combo errors surface at the envelope level (fresh-path contract).
    assert env["errors"] == ["no result in latest nightly run", "insufficient_data"]


def test_nightly_null_cells_preserved(capsys, monkeypatch, tmp_path):
    """JSON null matrix cells are emitted as null, never coerced to 0.0."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    _write_nightly_artifacts(
        tmp_path,
        matrix=_nightly_matrix_fixture(),
        runs=[_nightly_run_fixture("2026-09-03T00:52:22+00:00")],
    )
    _forbid_network_and_skills(_RUN, monkeypatch)

    rc = _run_main(_RUN, monkeypatch, "--json")
    assert rc == 0
    env = _envelope(capsys)
    values_1d = env["data"]["intervals"]["1d"]["matrix"]["values"]
    values_4h = env["data"]["intervals"]["4h"]["matrix"]["values"]
    # Exact positions, exactly as stored in the fixture file.
    assert values_1d == [[1.23, None], [None, None]]
    assert values_4h == [[0.11, None], [2.5, None]]
    assert values_1d[0][1] is None
    assert values_1d[1][0] is None
    # A null cell's detail mirrors the null (no fabricated 0.0 sharpe).
    detail = env["data"]["intervals"]["1d"]["details"][1]
    assert detail["sharpe"] is None
    assert detail["error"] == "no result in latest nightly run"


def test_missing_nightly_artifacts_falls_back_to_fresh(capsys, monkeypatch, tmp_path):
    """Empty artifact dir -> fresh grid runs, with the fallback note in errors + help."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path / "does-not-exist"))
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    # No --tickers/--strategies override (that would skip the nightly read), so
    # shrink the default fresh grid to one combo via the module's list providers.
    monkeypatch.setattr(_RUN, "l3_strategies", lambda: ["no-idea"])
    monkeypatch.setattr(_RUN, "_default_tickers", lambda: ["BTCUSD"])
    calls: list[tuple] = []
    monkeypatch.setattr(_RUN, "fetch_ohlc", lambda *a, **kw: calls.append(kw) or _make_candles(n=600))

    rc = _run_main(_RUN, monkeypatch, "--json")
    assert rc == 0
    env = _envelope(capsys)
    # Fallback note first, then the (none) fresh-grid combo errors.
    assert env["errors"] == ["nightly fitness_matrix.json not found — falling back to fresh grid"]
    assert any("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR" in h for h in env["help"])
    assert any("backtest-pipeline" in h for h in env["help"])
    # The fresh grid actually ran (both intervals fetched, known no-idea Sharpe).
    assert len(calls) == 2
    data = env["data"]
    for iv in ("1d", "4h"):
        assert data["intervals"][iv]["matrix"]["values"] == [[0.0]]
    # Fresh payload config shape (not the nightly source marker).
    assert "base_capital" in data["config"]
    assert "source" not in data["config"]


def test_unreadable_fitness_matrix_falls_back_to_fresh(capsys, monkeypatch, tmp_path):
    """Malformed fitness_matrix.json -> unreadable note + fresh grid, no crash."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "fitness_matrix.json").write_text("{not valid json")
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    monkeypatch.setattr(_RUN, "l3_strategies", lambda: ["no-idea"])
    monkeypatch.setattr(_RUN, "_default_tickers", lambda: ["BTCUSD"])
    _install_fetch_ohlc(_RUN, monkeypatch, _make_candles(n=600))

    rc = _run_main(_RUN, monkeypatch, "--json")
    assert rc == 0
    env = _envelope(capsys)
    assert env["errors"] == ["nightly fitness_matrix.json unreadable — falling back to fresh grid"]
    assert env["data"]["intervals"]["1d"]["matrix"]["values"] == [[0.0]]


def test_empty_runs_jsonl_falls_back_to_fresh(capsys, monkeypatch, tmp_path):
    """Valid matrix but zero usable runs -> runs.jsonl note + fresh grid."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "fitness_matrix.json").write_text(json.dumps(_nightly_matrix_fixture()))
    (tmp_path / "runs.jsonl").write_text("\n \n")  # exists but no non-empty lines
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    monkeypatch.setattr(_RUN, "l3_strategies", lambda: ["no-idea"])
    monkeypatch.setattr(_RUN, "_default_tickers", lambda: ["BTCUSD"])
    _install_fetch_ohlc(_RUN, monkeypatch, _make_candles(n=600))

    rc = _run_main(_RUN, monkeypatch, "--json")
    assert rc == 0
    env = _envelope(capsys)
    assert env["errors"] == ["nightly runs.jsonl not found or empty — falling back to fresh grid"]
    assert env["data"]["intervals"]["1d"]["matrix"]["values"] == [[0.0]]


def test_fresh_flag_forces_recompute_over_nightly(capsys, monkeypatch, tmp_path):
    """--fresh ignores present nightly artifacts and recomputes the grid live."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    _write_nightly_artifacts(
        tmp_path,
        matrix=_nightly_matrix_fixture(),
        runs=[_nightly_run_fixture("2026-09-03T00:52:22+00:00")],
    )
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    # Pure --fresh (no overrides): shrink the default grid to one combo.
    monkeypatch.setattr(_RUN, "l3_strategies", lambda: ["no-idea"])
    monkeypatch.setattr(_RUN, "_default_tickers", lambda: ["BTCUSD"])
    calls: list[tuple] = []
    monkeypatch.setattr(_RUN, "fetch_ohlc", lambda *a, **kw: calls.append(kw) or _make_candles(n=600))

    rc = _run_main(_RUN, monkeypatch, "--json", "--fresh")
    assert rc == 0
    env = _envelope(capsys)
    assert len(calls) == 2  # live recompute happened
    # Fresh-computed values, NOT the nightly 1.23 / 0.11 cells.
    for iv in ("1d", "4h"):
        assert env["data"]["intervals"][iv]["matrix"]["values"] == [[0.0]]
    assert env["errors"] == []  # no fallback note when --fresh was explicit
    assert "base_capital" in env["data"]["config"]
    assert "source" not in env["data"]["config"]


def test_grid_override_forces_fresh_over_nightly(capsys, monkeypatch, tmp_path):
    """--tickers/--strategies select fresh-recompute inputs, so they imply --fresh."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    _write_nightly_artifacts(
        tmp_path,
        matrix=_nightly_matrix_fixture(),
        runs=[_nightly_run_fixture("2026-09-03T00:52:22+00:00")],
    )
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    calls: list[tuple] = []
    monkeypatch.setattr(_RUN, "fetch_ohlc", lambda *a, **kw: calls.append(kw) or _make_candles(n=600))

    rc = _run_main(_RUN, monkeypatch, "--json", "--tickers", "BTCUSD", "--strategies", "no-idea")
    assert rc == 0
    env = _envelope(capsys)
    assert len(calls) == 2
    assert env["data"]["intervals"]["1d"]["matrix"]["values"] == [[0.0]]
    assert env["data"]["strategies"] == ["no-idea"]
    assert env["data"]["tickers"] == ["BTCUSD"]
    assert env["errors"] == []  # nightly artifacts present, so no fallback note


# --- Non-dict runs.jsonl results value (Finding: guard _nightly_detail) --------


def test_nightly_non_dict_results_value_degrades(capsys, monkeypatch, tmp_path):
    """A runs.jsonl ``results`` entry that is not a dict must not crash.

    ``_load_runs`` only validates top-level lines as dicts, so a ``results``
    value like a bare string used to hit ``result.get(...)`` and raise
    AttributeError. It must degrade to the "no result in latest nightly run"
    detail shape instead of taking down the default path.
    """
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    run_fx = _nightly_run_fixture("2026-09-03T00:52:22+00:00")
    run_fx["results"][f"1d{_SEP}strategy-trend-follow{_SEP}BTCUSD"] = "not a dict"
    _write_nightly_artifacts(tmp_path, matrix=_nightly_matrix_fixture(), runs=[run_fx])
    _forbid_network_and_skills(_RUN, monkeypatch)

    rc = _run_main(_RUN, monkeypatch, "--json")
    assert rc == 0
    env = _envelope(capsys)
    # The malformed combo degrades to the no-result shape (sharpe from the
    # matrix cell, all enriched fields null).
    d = env["data"]["intervals"]["1d"]["details"][0]
    assert d["strategy"] == "strategy-trend-follow"
    assert d["ticker"] == "BTCUSD"
    assert d["error"] == "no result in latest nightly run"
    assert d["sharpe"] == 1.23
    assert d["trade_count"] is None
    # The remaining combos still enrich normally.
    assert env["data"]["intervals"]["1d"]["details"][3]["trade_count"] == 4


# --- --help surface (Finding: parse_axi_flags intercepts -h/--help) ------------


def test_help_prints_skill_flags_not_generic_axi_usage(capsys, monkeypatch):
    """--help / -h show this skill's own flags, not the ticker-based AXI usage."""
    for argv in (["--help"], ["-h"]):
        rc = _run_main(_RUN, monkeypatch, *argv)
        assert rc == 0
        out = capsys.readouterr().out
        assert "usage: strategy-fitness-heatmap" in out
        for flag in ("--json", "--fresh", "--tickers", "--strategies"):
            assert flag in out
        # The generic AXI usage implies a positional TICKER + --source this
        # skill does not accept - it must not leak through.
        assert "TICKER [--json]" not in out
        assert "--source=PROVIDER" not in out


# --- Human (non---json) mode error/fallback visibility (Finding 4) -------------


def test_human_mode_surfaces_fallback_and_errors(capsys, monkeypatch, tmp_path):
    """Without --json, the fallback note + errors print on stderr, matrix on stdout."""
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path / "does-not-exist"))
    _install_load_skill(_RUN, monkeypatch, bt_lib=_BT, strategies_map={"no-idea": _NoIdeaStrategy()})
    monkeypatch.setattr(_RUN, "l3_strategies", lambda: ["no-idea"])
    monkeypatch.setattr(_RUN, "_default_tickers", lambda: ["BTCUSD"])
    calls: list[tuple] = []
    monkeypatch.setattr(_RUN, "fetch_ohlc", lambda *a, **kw: calls.append(kw) or _make_candles(n=600))

    rc = _run_main(_RUN, monkeypatch)  # no --json -> human mode
    assert rc == 0
    captured = capsys.readouterr()
    assert "nightly fitness_matrix.json not found" in captured.err
    assert "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR" in captured.err  # _HELP_FALLBACK line
    assert "strategy-fitness-heatmap" in captured.out  # human matrix header intact
    assert len(calls) == 2  # the previously-un-surfaced fresh recompute did run

    # Nightly artifacts present with per-combo errors: those surface too.
    monkeypatch.setenv(_RUN.ENV_OUT_DIR, str(tmp_path))
    _write_nightly_artifacts(
        tmp_path,
        matrix=_nightly_matrix_fixture(),
        runs=[_nightly_run_fixture("2026-09-03T00:52:22+00:00")],
    )
    rc = _run_main(_RUN, monkeypatch)
    assert rc == 0
    captured = capsys.readouterr()
    assert "error: no result in latest nightly run" in captured.err
    assert "error: insufficient_data" in captured.err
    assert "source: nightly" in captured.out
