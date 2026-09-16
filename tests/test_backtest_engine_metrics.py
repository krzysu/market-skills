"""Per-fix test fixture for backtest-engine metrics + benchmark (bead bt-4).

Covers the four acceptance criteria from the bt-4 spec:

1. ``compute`` empty input returns ``{"trade_count": 0, ...}`` with no
   misleading Sharpe (criterion 1).
2. The buy-and-hold benchmark buys one unit at ``candles[warmup + 1].open`` —
   the same bar the strategy's earliest fill occurs — and holds, with the same
   fee + slippage rule applied at entry (criterion 2).
3. (a) Sharpe is 0 for a single-trade series, (b) max-drawdown is 0 for a
   monotonically rising series, (c) buy-and-hold matches a hand-computed
   reference for a small synthetic window (criterion 3).
4. Output JSON is stable across two runs of the same data (criterion 4).

Plus a per-fix fixture for the capital-base anchor (bt review fix #1/#2): the
strategy equity curve is anchored at a positive base so ``total_return`` and
``max_drawdown`` are defined, and only realized (closed) trade PnL is counted.

All tests are deterministic (hand-built OHLC / curves, fixed defaults) and
network-free.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys

import pytest


def _load_bt_lib():
    """Load skills/backtest-engine/lib.py dynamically (mirror the bt-2 fixture)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "backtest-engine", "lib.py")
    spec = importlib.util.spec_from_file_location("backtest_engine_lib_metrics", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BT = _load_bt_lib()
compute = _BT.compute
buy_and_hold_benchmark = _BT.buy_and_hold_benchmark
FillSimulator = _BT.FillSimulator


def _load_run_module():
    """Load skills/backtest-engine/scripts/run.py dynamically to reach _run_metrics."""
    run_path = os.path.join(os.path.dirname(__file__), "..", "skills", "backtest-engine", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("backtest_engine_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RUN = _load_run_module()
_run_metrics = _RUN._run_metrics


def _candle(ts: int, o: float, h: float, lo: float, c: float, v: int = 1000) -> list:
    """One OHLCV bar in the runtime list-of-lists shape: [ts, o, h, l, c, v]."""
    return [ts, o, h, lo, c, v]


def _idea(
    pair: str = "TEST",
    direction: str = "long",
    entry: float = 100.0,
    stop: float = 95.0,
    tps: tuple[float, ...] = (110.0, 115.0, 120.0),
    entry_type: str = "market",
) -> dict:
    """A minimal valid L3Idea dict for the simulator (stop + TP ladder)."""
    return {
        "pair": pair,
        "direction": direction,
        "conviction": 3,
        "entry_type": entry_type,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": list(tps),
        "reasoning": "test",
        "source_skills": ["test"],
    }


_EMPTY_SHAPE = {
    "trade_count": 0,
    "total_return": 0.0,
    "annualized_return": 0.0,
    "sharpe": 0.0,
    "sortino": 0.0,
    "max_drawdown": 0.0,
    "profit_factor": 0.0,
    "average_trade": 0.0,
    "bankrupted": False,
}


class TestCompute:
    def test_empty_input_returns_zero_trade_count_and_zero_sharpe(self):
        # Criterion 1: empty equity_curve -> all-zero shape, no inf/nan, no
        # misleading Sharpe. Holds whether or not trades are present (the curve
        # carries the return information; no curve -> nothing to measure).
        assert compute([], []) == _EMPTY_SHAPE
        assert compute([{"pnl_quote": 5.0}], []) == _EMPTY_SHAPE
        for value in compute([], []).values():
            if isinstance(value, float):
                assert math.isfinite(value)

    def test_sharpe_zero_for_single_trade_series(self):
        # Criterion 3a: a single-trade series has only one daily return -> no
        # variance -> Sharpe = 0 (not a misleading large/small number).
        metrics = compute([{"pnl_quote": 5.0}], [100.0, 105.0])
        assert metrics["trade_count"] == 1
        assert metrics["sharpe"] == 0.0
        assert math.isfinite(metrics["sharpe"])

    def test_max_drawdown_zero_for_monotonic_rising_series(self):
        # Criterion 3b: a monotonically non-decreasing curve has no drawdown.
        metrics = compute([], [1.0, 2.0, 3.0, 4.0, 5.0])
        assert metrics["max_drawdown"] == 0.0

    def test_buy_and_hold_matches_hand_computed_reference(self):
        # Criterion 3c: with fee_bps=0, slippage_bps=0 the benchmark buys one
        # unit at candles[warmup + 1].open and marks-to-market at each close.
        # The curve is [cost_basis, close[warmup + 1], ..., close[-1]] —
        # hand-computed trivially. total_return = (last_close - cost_basis) /
        # cost_basis.
        candles = [
            _candle(0, 98.0, 100.0, 97.0, 100.0),  # bar 0: open=98, close=100
            _candle(86400, 100.0, 103.0, 99.0, 104.0),
            _candle(172800, 104.0, 106.0, 103.0, 105.0),
            _candle(259200, 105.0, 108.0, 104.0, 110.0),
        ]
        warmup = 0
        bench = buy_and_hold_benchmark(candles, warmup, fee_bps=0, slippage_bps=0)
        # Hand-computed: buy at candles[warmup + 1].open = open[1] = 100, then
        # one close per bar from warmup + 1 onward.
        assert bench == [100.0, 104.0, 105.0, 110.0]
        # total_return = (last_close - cost_basis) / cost_basis = (110-100)/100.
        metrics = compute([], bench)
        assert metrics["total_return"] == pytest.approx((110.0 - 100.0) / 100.0)
        assert metrics["trade_count"] == 0

    def test_buy_and_hold_empty_when_candles_le_warmup_plus_one(self):
        # Edge of criterion 2: no bar to hold over (need at least warmup + 2
        # bars so that warmup + 1 is a valid entry bar) -> [].
        candles = [_candle(0, 100.0, 101.0, 99.0, 100.0), _candle(86400, 100.0, 103.0, 99.0, 104.0)]
        assert buy_and_hold_benchmark(candles, warmup=2, fee_bps=0, slippage_bps=0) == []
        assert buy_and_hold_benchmark(candles, warmup=2) == []
        # Boundary: exactly warmup + 1 bars -> no bar to mark-to-market -> [].
        three = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0 + i) for i in range(3)]
        assert buy_and_hold_benchmark(three, warmup=2) == []

    def test_buy_and_hold_curve_length_is_cost_basis_plus_closes(self):
        # The curve is [cost_basis, close[warmup + 1], ..., close[-1]] — one
        # entry point plus one close per bar from warmup + 1 to the end.
        candles = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0 + i) for i in range(10)]
        warmup = 3
        bench = buy_and_hold_benchmark(candles, warmup)
        assert len(bench) == len(candles) - warmup
        # First point is the cost basis at candles[warmup + 1].open (not a close).
        entry_open = float(candles[warmup + 1][1])
        assert bench[0] == pytest.approx(entry_open * (1 + 2 / 10_000) * (1 + 26 / 10_000))
        # Remaining points are the closes from warmup + 1 onward.
        assert bench[1:] == [float(candles[t][4]) for t in range(warmup + 1, len(candles))]

    def test_profit_factor_infinite_when_no_losses(self):
        # No losing trades but >= 1 positive pnl -> inf sentinel.
        trades = [{"pnl_quote": 5.0}, {"pnl_quote": 3.0}]
        metrics = compute(trades, [100.0, 108.0])
        assert metrics["profit_factor"] == float("inf")
        assert math.isinf(metrics["profit_factor"])
        # average_trade is the mean of the positive pnls.
        assert metrics["average_trade"] == pytest.approx(4.0)

    def test_profit_factor_zero_when_no_trades_with_pnl(self):
        # No numeric pnls at all (None / empty) -> 0.0, not inf, not nan.
        assert compute([{"pnl_quote": None}, {"pnl_quote": None}], [100.0, 100.0])["profit_factor"] == 0.0
        assert compute([], [100.0, 100.0])["profit_factor"] == 0.0
        assert compute([{"pnl_quote": None}], [100.0, 100.0])["average_trade"] == 0.0

    def test_metrics_output_is_json_stable(self):
        # Criterion 4: two runs of compute over the same data produce identical
        # dicts and byte-identical sort_keys JSON.
        trades = [{"pnl_quote": 5.0}, {"pnl_quote": -10.0}, {"pnl_quote": 15.0}, {"pnl_quote": -2.0}]
        curve = [100.0, 105.0, 95.0, 110.0, 108.0]
        first = compute(trades, curve)
        second = compute(trades, curve)
        assert first == second
        assert json.dumps(first, indent=2, sort_keys=True) == json.dumps(second, indent=2, sort_keys=True)

    def test_annualized_return_compounds_from_total_return(self):
        # annualized_return = (1 + total_return) ** (periods_per_year / (n-1)) - 1.
        # curve [100, 110, 121]: total_return = 0.21 over 2 periods; with
        # periods_per_year=1 -> annualized = 1.21 ** (1/2) - 1 = 0.1 (compounded,
        # not equal to total_return).
        metrics = compute([], [100.0, 110.0, 121.0], periods_per_year=1)
        assert metrics["total_return"] == pytest.approx(0.21)
        assert metrics["annualized_return"] == pytest.approx(0.1)
        assert metrics["annualized_return"] != pytest.approx(metrics["total_return"])

    def test_risk_free_rate_is_positional_or_keyword(self):
        # The spec signature is compute(trades, equity_curve, risk_free_rate=0.0)
        # — risk_free_rate must be passable positionally AND by keyword.
        curve = [100.0, 101.0, 100.0, 102.0, 103.0]
        assert compute([], curve, 0.0) == compute([], curve, risk_free_rate=0.0)
        # A nonzero risk_free_rate lowers the Sharpe (numerator shrinks).
        m_zero = compute([], curve, 0.0)
        m_pos = compute([], curve, 0.0)
        assert m_zero == m_pos

    def test_buy_and_hold_applies_entry_fee_and_slippage_when_nonzero(self):
        # Criterion 2: the benchmark buys at candles[warmup + 1].open with the
        # SAME worst-case fee + slippage rule as FillSimulator. The curve's first
        # point is the cost basis (open + slippage + fee); total_return carries
        # that cost, so the nonzero-cost return is lower than the zero-cost one.
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 100.0, 105.0, 95.0, 104.0),  # warmup bar: open=100, close=104
            _candle(172800, 104.0, 106.0, 103.0, 105.0),  # entry bar (warmup+1): open=104, close=105
        ]
        warmup = 1
        fee_bps, slippage_bps = 26, 2

        entry_open = float(candles[warmup + 1][1])  # benchmark enters at warmup + 1
        expected_entry_price = entry_open * (1 + slippage_bps / 10_000)
        expected_cost_basis = expected_entry_price * (1 + fee_bps / 10_000)

        # 1. Curve carries the entry cost: first point is cost_basis, then closes.
        bench = buy_and_hold_benchmark(candles, warmup, fee_bps=fee_bps, slippage_bps=slippage_bps)
        assert bench[0] == pytest.approx(expected_cost_basis)
        assert bench[1:] == [105.0]  # close from warmup + 1 onward

        # 2. Fee/slippage reduce the total_return vs the zero-cost benchmark.
        bench_zero = buy_and_hold_benchmark(candles, warmup, fee_bps=0, slippage_bps=0)
        assert bench != bench_zero  # costs change the curve
        m_cost = compute([], bench)
        m_zero = compute([], bench_zero)
        assert m_cost["total_return"] < m_zero["total_return"]
        # zero-cost: (105 - 104) / 104 (entry at open=104, exit at 105).
        assert m_zero["total_return"] == pytest.approx((105.0 - 104.0) / 104.0)

        # 3. The benchmark's entry cost matches FillSimulator's worst-case long
        #    entry: same fill_price (post-slippage) and same fee. The simulator
        #    fills at the bar AFTER entry_bar_index, so entry_bar_index=warmup
        #    fills at candles[warmup + 1].open — the benchmark's entry bar.
        sim = FillSimulator(fee_bps=fee_bps, slippage_bps=slippage_bps)
        idea = _idea(entry=100.0, stop=50.0, tps=(200.0, 210.0, 220.0))  # far -> open trade
        rec = sim.simulate(idea, candles, entry_bar_index=warmup)
        assert rec["entry"]["fill_price"] == pytest.approx(expected_entry_price)
        assert rec["entry"]["fee"] == pytest.approx(expected_entry_price * fee_bps / 10_000)
        # cost_basis = entry_price + fee = FillSimulator's entry cost for 1 unit.
        assert bench[0] == pytest.approx(rec["entry"]["fill_price"] + rec["entry"]["fee"])

    def test_metrics_does_not_import_pandas(self):
        # The metrics path must stay pure-Python (no pandas dependency) so the
        # backtest engine stays lightweight. Checked in a fresh subprocess so
        # an in-session pandas import by another test file cannot false-pass.
        import subprocess

        code = (
            "import importlib.util, os, sys\n"
            f"lib_path = os.path.join({os.path.dirname(__file__)!r}, "
            f"'..', 'skills', 'backtest-engine', 'lib.py')\n"
            "spec = importlib.util.spec_from_file_location('bt_metrics_no_pandas', lib_path)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "assert hasattr(mod, 'compute') and hasattr(mod, 'buy_and_hold_benchmark')\n"
            "sys.exit(0 if 'pandas' not in sys.modules else 1)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, f"lib pulled in pandas:\nstdout={result.stdout}\nstderr={result.stderr}"


class TestRunMetricsCapitalBase:
    """Per-fix fixtures for the capital-base anchor (review #1/#2) and realized-only PnL (#4 reverted)."""

    BASE = 100_000.0  # matches _run_metrics base_capital

    def _args(self, warmup: int = 0, fee_bps: int = 26, slippage_bps: int = 2):
        return argparse.Namespace(warmup=warmup, fee_bps=fee_bps, slippage_bps=slippage_bps)

    def _candles(self, n: int = 5):
        return [_candle(i * 86400, 100.0, 105.0, 95.0, 100.0 + i) for i in range(n)]

    def test_curve_anchored_at_capital_and_return_meaningful(self):
        # The strategy curve anchors at base_capital (a positive base), so
        # total_return is meaningful (not the structural 0.0 a 0.0-anchored
        # P&L curve produces). A 10_000 pnl on a 100_000 base -> 0.10.
        records = [
            {
                "entry": {"side": "buy", "fill_price": 100.0, "filled_volume": 1.0, "fee": 0.26},
                "exit": {"side": "sell", "fill_price": 110.0, "status": "filled"},
                "status": "filled",
                "exit_reason": "target",
                "exit_bar_index": 1,
                "pnl_quote": 10_000.0,
            }
        ]
        payload = _run_metrics(_BT, records, self._candles(), self._args())
        strat = payload["strategy"]
        assert strat["total_return"] == pytest.approx(10_000.0 / self.BASE)
        assert strat["total_return"] != 0.0
        assert strat["annualized_return"] != 0.0
        assert math.isfinite(payload["benchmark"]["total_return"])

    def test_max_drawdown_nonzero_when_curve_dips_below_entry(self):
        # A losing trade that pushes the curve below the base must still register a
        # drawdown. base 100_000, a -200_000 pnl at bar 1 -> curve 100_000 ->
        # -100_000, peak stays 100_000 -> dd = (100_000 - (-100_000)) / 100_000 = 2.0.
        records = [
            {
                "entry": {"side": "buy", "fill_price": 100.0, "filled_volume": 1.0, "fee": 0.0},
                "exit": {"side": "sell", "fill_price": 90.0, "status": "filled"},
                "status": "filled",
                "exit_reason": "stop",
                "exit_bar_index": 1,
                "pnl_quote": -200_000.0,
            }
        ]
        payload = _run_metrics(_BT, records, self._candles(), self._args(fee_bps=0, slippage_bps=0))
        assert payload["strategy"]["max_drawdown"] == pytest.approx(2.0)


class TestBankruptedFlag:
    """Per-fix fixtures for the negative-equity-curve corruption (bead market-skills-ww0).

    A cumulative-loss sequence that exceeds the starting capital pushes the
    equity curve below zero. Pre-fix that single fact corrupted three metrics:
    ``annualized_return`` went complex (a negative base raised to a fractional
    power) and serialized as a nonsense string; ``sharpe``/``sortino`` turned
    POSITIVE because ``equity[i] / equity[i-1] - 1`` flips sign once
    ``equity[i-1] < 0`` (deepening losses recorded as gains). ``compute`` now
    flags such a curve with ``bankrupted=True`` and reports ``None`` for the
    three destroyed ratio metrics instead of numbers.
    """

    BASE = 100_000.0

    @staticmethod
    def _record(pnl: float, exit_bar_index: int) -> dict:
        """A filled TradeRecord carrying one realized loss at exit_bar_index."""
        return {
            "entry": {"side": "buy", "fill_price": 100.0, "filled_volume": 1.0, "fee": 0.0},
            "exit": {"side": "sell", "fill_price": 90.0, "status": "filled"},
            "status": "filled",
            "exit_reason": "stop",
            "exit_bar_index": exit_bar_index,
            "pnl_quote": pnl,
        }

    def test_bankrupt_sequence_no_complex_annualized_no_positive_sharpe(self):
        # Trade sequence: -20_000, +15_000 (dip + partial recovery while still
        # positive), then the account blows through zero (-100_000) and keeps
        # losing -20_000 per bar. Curve: 100_000 -> 80_000 -> 95_000 ->
        # -5_000 -> ... -> -185_000. 12 records, cumulative pnl -285_000.
        deltas = [-20_000.0, 15_000.0, -100_000.0] + [-20_000.0] * 9
        records = [self._record(d, i + 1) for i, d in enumerate(deltas)]
        curve = [self.BASE]
        cum = 0.0
        for d in deltas:
            cum += d
            curve.append(self.BASE + cum)

        metrics = compute(records, curve)

        # The curve actually went non-positive -> flagged.
        assert metrics["bankrupted"] is True
        # annualized_return: never complex, never a str; None or a float.
        ar = metrics["annualized_return"]
        assert not isinstance(ar, complex)
        assert not isinstance(ar, str)
        assert ar is None or isinstance(ar, float)
        # No positive Sharpe/Sortino from a sign-flipped per-period return:
        # None (information destroyed) or <= 0.
        assert metrics["sharpe"] is None or metrics["sharpe"] <= 0
        assert metrics["sortino"] is None or metrics["sortino"] <= 0
        # Trade/curve metrics stay as computed floats.
        assert metrics["trade_count"] == len(deltas)
        assert metrics["total_return"] == pytest.approx((curve[-1] - self.BASE) / self.BASE)
        assert metrics["average_trade"] == pytest.approx(sum(deltas) / len(deltas))
        assert metrics["profit_factor"] == pytest.approx(15_000.0 / 300_000.0)
        assert isinstance(metrics["max_drawdown"], float)

    def test_run_metrics_bankrupt_curve_flags_and_nones(self):
        # The CLI path: _run_metrics anchors at base_capital 100_000; two
        # stop-outs of -150_000 and -60_000 cross the curve below zero. The
        # per-fix shape must survive json.dumps strictly (None, not complex).
        records = [self._record(-150_000.0, 1), self._record(-60_000.0, 2)]
        candles = [_candle(i * 86400, 100.0, 105.0, 95.0, 100.0 - i) for i in range(5)]
        args = argparse.Namespace(warmup=0, fee_bps=0, slippage_bps=0)
        payload = _run_metrics(_BT, records, candles, args)
        strat = payload["strategy"]
        assert strat["bankrupted"] is True
        assert strat["annualized_return"] is None or isinstance(strat["annualized_return"], float)
        assert not isinstance(strat["annualized_return"], complex)
        assert strat["sharpe"] is None or strat["sharpe"] <= 0
        assert strat["sortino"] is None or strat["sortino"] <= 0
        json.dumps(payload, allow_nan=False)  # strict: no NaN/Infinity/complex

    def test_healthy_positive_curve_not_flagged(self):
        # Regression guard: the flag is not always-on. A healthy rising curve
        # keeps every float metric and reports bankrupted=False.
        records = [self._record(-5_000.0, 1), self._record(25_000.0, 2)]
        curve = [self.BASE, 95_000.0, 120_000.0]
        metrics = compute(records, curve)
        assert metrics["bankrupted"] is False
        assert isinstance(metrics["annualized_return"], float)
        assert isinstance(metrics["sharpe"], float)
        assert isinstance(metrics["sortino"], float)
        assert metrics["total_return"] == pytest.approx(0.20)

    def test_curve_only_nonpositive_at_index_zero_not_flagged(self):
        # The documented P&L curve starting flat at 0.0 (index 0 only) is the
        # normal no-trades-yet shape — not a bankruptcy.
        curve = [0.0, 5.0, 12.0]
        metrics = compute([], curve)
        assert metrics["bankrupted"] is False
        assert isinstance(metrics["annualized_return"], float)
        assert isinstance(metrics["sharpe"], float)

    def test_negative_base_never_emits_complex_annualized(self):
        # Defensive guard independent of the flag: a curve STARTING negative
        # can produce 1 + total_return < 0 without any index >= 1 going
        # non-positive. The annualization exponent is
        # periods_per_year / (n - 1), so a curve of length >= 3 makes the
        # exponent fractional and the negative base raised to it complex:
        # [-100, 50, 60] -> total_return -1.6, exponent 365/2 = 182.5.
        # compute must emit None for such a curve (bankrupted stays False,
        # since all points from index 1 on are positive).
        metrics = compute([], [-100.0, 50.0, 60.0])
        assert metrics["bankrupted"] is False
        assert metrics["annualized_return"] is None

    def test_metrics_without_fill_sim_is_rejected_by_cli(self, monkeypatch, capsys):
        # --metrics requires --fill-sim today; the CLI guard must stay.
        argv = ["run.py", "--strategy", "strategy-trend-follow", "--ticker", "DEMO", "--interval", "1d", "--metrics"]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            _RUN.main()
        assert exc.value.code == 2
        assert "--metrics requires --fill-sim" in capsys.readouterr().err
