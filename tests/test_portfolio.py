"""Tests for portfolio/db.py — FIFO, multi-portfolio, P&L, CRUD."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from portfolio.db import (
    add_portfolio,
    add_transaction,
    compute_fifo,
    compute_lots,
    compute_pnl,
    compute_positions,
    delete_portfolio,
    edit_transaction,
    get_cached_prices,
    get_portfolio,
    get_portfolio_summary,
    get_transaction,
    init_db,
    list_portfolios,
    list_transactions,
    reconcile,
    refresh_prices,
    remove_transaction,
    rename_portfolio,
    replay_fifo,
)


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.db")
        init_db(path)
        yield path


T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"
T2 = "2026-01-03T00:00:00Z"


def test_add_and_list_portfolios(db_path):
    pid = add_portfolio(db_path, "spot", "EUR")
    assert pid == 1
    pfs = list_portfolios(db_path)
    assert len(pfs) == 1
    assert pfs[0]["name"] == "spot"


def test_add_duplicate_portfolio_name_fails(db_path):
    add_portfolio(db_path, "spot")
    with pytest.raises(Exception):
        add_portfolio(db_path, "spot")


def test_get_portfolio_by_id_and_name(db_path):
    pid = add_portfolio(db_path, "kraken")
    assert get_portfolio(db_path, pid)["name"] == "kraken"
    assert get_portfolio(db_path, "kraken")["id"] == pid
    assert get_portfolio(db_path, 999) is None
    assert get_portfolio(db_path, "nope") is None


def test_rename_portfolio(db_path):
    pid = add_portfolio(db_path, "old")
    assert rename_portfolio(db_path, pid, "new")
    assert get_portfolio(db_path, pid)["name"] == "new"


def test_delete_portfolio_removes_tx_too(db_path):
    pid = add_portfolio(db_path, "temp")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    delete_portfolio(db_path, pid)
    assert len(list_transactions(db_path, portfolio_id=pid)) == 0
    assert get_portfolio(db_path, pid) is None


# ── transaction CRUD ──────────────────────────────────────────────────


def test_add_buy_transaction(db_path):
    pid = add_portfolio(db_path, "spot")
    txid = add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    assert txid == 1
    tx = get_transaction(db_path, txid)
    assert tx["side"] == "BUY"
    assert tx["asset"] == "kraken:BTCUSD"
    assert tx["qty"] == 1
    assert tx["price"] == 100
    assert tx["cost_quote"] == 100


def test_add_sell_transaction(db_path):
    pid = add_portfolio(db_path, "spot")
    txid = add_transaction(db_path, pid, T0, "SELL", "kraken:HYPEEUR", qty=2.99, price=33.35)
    tx = get_transaction(db_path, txid)
    assert round(tx["cost_quote"], 6) == round(2.99 * 33.35, 6)


def test_add_transaction_invalid_side(db_path):
    pid = add_portfolio(db_path, "spot")
    with pytest.raises(ValueError, match="side"):
        add_transaction(db_path, pid, T0, "WITHDRAW", "kraken:BTCUSD", qty=1, price=100)


def test_add_transaction_zero_qty(db_path):
    pid = add_portfolio(db_path, "spot")
    with pytest.raises(ValueError):
        add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=0, price=100)


def test_list_transactions_filters(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.3, price=150)
    add_transaction(db_path, pid, T2, "BUY", "kraken:HYPEEUR", qty=2, price=33)

    assert len(list_transactions(db_path, portfolio_id=pid)) == 3
    assert len(list_transactions(db_path, portfolio_id=pid, side="BUY")) == 2
    assert len(list_transactions(db_path, portfolio_id=pid, asset="kraken:HYPEEUR")) == 1
    assert len(list_transactions(db_path, portfolio_id=pid, since=T1)) == 2
    assert len(list_transactions(db_path, portfolio_id=pid, limit=1)) == 1


def test_edit_transaction_notes_only(db_path):
    pid = add_portfolio(db_path, "spot")
    txid = add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100, notes="old")
    assert edit_transaction(db_path, txid, "notes", "new note")
    assert get_transaction(db_path, txid)["notes"] == "new note"


def test_edit_transaction_ref_only(db_path):
    pid = add_portfolio(db_path, "spot")
    txid = add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    assert edit_transaction(db_path, txid, "ref", "order-123")
    assert get_transaction(db_path, txid)["ref"] == "order-123"


def test_edit_rejects_price(db_path):
    pid = add_portfolio(db_path, "spot")
    txid = add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    with pytest.raises(ValueError, match="can only edit"):
        edit_transaction(db_path, txid, "price", 200)


def test_remove_transaction(db_path):
    pid = add_portfolio(db_path, "spot")
    txid = add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    assert remove_transaction(db_path, txid)
    assert get_transaction(db_path, txid) is None


# ── FIFO lot tracking ──────────────────────────────────────────────────


def test_fifo_single_buy_no_sell(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    lots = compute_lots(db_path, pid)
    assert len(lots) == 1
    assert lots[0]["asset"] == "kraken:BTCUSD"
    assert lots[0]["qty"] == 1
    assert lots[0]["entry_price"] == 100


def test_fifo_partial_sell_one_lot(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.3, price=150)
    lots = compute_lots(db_path, pid)
    assert len(lots) == 1
    assert round(lots[0]["qty"], 10) == 0.7
    assert lots[0]["entry_price"] == 100


def test_fifo_full_sell(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=1, price=150)
    lots = compute_lots(db_path, pid)
    assert len(lots) == 0


def test_fifo_multi_lot_partial_sell(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "BUY", "kraken:BTCUSD", qty=1, price=200)
    add_transaction(db_path, pid, T2, "SELL", "kraken:BTCUSD", qty=1.5, price=180)
    lots = compute_lots(db_path, pid)
    assert len(lots) == 1
    assert round(lots[0]["qty"], 10) == 0.5
    assert lots[0]["entry_price"] == 200


def test_fifo_sell_more_than_held(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=0.5, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=2, price=150)
    lots = compute_lots(db_path, pid)
    assert len(lots) == 0


def test_fifo_fees_tracked(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100, fee=2)
    lots = compute_lots(db_path, pid)
    assert lots[0]["entry_price"] == 100
    pnl = compute_pnl(db_path, pid)
    assert pnl[0]["total_fees"] == 2


# ── P&L computation ────────────────────────────────────────────────────


def test_pnl_basic_realized(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=1, price=150)
    pnl = compute_pnl(db_path, pid)
    assert pnl[0]["realized_pnl"] == 50
    assert pnl[0]["total_bought_qty"] == 1
    assert pnl[0]["total_sold_qty"] == 1
    assert pnl[0]["remaining_qty"] == 0


def test_pnl_partial_sell_realized(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.5, price=150)
    pnl = compute_pnl(db_path, pid)
    assert pnl[0]["realized_pnl"] == 25
    assert pnl[0]["remaining_qty"] == 0.5


def test_pnl_multi_lot_partial_sell(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "BUY", "kraken:BTCUSD", qty=1, price=200)
    add_transaction(db_path, pid, T2, "SELL", "kraken:BTCUSD", qty=1.5, price=180)

    pnl = compute_pnl(db_path, pid)
    r = pnl[0]["realized_pnl"]
    assert r == 70
    assert pnl[0]["remaining_qty"] == 0.5


def test_pnl_unrealized(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    pnl = compute_pnl(db_path, pid, {"kraken:BTCUSD": 200})
    assert pnl[0]["realized_pnl"] == 0
    assert pnl[0]["unrealized_pnl"] == 100
    assert pnl[0]["total_pnl"] == 100


def test_pnl_both_realized_and_unrealized(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=2, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.5, price=150)
    pnl = compute_pnl(db_path, pid, {"kraken:BTCUSD": 200})
    assert pnl[0]["realized_pnl"] == 25
    assert pnl[0]["unrealized_pnl"] == 150
    assert pnl[0]["total_pnl"] == 175


def test_pnl_multiple_assets(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "BUY", "kraken:HYPEEUR", qty=10, price=33)
    pnl = compute_pnl(db_path, pid)
    assert len(pnl) == 2


# ── multi-portfolio ────────────────────────────────────────────────────


def test_multi_portfolio_isolation(db_path):
    pid1 = add_portfolio(db_path, "spot-a")
    pid2 = add_portfolio(db_path, "spot-b")
    add_transaction(db_path, pid1, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid2, T0, "BUY", "kraken:BTCUSD", qty=2, price=200)
    add_transaction(db_path, pid1, T1, "SELL", "kraken:BTCUSD", qty=0.5, price=150)

    pnl_a = compute_pnl(db_path, pid1)
    pnl_b = compute_pnl(db_path, pid2)
    assert pnl_a[0]["realized_pnl"] == 25
    assert pnl_a[0]["remaining_qty"] == 0.5
    assert pnl_b[0]["realized_pnl"] == 0
    assert pnl_b[0]["remaining_qty"] == 2


# ── summary ─────────────────────────────────────────────────────────────


def test_summary_aggregates_across_portfolios(db_path):
    pid1 = add_portfolio(db_path, "spot-a")
    pid2 = add_portfolio(db_path, "spot-b")
    add_transaction(db_path, pid1, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid2, T0, "BUY", "kraken:BTCUSD", qty=2, price=200)

    summary = get_portfolio_summary(db_path, current_prices={"kraken:BTCUSD": 300})
    bp = {p["name"]: p for p in summary["by_portfolio"]}
    assert bp["spot-a"]["invested"] == 100
    assert bp["spot-a"]["current_value"] == 300
    assert bp["spot-b"]["invested"] == 400
    assert bp["spot-b"]["current_value"] == 600
    assert bp["spot-a"]["realized_pnl"] == 0
    assert bp["spot-b"]["realized_pnl"] == 0
    assert bp["spot-a"]["base_ccy"] == "EUR"
    assert len(summary["by_portfolio"]) == 2


def test_summary_single_portfolio(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    summary = get_portfolio_summary(db_path, pid, {"kraken:BTCUSD": 150})
    bp = summary["by_portfolio"][0]
    assert bp["invested"] == 100
    assert bp["current_value"] == 150
    assert len(summary["pnl"]) == 1


# ── compute_fifo raw ───────────────────────────────────────────────────


def test_raw_fifo_internal_dicts():
    rows = [
        {"portfolio_id": 1, "asset": "BTC", "ts": T0, "side": "BUY", "qty": 1, "price": 100, "fee": 0, "id": 1},
        {"portfolio_id": 1, "asset": "BTC", "ts": T1, "side": "BUY", "qty": 2, "price": 200, "fee": 0, "id": 2},
        {"portfolio_id": 1, "asset": "BTC", "ts": T2, "side": "SELL", "qty": 1.5, "price": 250, "fee": 0, "id": 3},
    ]
    fifo = compute_fifo(rows)
    assert fifo["realized"][(1, "BTC")] == 175
    assert fifo["n_buys"][(1, "BTC")] == 2
    assert fifo["n_sells"][(1, "BTC")] == 1
    assert fifo["cost_of_sold"][(1, "BTC")] == 200
    remaining = sum(lot["qty"] for lot in fifo["open_lots"][(1, "BTC")])
    assert remaining == 1.5


# ── Replay ───────────────────────────────────────────────────────────────


def test_replay_single_buy(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    events = replay_fifo(db_path, pid)
    assert len(events) == 1
    ev = events[0]
    assert ev["side"] == "BUY"
    assert ev["qty"] == 1
    assert ev["remain_qty"] == 1
    assert ev["consumed_lots"] == []
    assert ev["total_realized_pnl"] == 0


def test_replay_buy_then_sell_one_lot(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.3, price=46000)
    events = replay_fifo(db_path, pid)
    assert len(events) == 2
    ev = events[1]
    assert ev["side"] == "SELL"
    assert len(ev["consumed_lots"]) == 1
    assert ev["consumed_lots"][0]["tx_id"] == events[0]["tx_id"]
    assert ev["consumed_lots"][0]["qty_consumed"] == 0.3
    assert ev["consumed_lots"][0]["pnl"] > 0
    assert ev["total_realized_pnl"] == 300


def test_replay_sell_consumes_multiple_lots(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "BUY", "kraken:BTCUSD", qty=2, price=200)
    add_transaction(db_path, pid, T2, "SELL", "kraken:BTCUSD", qty=1.5, price=250)
    events = replay_fifo(db_path, pid)
    assert len(events) == 3
    sell = events[2]
    assert len(sell["consumed_lots"]) == 2
    # Lot 1 (BUY at 100): fully consumed qty=1, P&L = (250-100)*1 = 150
    assert sell["consumed_lots"][0]["tx_id"] == events[0]["tx_id"]
    assert sell["consumed_lots"][0]["qty_consumed"] == 1
    assert sell["consumed_lots"][0]["pnl"] == 150
    # Lot 2 (BUY at 200): consumed qty=0.5, P&L = (250-200)*0.5 = 25
    assert sell["consumed_lots"][1]["tx_id"] == events[1]["tx_id"]
    assert sell["consumed_lots"][1]["qty_consumed"] == 0.5
    assert sell["consumed_lots"][1]["pnl"] == 25
    assert sell["total_realized_pnl"] == 175
    # Lot 1 (BUY at 100): fully consumed -> remain_qty = 0
    assert events[0]["remain_qty"] == 0
    # Lot 2 (BUY at 200): 0.5 consumed out of 2 -> remain_qty = 1.5
    assert events[1]["remain_qty"] == 1.5


def test_replay_remain_qty_after_partial_sell(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.4, price=150)
    events = replay_fifo(db_path, pid)
    # After the sell, 0.6 of the original BUY lot remains
    assert events[0]["side"] == "BUY"
    assert events[0]["remain_qty"] == 0.6


def test_replay_remain_qty_fully_consumed(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=100)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=1, price=150)
    events = replay_fifo(db_path, pid)
    assert events[0]["side"] == "BUY"
    assert events[0]["remain_qty"] == 0


def test_replay_multi_asset_isolation(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    add_transaction(db_path, pid, T1, "BUY", "kraken:HYPEEUR", qty=10, price=33)
    add_transaction(db_path, pid, T2, "SELL", "kraken:BTCUSD", qty=0.5, price=46000)
    events = replay_fifo(db_path, pid)
    assert len(events) == 3
    btc_buy = [e for e in events if e["asset"] == "kraken:BTCUSD" and e["side"] == "BUY"][0]
    hype_buy = [e for e in events if e["asset"] == "kraken:HYPEEUR" and e["side"] == "BUY"][0]
    assert btc_buy["remain_qty"] == 0.5  # 1.0 - 0.5 sold
    assert hype_buy["remain_qty"] == 10  # untouched


# ── Reconcile ────────────────────────────────────────────────────────────


def test_reconcile_exact_match(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    diffs = reconcile(db_path, pid, {"kraken:BTCUSD": 1.0})
    assert len(diffs) == 1
    assert diffs[0]["status"] == "match"
    assert diffs[0]["delta"] == 0


def test_reconcile_diff(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    add_transaction(db_path, pid, T1, "SELL", "kraken:BTCUSD", qty=0.3, price=46000)
    diffs = reconcile(db_path, pid, {"kraken:BTCUSD": 0.5})
    assert len(diffs) == 1
    assert diffs[0]["status"] == "diff"
    assert diffs[0]["computed_qty"] == 0.7
    assert diffs[0]["delta"] == 0.2


def test_reconcile_missing_computed(db_path):
    pid = add_portfolio(db_path, "spot")
    diffs = reconcile(db_path, pid, {"kraken:BTCUSD": 0.15})
    assert len(diffs) == 1
    assert diffs[0]["status"] == "missing_computed"
    assert diffs[0]["computed_qty"] == 0
    assert diffs[0]["external_qty"] == 0.15
    assert diffs[0]["delta"] == -0.15


def test_reconcile_missing_external(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    diffs = reconcile(db_path, pid, {})
    assert len(diffs) == 1
    assert diffs[0]["status"] == "missing_external"
    assert diffs[0]["computed_qty"] == 1
    assert diffs[0]["external_qty"] == 0
    assert diffs[0]["delta"] == 1


def test_reconcile_mixed(db_path):
    pid = add_portfolio(db_path, "spot")
    add_transaction(db_path, pid, T0, "BUY", "kraken:BTCUSD", qty=1, price=45000)
    add_transaction(db_path, pid, T1, "BUY", "kraken:HYPEEUR", qty=10, price=33)
    diffs = reconcile(db_path, pid, {"kraken:BTCUSD": 1.0, "hl:LIT": 5.0})
    diffs_map = {d["asset"]: d for d in diffs}
    assert diffs_map["kraken:BTCUSD"]["status"] == "match"
    assert diffs_map["kraken:HYPEEUR"]["status"] == "missing_external"
    assert diffs_map["hl:LIT"]["status"] == "missing_computed"


# ── refresh_prices — spot price vs stale OHLC close ─────────────────────


def _kraken_ticker_json(pair: str, *, last: float, bid: float, ask: float) -> str:
    """Build a fake ``kraken ticker`` JSON payload for one pair."""
    return json.dumps(
        {
            pair: {
                "a": [f"{ask:.5f}", "1", "1.000"],
                "b": [f"{bid:.5f}", "1", "1.000"],
                "c": [f"{last:.5f}", "0.5"],
                "h": [f"{max(ask, last):.5f}", f"{max(ask, last):.5f}"],
                "l": [f"{min(bid, last):.5f}", f"{min(bid, last):.5f}"],
                "o": f"{last:.5f}",
                "p": [f"{last:.5f}", f"{last:.5f}"],
                "t": [1, 1],
                "v": ["1", "1"],
            }
        }
    )


def _kraken_ohlc_json(pair: str, candles: list[list]) -> str:
    """Build a fake ``kraken ohlc`` JSON payload for one pair."""
    return json.dumps({pair: candles, "last": candles[-1][0] if candles else 0})


def test_refresh_prices_uses_live_spot_not_ohlc_close(db_path, capsys):
    """Bug: refresh_prices used candles[-1][4] (daily close) instead of live spot.

    Reproduction: HYPE live bid=60.10, but the prior daily candle's close was
    62.46 (off by ~4%). Fix routes through ``kraken ticker`` c[0] (last trade)
    so positions reflects the current price.
    """
    pid = add_portfolio(db_path, "spot", "EUR")
    add_transaction(db_path, pid, T0, "BUY", "kraken:HYPEEUR", qty=1.66, price=60.15)

    spot_payload = _kraken_ticker_json("HYPEEUR", last=60.31, bid=60.10, ask=60.14)
    stale_close = 62.46  # the wrong number from the prior daily candle
    ohlc_payload = _kraken_ohlc_json(
        "HYPEEUR",
        [[1769904000, "62.00", "63.00", "61.00", stale_close, "62.20", "100", 5]],
    )

    def fake_run(cmd, *args, **kwargs):
        from subprocess import CompletedProcess

        if cmd[:2] == ["kraken", "ticker"]:
            return CompletedProcess(cmd, 0, stdout=spot_payload, stderr="")
        if cmd[:2] == ["kraken", "ohlc"]:
            return CompletedProcess(cmd, 0, stdout=ohlc_payload, stderr="")
        return CompletedProcess(cmd, 1, stdout="", stderr="unsupported")

    with patch("analysis.providers.kraken.subprocess.run", side_effect=fake_run):
        prices = refresh_prices(db_path)

    assert prices["kraken:HYPEEUR"] == 60.31  # live last, NOT 62.46

    cached = get_cached_prices(db_path)
    assert cached["kraken:HYPEEUR"] == 60.31

    positions = compute_positions(db_path, pid, prices)
    assert positions[0]["current_price"] == 60.31
    assert round(positions[0]["current_value"], 2) == round(1.66 * 60.31, 2)
    assert abs(positions[0]["unrealized_pnl"] - 1.66 * (60.31 - 60.15)) < 0.05

    assert "stale" not in capsys.readouterr().err.lower()


def test_refresh_prices_falls_back_to_ohlc_when_spot_unavailable(db_path, capsys):
    """When ``kraken ticker`` fails, fall back to the latest candle close and warn."""
    pid = add_portfolio(db_path, "spot", "EUR")
    add_transaction(db_path, pid, T0, "BUY", "kraken:HYPEEUR", qty=1, price=60)

    ohlc_payload = _kraken_ohlc_json(
        "HYPEEUR",
        [[1769904000, "62.00", "63.00", "61.00", 62.46, "62.20", "100", 5]],
    )

    def fake_run(cmd, *args, **kwargs):
        from subprocess import CompletedProcess

        if cmd[:2] == ["kraken", "ticker"]:
            return CompletedProcess(cmd, 1, stdout="", stderr="rate limited")
        if cmd[:2] == ["kraken", "ohlc"]:
            return CompletedProcess(cmd, 0, stdout=ohlc_payload, stderr="")
        return CompletedProcess(cmd, 1, stdout="", stderr="unsupported")

    with patch("analysis.providers.kraken.subprocess.run", side_effect=fake_run):
        prices = refresh_prices(db_path)

    assert prices["kraken:HYPEEUR"] == 62.46
    err = capsys.readouterr().err
    assert "stale" in err.lower() or "fell back" in err.lower()
    assert "kraken:HYPEEUR" in err


def test_refresh_prices_tracks_source_in_cache(db_path):
    """Cache ``source`` column distinguishes live spot from OHLC fallback."""
    pid = add_portfolio(db_path, "spot", "EUR")
    add_transaction(db_path, pid, T0, "BUY", "kraken:HYPEEUR", qty=1, price=60)

    spot_payload = _kraken_ticker_json("HYPEEUR", last=60.0, bid=59.9, ask=60.1)
    ohlc_payload = _kraken_ohlc_json(
        "HYPEEUR",
        [[1769904000, "60.00", "61.00", "59.00", 60.5, "60.10", "100", 5]],
    )

    def fake_run(cmd, *args, **kwargs):
        from subprocess import CompletedProcess

        if cmd[:2] == ["kraken", "ticker"]:
            return CompletedProcess(cmd, 0, stdout=spot_payload, stderr="")
        if cmd[:2] == ["kraken", "ohlc"]:
            return CompletedProcess(cmd, 0, stdout=ohlc_payload, stderr="")
        return CompletedProcess(cmd, 1, stdout="", stderr="unsupported")

    with patch("analysis.providers.kraken.subprocess.run", side_effect=fake_run):
        refresh_prices(db_path)

    from portfolio.db import get_db

    conn = get_db(db_path)
    row = conn.execute("SELECT source FROM price_cache WHERE asset = 'kraken:HYPEEUR'").fetchone()
    conn.close()
    assert row["source"] == "kraken:ticker"


def test_refresh_prices_skips_assets_without_provider_prefix(db_path):
    """Assets like ``BTC`` (no ``provider:``) are skipped — manual price only."""
    pid = add_portfolio(db_path, "spot", "EUR")
    add_transaction(db_path, pid, T0, "BUY", "BTC", qty=0.1, price=100_000)
    add_transaction(db_path, pid, T1, "BUY", "kraken:HYPEEUR", qty=1, price=60)

    spot_payload = _kraken_ticker_json("HYPEEUR", last=60.0, bid=59.9, ask=60.1)
    ohlc_payload = _kraken_ohlc_json(
        "HYPEEUR",
        [[1769904000, "60.00", "61.00", "59.00", 60.5, "60.10", "100", 5]],
    )

    def fake_run(cmd, *args, **kwargs):
        from subprocess import CompletedProcess

        if cmd[:2] == ["kraken", "ticker"]:
            return CompletedProcess(cmd, 0, stdout=spot_payload, stderr="")
        if cmd[:2] == ["kraken", "ohlc"]:
            return CompletedProcess(cmd, 0, stdout=ohlc_payload, stderr="")
        return CompletedProcess(cmd, 1, stdout="", stderr="unsupported")

    with patch("analysis.providers.kraken.subprocess.run", side_effect=fake_run):
        prices = refresh_prices(db_path)

    assert "BTC" not in prices
    assert prices["kraken:HYPEEUR"] == 60.0


# ── KrakenProvider.fetch_spot_price ─────────────────────────────────────


class TestKrakenProviderSpotPrice:
    def test_parses_last_trade(self):
        from analysis.providers.kraken import KrakenProvider

        payload = _kraken_ticker_json("HYPEEUR", last=60.31, bid=60.10, ask=60.14)
        with patch(
            "analysis.providers.kraken.subprocess.run",
            return_value=_completed(payload),
        ):
            result = KrakenProvider().fetch_spot_price("HYPEEUR")

        assert result is not None
        assert result["price"] == 60.31
        assert result["last"] == 60.31
        assert result["bid"] == 60.10
        assert result["ask"] == 60.14
        assert result["source"] == "kraken:ticker"

    def test_normalises_dash_and_slash(self):
        from analysis.providers.kraken import KrakenProvider

        payload = _kraken_ticker_json("BTCEUR", last=50000.0, bid=49999.0, ask=50001.0)
        with patch(
            "analysis.providers.kraken.subprocess.run",
            return_value=_completed(payload),
        ) as run:
            KrakenProvider().fetch_spot_price("BTC-EUR")

        cmd = run.call_args[0][0]
        assert cmd == ["kraken", "ticker", "BTCEUR", "-o", "json"]

    def test_falls_back_to_bid_when_last_missing(self):
        from analysis.providers.kraken import KrakenProvider

        payload = json.dumps({"BTCEUR": {"b": ["49999.0", "1", "1.000"], "a": ["50001.0", "1", "1.000"]}})
        with patch(
            "analysis.providers.kraken.subprocess.run",
            return_value=_completed(payload),
        ):
            result = KrakenProvider().fetch_spot_price("BTCEUR")

        assert result["price"] == 49999.0
        assert result["last"] is None
        assert result["bid"] == 49999.0

    def test_returns_none_on_cli_failure(self):
        from subprocess import CompletedProcess

        from analysis.providers.kraken import KrakenProvider

        with patch(
            "analysis.providers.kraken.subprocess.run",
            return_value=CompletedProcess(["kraken"], 1, stdout="", stderr="rate limited"),
        ):
            assert KrakenProvider().fetch_spot_price("HYPEEUR") is None

    def test_returns_none_on_bad_json(self):
        from subprocess import CompletedProcess

        from analysis.providers.kraken import KrakenProvider

        with patch(
            "analysis.providers.kraken.subprocess.run",
            return_value=CompletedProcess(["kraken"], 0, stdout="not json", stderr=""),
        ):
            assert KrakenProvider().fetch_spot_price("HYPEEUR") is None

    def test_returns_none_when_no_price_fields(self):
        from analysis.providers.kraken import KrakenProvider

        payload = json.dumps({"HYPEEUR": {"o": "60.0"}})
        with patch(
            "analysis.providers.kraken.subprocess.run",
            return_value=_completed(payload),
        ):
            assert KrakenProvider().fetch_spot_price("HYPEEUR") is None


def _completed(stdout: str):
    from subprocess import CompletedProcess

    return CompletedProcess(["kraken"], 0, stdout=stdout, stderr="")
