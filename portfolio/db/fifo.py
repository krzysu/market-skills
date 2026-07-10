"""FIFO cost-basis engine and open-lot computation."""

import sqlite3
from collections import defaultdict, deque

from portfolio.db.schema import get_db


def _fetch_transactions_sorted(conn: sqlite3.Connection, portfolio_id: int | None = None) -> list:
    if portfolio_id is not None:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE portfolio_id = ? ORDER BY ts ASC, id ASC", (portfolio_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions ORDER BY ts ASC, id ASC").fetchall()
    return rows


def compute_fifo(tx_rows) -> dict:
    open_lots: dict[tuple, deque] = defaultdict(deque)
    realized: dict[tuple, float] = defaultdict(float)
    cost_of_sold: dict[tuple, float] = defaultdict(float)
    n_buys: dict[tuple, int] = defaultdict(int)
    n_sells: dict[tuple, int] = defaultdict(int)
    total_bought_qty: dict[tuple, float] = defaultdict(float)
    total_sold_qty: dict[tuple, float] = defaultdict(float)
    total_invested: dict[tuple, float] = defaultdict(float)
    total_proceeds: dict[tuple, float] = defaultdict(float)
    total_fees: dict[tuple, float] = defaultdict(float)

    for tx in tx_rows:
        pid = tx["portfolio_id"]
        asset = tx["asset"]
        key = (pid, asset)
        side = tx["side"]
        qty = tx["qty"] or 0
        price = tx["price"] or 0
        fee = tx["fee"] or 0

        total_fees[key] += fee

        if side == "BUY":
            cost = qty * price
            open_lots[key].append({"qty": qty, "price": price, "ts": tx["ts"], "id": tx["id"]})
            n_buys[key] += 1
            total_bought_qty[key] += qty
            total_invested[key] += cost

        elif side == "SELL":
            proceeds = qty * price
            remaining = qty
            n_sells[key] += 1
            total_sold_qty[key] += qty
            total_proceeds[key] += proceeds

            while remaining > 1e-12 and open_lots[key]:
                lot = open_lots[key][0]
                consumed = min(lot["qty"], remaining)
                realized[key] += consumed * (price - lot["price"])
                cost_of_sold[key] += consumed * lot["price"]
                lot["qty"] -= consumed
                remaining -= consumed
                if lot["qty"] < 1e-12:
                    open_lots[key].popleft()

    return {
        "open_lots": open_lots,
        "realized": realized,
        "cost_of_sold": cost_of_sold,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "total_bought_qty": total_bought_qty,
        "total_sold_qty": total_sold_qty,
        "total_invested": total_invested,
        "total_proceeds": total_proceeds,
        "total_fees": total_fees,
    }


def compute_lots(db_path: str, portfolio_id: int | None = None, asset_filter: str | None = None) -> list[dict]:
    conn = get_db(db_path)
    rows = _fetch_transactions_sorted(conn, portfolio_id)
    conn.close()
    fifo = compute_fifo(rows)
    result = []
    for (pid, asset), lots in sorted(fifo["open_lots"].items()):
        if asset_filter and asset != asset_filter:
            continue
        for lot in lots:
            if lot["qty"] > 1e-12:
                result.append(
                    {
                        "portfolio_id": pid,
                        "asset": asset,
                        "entry_price": lot["price"],
                        "entry_ts": lot["ts"],
                        "qty": round(lot["qty"], 10),
                        "tx_id": lot["id"],
                    }
                )
    return result
