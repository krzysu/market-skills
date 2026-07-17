"""Per-fix test fixture for backtest-engine fill simulator (bead bt-2).

Covers the six acceptance criteria from the bt-2 spec: next-bar-open entry,
stop-first intrabar tie, target hit before stop, slippage applied, fee math,
and the out-of-range skipped case. All tests are deterministic (hand-built
OHLC, fixed defaults) and network-free.
"""

from __future__ import annotations

import importlib.util
import logging
import os

import pytest


def _load_bt_lib():
    """Load skills/backtest-engine/lib.py dynamically (mirror the bt-1 fixture)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "backtest-engine", "lib.py")
    spec = importlib.util.spec_from_file_location("backtest_engine_lib_fill", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BT = _load_bt_lib()
FillSimulator = _BT.FillSimulator


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


class TestFillSimulator:
    def test_next_bar_open_entry(self):
        # Entry fills at the NEXT bar's open (candles[1].open=102) + slippage,
        # not the signal bar's close. Target hits on bar 2 -> "filled".
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),  # signal bar (entry_bar_index=0)
            _candle(86400, 102.0, 105.0, 101.0, 104.0),  # entry bar: open=102, neither stop(95) nor target(110) touched
            _candle(172800, 104.0, 112.0, 103.0, 111.0),  # target=110 touched (high=112), stop not
        ]
        rec = sim.simulate(idea, candles, entry_bar_index=0)
        assert rec["status"] == "filled"
        # 102 * (1 + 2/10000) = 102.0204
        assert rec["entry"]["fill_price"] == pytest.approx(102.0204)
        assert rec["entry"]["status"] == "filled"
        assert rec["entry"]["filled_volume"] == 1.0
        # Exit at target=110 on bar 2.
        assert rec["exit_reason"] == "target"
        assert rec["exit_bar_index"] == 2
        assert rec["exit"]["fill_price"] == pytest.approx(110.0)

    def test_stop_first_on_intrabar_tie(self):
        # Same bar touches BOTH stop and target -> STOP wins (worst case),
        # trade exits at stop_loss, not target.
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),  # signal bar
            _candle(86400, 100.0, 115.0, 90.0, 105.0),  # entry bar: low=90<=stop=95 AND high=115>=target=110
        ]
        rec = sim.simulate(idea, candles, entry_bar_index=0)
        assert rec["status"] == "filled"
        assert rec["exit_reason"] == "stop"
        assert rec["exit_bar_index"] == 1
        # Stop wins the tie: exit at stop=95, NOT target=110.
        assert rec["exit"]["fill_price"] == pytest.approx(95.0)
        assert rec["exit"]["fill_price"] != pytest.approx(110.0)

    def test_target_hit_before_stop_next_bar(self):
        # Target hits on a later bar where the stop is not touched.
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),  # signal bar
            _candle(86400, 100.0, 103.0, 98.0, 102.0),  # entry bar: neither touched
            _candle(172800, 102.0, 111.0, 101.0, 110.0),  # target=110 touched, stop=95 not
        ]
        rec = sim.simulate(idea, candles, entry_bar_index=0)
        assert rec["status"] == "filled"
        assert rec["exit_reason"] == "target"
        assert rec["exit_bar_index"] == 2
        assert rec["exit"]["fill_price"] == pytest.approx(110.0)

    def test_slippage_applied(self):
        # Slippage makes the entry WORSE: longs pay above open, shorts below.
        sim = FillSimulator(slippage_bps=2)
        # Long: entry = open * (1 + 2/10000) -> 100.02 (above open=100).
        long_idea = _idea(direction="long", entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        long_candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 100.0, 105.0, 99.0, 104.0),  # neither stop nor target touched -> open trade
        ]
        rec_long = sim.simulate(long_idea, long_candles, entry_bar_index=0)
        assert rec_long["entry"]["fill_price"] == pytest.approx(100.0 * 1.0002)
        assert rec_long["entry"]["fill_price"] > 100.0
        assert rec_long["entry"]["raw"]["slippage_paid"] == pytest.approx(0.02)

        # Short: entry = open * (1 - 2/10000) -> 99.98 (below open=100).
        short_idea = _idea(direction="short", entry=100.0, stop=105.0, tps=(90.0, 85.0, 80.0))
        short_candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 100.0, 101.0, 95.0, 96.0),  # neither stop(105) nor target(90) touched -> open trade
        ]
        rec_short = sim.simulate(short_idea, short_candles, entry_bar_index=0)
        assert rec_short["entry"]["fill_price"] == pytest.approx(100.0 * 0.9998)
        assert rec_short["entry"]["fill_price"] < 100.0
        assert rec_short["entry"]["raw"]["slippage_paid"] == pytest.approx(0.02)

    def test_fee_math(self):
        # fee = cost_quote * fee_bps / 10_000, cost_quote = filled_volume * fill_price.
        # slippage_bps=0 isolates the fee; qty=2.0; open=100 -> fill_price=100.
        sim = FillSimulator(fee_bps=26, slippage_bps=0)
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 100.0, 105.0, 99.0, 104.0),
        ]
        rec = sim.simulate(idea, candles, entry_bar_index=0, ctx={"qty": 2.0})
        entry = rec["entry"]
        # cost_quote = filled_volume * fill_price = 2.0 * 100.0 = 200.0
        assert entry["cost_quote"] == pytest.approx(200.0)
        # fee = 200.0 * 26 / 10_000 = 0.52
        assert entry["fee"] == pytest.approx(0.52)
        assert entry["raw"]["fee_paid"] == pytest.approx(0.52)
        assert entry["filled_volume"] == 2.0
        assert entry["raw"]["qty"] == 2.0

    def test_entry_next_bar_out_of_range_is_skipped(self):
        # entry_bar_index+1 >= len(candles) -> no next bar -> status="skipped".
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))

        # Only a signal bar: entry_bar_index=0 -> entry_idx=1 >= len=1 -> skipped.
        candles = [_candle(0, 100.0, 101.0, 99.0, 100.0)]
        rec = sim.simulate(idea, candles, entry_bar_index=0)
        assert rec["status"] == "skipped"
        assert rec["exit_reason"] == "none"
        assert rec["exit_bar_index"] is None
        assert rec["entry"]["status"] == "skipped"
        assert rec["entry"]["fill_price"] is None
        assert rec["entry"]["filled_volume"] == 0.0

        # Also: entry_bar_index at the last bar -> entry_idx == len -> skipped.
        candles2 = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 102.0, 105.0, 101.0, 104.0),
        ]
        rec2 = sim.simulate(idea, candles2, entry_bar_index=1)
        assert rec2["status"] == "skipped"

    def test_skipped_emits_debug_log(self, caplog):
        # The skipped path must emit a debug log line (spec requirement).
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [_candle(0, 100.0, 101.0, 99.0, 100.0)]
        caplog.set_level(logging.DEBUG, logger="backtest_engine_lib_fill")
        sim.simulate(idea, candles, entry_bar_index=0)
        assert any("skip" in r.message.lower() for r in caplog.records)

    def test_trade_record_carry_two_fill_confirmations(self):
        # The TradeRecord must carry two real FillConfirmation shapes (entry + exit)
        # so a future orchestrator can run the same post-fill logic on either side.
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 100.0, 112.0, 99.0, 111.0),  # target touched on entry bar
        ]
        rec = sim.simulate(idea, candles, entry_bar_index=0)
        required = (
            "intent_id",
            "order_id",
            "pair",
            "side",
            "order_type",
            "requested_volume",
            "filled_volume",
            "status",
            "timestamp",
            "venue",
        )
        for fill in (rec["entry"], rec["exit"]):
            # Required FillConfirmation fields present.
            for k in required:
                assert k in fill
            assert fill["venue"] == "backtest"
