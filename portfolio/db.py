"""Portfolio tracking — SQLite-backed, multi-portfolio, FIFO cost basis.

All functions take a ``db_path`` as the first argument. The caller manages
the database file location. Tests use ``:memory:`` or a temp file.
"""

import sqlite3
from collections import defaultdict, deque
from datetime import UTC, datetime

VALID_SIDES = ("BUY", "SELL")


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            base_ccy TEXT NOT NULL DEFAULT 'EUR',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            ts TEXT NOT NULL,
            side TEXT NOT NULL,
            asset TEXT NOT NULL,
            qty REAL NOT NULL,
            price REAL,
            cost_quote REAL,
            fee REAL DEFAULT 0,
            tx_hash TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            ref TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(portfolio_id, ts, tx_hash, side, asset)
        );
        CREATE INDEX IF NOT EXISTS idx_tx_portfolio ON transactions(portfolio_id);
        CREATE INDEX IF NOT EXISTS idx_tx_asset ON transactions(asset);
        CREATE INDEX IF NOT EXISTS idx_tx_ts ON transactions(ts);
        CREATE INDEX IF NOT EXISTS idx_tx_side ON transactions(side);
        CREATE TABLE IF NOT EXISTS price_cache (
            asset TEXT PRIMARY KEY,
            price REAL NOT NULL,
            ts TEXT NOT NULL,
            source TEXT NOT NULL
        );
    """
    )
    conn.commit()
    conn.close()


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Portfolio CRUD ──────────────────────────────────────────────────────


def add_portfolio(db_path: str, name: str, base_ccy: str = "EUR", notes: str | None = None) -> int:
    conn = get_db(db_path)
    cur = conn.execute(
        "INSERT INTO portfolios (name, base_ccy, notes) VALUES (?, ?, ?)",
        (name, base_ccy.upper(), notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_portfolios(db_path: str) -> list[dict]:
    conn = get_db(db_path)
    rows = conn.execute("SELECT * FROM portfolios ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio(db_path: str, id_or_name: int | str) -> dict | None:
    conn = get_db(db_path)
    if isinstance(id_or_name, int):
        row = conn.execute("SELECT * FROM portfolios WHERE id = ?", (id_or_name,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM portfolios WHERE name = ?", (id_or_name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def rename_portfolio(db_path: str, portfolio_id: int, new_name: str) -> bool:
    conn = get_db(db_path)
    conn.execute("UPDATE portfolios SET name = ? WHERE id = ?", (new_name, portfolio_id))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


def delete_portfolio(db_path: str, portfolio_id: int) -> bool:
    conn = get_db(db_path)
    conn.execute("DELETE FROM transactions WHERE portfolio_id = ?", (portfolio_id,))
    conn.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


# ── Transaction CRUD ───────────────────────────────────────────────────


def add_transaction(  # noqa: PLR0913
    db_path: str,
    portfolio_id: int,
    ts: str,
    side: str,
    asset: str,
    qty: float,
    price: float | None = None,
    cost_quote: float | None = None,
    fee: float = 0,
    tx_hash: str | None = None,
    source: str = "manual",
    ref: str | None = None,
    notes: str | None = None,
) -> int:
    side = side.upper()
    if side not in VALID_SIDES:
        raise ValueError(f"side must be one of {VALID_SIDES}, got '{side}'")

    if qty <= 0:
        raise ValueError("qty must be positive")

    if cost_quote is None and price is not None:
        cost_quote = round(qty * price, 8)
    elif cost_quote is None:
        cost_quote = 0

    conn = get_db(db_path)
    cur = conn.execute(
        """INSERT INTO transactions
           (portfolio_id, ts, side, asset, qty, price, cost_quote, fee, tx_hash, source, ref, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (portfolio_id, ts, side, asset, qty, price, cost_quote, fee, tx_hash, source, ref, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def edit_transaction(db_path: str, tx_id: int, field: str, value) -> bool:
    """Edit only ``notes`` or ``ref`` fields. For everything else, remove + re-add."""
    if field not in ("notes", "ref"):
        raise ValueError(f"can only edit 'notes' or 'ref', not '{field}'. Remove + re-add to change other fields.")

    conn = get_db(db_path)
    conn.execute(f"UPDATE transactions SET {field} = ? WHERE id = ?", (value, tx_id))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


def remove_transaction(db_path: str, tx_id: int) -> bool:
    conn = get_db(db_path)
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


def list_transactions(
    db_path: str,
    portfolio_id: int | None = None,
    asset: str | None = None,
    side: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    conn = get_db(db_path)
    where: list[str] = []
    params: list = []
    if portfolio_id is not None:
        where.append("portfolio_id = ?")
        params.append(portfolio_id)
    if asset is not None:
        where.append("asset = ?")
        params.append(asset)
    if side is not None:
        where.append("side = ?")
        params.append(side.upper())
    if since is not None:
        where.append("ts >= ?")
        params.append(since)

    sql = "SELECT * FROM transactions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts ASC, id ASC"
    if limit is not None:
        sql += f" LIMIT {limit}"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transaction(db_path: str, tx_id: int) -> dict | None:
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── FIFO computation ─────────────────────────────────────────────────────


def _fetch_transactions_sorted(conn: sqlite3.Connection, portfolio_id: int | None = None) -> list:
    if portfolio_id is not None:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE portfolio_id = ? ORDER BY ts ASC, id ASC", (portfolio_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions ORDER BY ts ASC, id ASC").fetchall()
    return rows


def compute_fifo(tx_rows) -> dict:
    """Compute FIFO lots and realized P&L from sorted transaction rows.

    Returns:
        ``{"open_lots": {(pid, asset): deque([{qty, price, ts, id}, ...])},
           "realized": {(pid, asset): float},
           "cost_of_sold": {(pid, asset): float}, ...}``
    """
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


# ── Views ────────────────────────────────────────────────────────────────


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


def compute_positions(
    db_path: str, portfolio_id: int | None = None, current_prices: dict[str, float] | None = None
) -> list[dict]:
    lots = compute_lots(db_path, portfolio_id)
    if current_prices is None:
        current_prices = {}

    grouped: dict[tuple, dict] = {}
    for lot in lots:
        key = (lot["portfolio_id"], lot["asset"])
        if key not in grouped:
            grouped[key] = {"qty": 0, "cost_basis": 0, "portfolio_id": lot["portfolio_id"], "asset": lot["asset"]}
        grouped[key]["qty"] += lot["qty"]
        grouped[key]["cost_basis"] += lot["qty"] * lot["entry_price"]

    result = []
    for (pid, asset), g in sorted(grouped.items()):
        qty = g["qty"]
        cost_basis = g["cost_basis"]
        avg_cost = cost_basis / qty if qty > 1e-12 else 0
        cur_price = current_prices.get(asset)
        cur_value = round(qty * cur_price, 2) if cur_price is not None else None
        unrealized = round(cur_value - cost_basis, 2) if cur_value is not None else None
        if unrealized is not None and cost_basis > 0:
            unrealized_pct: float | None = round((unrealized / cost_basis) * 100, 2)
        else:
            unrealized_pct = None

        result.append(
            {
                "portfolio_id": pid,
                "asset": asset,
                "qty": round(qty, 10),
                "avg_cost": round(avg_cost, 6),
                "cost_basis": round(cost_basis, 2),
                "current_price": cur_price,
                "current_value": cur_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
            }
        )
    return result


def compute_pnl(
    db_path: str, portfolio_id: int | None = None, current_prices: dict[str, float] | None = None
) -> list[dict]:
    conn = get_db(db_path)
    rows = _fetch_transactions_sorted(conn, portfolio_id)

    portfolio_names = {}
    for p in conn.execute("SELECT id, name FROM portfolios").fetchall():
        portfolio_names[p["id"]] = p["name"]

    conn.close()
    fifo = compute_fifo(rows)

    if current_prices is None:
        current_prices = {}

    positions = compute_positions(db_path, portfolio_id, current_prices)
    pos_map: dict[tuple, dict] = {}
    for p in positions:
        pos_map[(p["portfolio_id"], p["asset"])] = p

    all_keys: set[tuple] = set(fifo["n_buys"].keys()) | set(fifo["n_sells"].keys()) | set(fifo["open_lots"].keys())

    result = []
    for (pid, asset) in sorted(all_keys):
        r = fifo["realized"].get((pid, asset), 0)
        cos = fifo["cost_of_sold"].get((pid, asset), 0)
        invested = fifo["total_invested"].get((pid, asset), 0)
        proceeds = fifo["total_proceeds"].get((pid, asset), 0)
        bought_qty = fifo["total_bought_qty"].get((pid, asset), 0)
        sold_qty = fifo["total_sold_qty"].get((pid, asset), 0)
        fees = fifo["total_fees"].get((pid, asset), 0)
        realized_pct = round((r / cos) * 100, 2) if cos > 0 else 0

        pos = pos_map.get((pid, asset), {})
        unrealized = pos.get("unrealized_pnl")
        current_price = pos.get("current_price")
        current_value = pos.get("current_value")
        avg_entry = pos.get("avg_cost")
        remaining_qty = pos.get("qty", 0)

        total_pnl = None
        if unrealized is not None:
            total_pnl = round(r + unrealized, 2)

        result.append(
            {
                "portfolio_id": pid,
                "portfolio_name": portfolio_names.get(pid, "?"),
                "asset": asset,
                "buys": fifo["n_buys"].get((pid, asset), 0),
                "sells": fifo["n_sells"].get((pid, asset), 0),
                "total_bought_qty": round(bought_qty, 10),
                "total_sold_qty": round(sold_qty, 10),
                "total_invested": round(invested, 2),
                "total_proceeds": round(proceeds, 2),
                "total_fees": round(fees, 2),
                "realized_pnl": round(r, 2),
                "realized_pnl_pct": realized_pct,
                "remaining_qty": remaining_qty,
                "avg_entry_price": avg_entry,
                "current_price": current_price,
                "current_value": current_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
                "total_pnl": total_pnl,
            }
        )
    return result


def get_portfolio_summary(
    db_path: str, portfolio_id: int | None = None, current_prices: dict[str, float] | None = None
) -> dict:
    """Return per-portfolio breakdowns, no cross-currency totals."""
    conn = get_db(db_path)
    portfolios = [dict(r) for r in conn.execute("SELECT * FROM portfolios ORDER BY id").fetchall()]
    conn.close()

    pnl = compute_pnl(db_path, portfolio_id, current_prices)
    positions = compute_positions(db_path, portfolio_id, current_prices)

    by_portfolio = []
    for pf in portfolios:
        pid = pf["id"]
        if portfolio_id is not None and pid != portfolio_id:
            continue
        pf_pnl = [p for p in pnl if p["portfolio_id"] == pid]
        pf_pos = [p for p in positions if p["portfolio_id"] == pid]
        by_portfolio.append(
            {
                "id": pid,
                "name": pf["name"],
                "base_ccy": pf["base_ccy"],
                "invested": round(sum(p["total_invested"] for p in pf_pnl), 2),
                "current_value": round(sum(p["current_value"] or 0 for p in pf_pos), 2),
                "realized_pnl": round(sum(p["realized_pnl"] for p in pf_pnl), 2),
                "unrealized_pnl": round(sum(p["unrealized_pnl"] or 0 for p in pf_pnl), 2),
                "total_pnl": round(
                    sum(p["realized_pnl"] for p in pf_pnl) + sum(p["unrealized_pnl"] or 0 for p in pf_pnl), 2
                ),
                "fees": round(sum(p["total_fees"] for p in pf_pnl), 2),
                "positions": len(pf_pos),
            }
        )

    return {
        "portfolios": portfolios,
        "by_portfolio": by_portfolio,
        "pnl": pnl,
        "positions": positions,
    }


# ── Price cache ──────────────────────────────────────────────────────────


def get_cached_prices(db_path: str) -> dict[str, float]:
    conn = get_db(db_path)
    rows = conn.execute("SELECT asset, price FROM price_cache").fetchall()
    conn.close()
    return {r["asset"]: r["price"] for r in rows}


def refresh_prices(db_path: str) -> dict[str, float]:
    """Fetch current prices for all held assets via lib/data.py, update cache."""
    from lib.data import fetch_ohlc

    conn = get_db(db_path)
    assets = [r[0] for r in conn.execute("SELECT DISTINCT asset FROM transactions").fetchall()]
    conn.close()

    now_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    prices: dict[str, float] = {}

    for asset in assets:
        if ":" not in asset:
            continue
        candles = fetch_ohlc(asset)
        if not candles:
            continue
        price = candles[-1][4]  # close
        prices[asset] = price

    conn = get_db(db_path)
    for asset, price in prices.items():
        conn.execute(
            "INSERT OR REPLACE INTO price_cache (asset, price, ts, source) VALUES (?, ?, ?, ?)",
            (asset, price, now_ts, "lib.data"),
        )
    conn.commit()
    conn.close()

    return prices


# ── Performance ──────────────────────────────────────────────────────────


def compute_performance(
    db_path: str, portfolio_id: int | None = None, current_prices: dict[str, float] | None = None
) -> list[dict]:
    conn = get_db(db_path)
    rows = _fetch_transactions_sorted(conn, portfolio_id)
    conn.close()
    fifo = compute_fifo(rows)

    result = []
    for (pid, asset) in sorted(set(fifo["n_buys"].keys()) | set(fifo["n_sells"].keys())):
        n_b = fifo["n_buys"].get((pid, asset), 0)
        n_s = fifo["n_sells"].get((pid, asset), 0)
        realized = fifo["realized"].get((pid, asset), 0)
        invested = fifo["total_invested"].get((pid, asset), 0)
        proceeds = fifo["total_proceeds"].get((pid, asset), 0)
        fees = fifo["total_fees"].get((pid, asset), 0)
        cost_sold = fifo["cost_of_sold"].get((pid, asset), 0)

        result.append(
            {
                "portfolio_id": pid,
                "asset": asset,
                "buys": n_b,
                "sells": n_s,
                "realized_pnl": round(realized, 2),
                "total_invested": round(invested, 2),
                "total_proceeds": round(proceeds, 2),
                "cost_of_sold": round(cost_sold, 2),
                "total_fees": round(fees, 2),
                "profit_factor": round(proceeds / cost_sold, 2) if cost_sold else 0,
            }
        )
    return result


# ── Allocation ────────────────────────────────────────────────────────────


def compute_allocation(
    db_path: str, portfolio_id: int | None = None, current_prices: dict[str, float] | None = None
) -> list[dict]:
    positions = compute_positions(db_path, portfolio_id, current_prices)
    total_value = sum(p["current_value"] or 0 for p in positions)
    if total_value <= 0:
        return []

    result = []
    for pos in positions:
        val = pos["current_value"] or 0
        result.append(
            {
                "portfolio_id": pos["portfolio_id"],
                "asset": pos["asset"],
                "value": round(val, 2),
                "weight_pct": round(val / total_value * 100, 1),
                "qty": pos["qty"],
            }
        )
    return sorted(result, key=lambda x: x["weight_pct"], reverse=True)


# ── Replay ────────────────────────────────────────────────────────────────


def replay_fifo(db_path: str, portfolio_id: int | None = None) -> list[dict]:
    conn = get_db(db_path)
    rows = _fetch_transactions_sorted(conn, portfolio_id)
    conn.close()

    open_lots: dict[str, deque] = defaultdict(deque)
    events = []

    for tx in rows:
        pid = tx["portfolio_id"]
        asset = tx["asset"]
        key = (pid, asset)
        side = tx["side"]
        qty = tx["qty"] or 0
        price = tx["price"] or 0
        fee = tx["fee"] or 0

        if side == "BUY":
            open_lots[key].append({"qty": qty, "price": price, "ts": tx["ts"], "id": tx["id"]})
            events.append(
                {
                    "tx_id": tx["id"],
                    "ts": tx["ts"],
                    "side": "BUY",
                    "asset": asset,
                    "qty": qty,
                    "price": price,
                    "fee": fee,
                    "remain_qty": qty,
                    "consumed_lots": [],
                    "total_realized_pnl": 0,
                }
            )

        elif side == "SELL":
            remaining = qty
            consumed = []
            total_pnl = 0.0

            while remaining > 1e-12 and open_lots[key]:
                lot = open_lots[key][0]
                taken = min(lot["qty"], remaining)
                pnl = taken * (price - lot["price"])
                cost = taken * lot["price"]
                consumed.append(
                    {
                        "tx_id": lot["id"],
                        "qty_consumed": round(taken, 10),
                        "cost_basis": round(cost, 2),
                        "entry_price": lot["price"],
                        "pnl": round(pnl, 2),
                    }
                )
                total_pnl += pnl
                lot["qty"] -= taken
                remaining -= taken
                if lot["qty"] < 1e-12:
                    open_lots[key].popleft()

            events.append(
                {
                    "tx_id": tx["id"],
                    "ts": tx["ts"],
                    "side": "SELL",
                    "asset": asset,
                    "qty": qty,
                    "price": price,
                    "fee": fee,
                    "remain_qty": 0,
                    "consumed_lots": consumed,
                    "total_realized_pnl": round(total_pnl, 2),
                }
            )

    # Post-process BUY events: update remain_qty from final open lots state
    remaining_map: dict[int, float] = {}
    for lots in open_lots.values():
        for lot in lots:
            remaining_map[lot["id"]] = lot["qty"]

    for ev in events:
        if ev["side"] == "BUY":
            ev["remain_qty"] = remaining_map.get(ev["tx_id"], 0)

    return events


# ── Reconcile ─────────────────────────────────────────────────────────────


def reconcile(db_path: str, portfolio_id: int | None, balance: dict[str, float]) -> list[dict]:
    positions = compute_positions(db_path, portfolio_id)
    computed_map: dict[str, float] = {p["asset"]: p["qty"] for p in positions}

    all_assets = set(computed_map.keys()) | set(balance.keys())
    result = []
    for asset in sorted(all_assets):
        cq = computed_map.get(asset, 0)
        eq = balance.get(asset, 0)
        delta = round(cq - eq, 10)
        if abs(delta) < 1e-12:
            status = "match"
        elif cq == 0:
            status = "missing_computed"
        elif eq == 0:
            status = "missing_external"
        else:
            status = "diff"
        result.append(
            {
                "asset": asset,
                "computed_qty": round(cq, 10),
                "external_qty": round(eq, 10),
                "delta": delta,
                "status": status,
            }
        )
    return result


# ── Export ────────────────────────────────────────────────────────────────


def export_transactions(db_path: str, portfolio_id: int | None = None) -> list[dict]:
    return list_transactions(db_path, portfolio_id=portfolio_id)
