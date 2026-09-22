"""Position, P&L, performance, allocation, drawdown, replay, and reconcile views."""

import sqlite3
from collections import defaultdict, deque

from portfolio.db.fifo import _fetch_transactions_sorted, compute_fifo, compute_lots
from portfolio.db.schema import PEAK_MODEL_DERIVED_CASH, ensure_peak_model_column, get_db


def derive_cash_balances(db_path: str, portfolio_id: int | None = None) -> dict[int, dict[str, float]]:
    """Derive each portfolio's cash-row balance from its own transaction stream.

    The base-ccy cash row (asset key ``<provider>:<BASE_CCY>``, e.g.
    ``kraken:EUR``) is a running balance computed from the portfolio's
    transactions — never a frozen notional and never a venue mirror:

    ::

        cash_balance(portfolio, base_ccy)
          = sum(cash-asset deposits)                    # the row's own BUY rows
          - sum(qty*price + fee for BUY  of non-cash assets)
          + sum(qty*price - fee for SELL of non-cash assets)

    Classification rule: a transaction's asset is the CASH ROW when
    ``asset.split(":", 1)[-1].upper() == portfolio.base_ccy.upper()``.
    A cash-asset BUY is a deposit and a cash-asset SELL is a withdrawal —
    both move the row by ``qty * price`` (fees on cash legs are ignored).

    The walk is scoped to the row's own lifetime, per portfolio: only
    transactions at or after the portfolio's FIRST cash-asset transaction
    are charged to it. Flows occurring before the row first exists are NOT
    charged (they were funded from capital the ledger does not model). A
    portfolio that books its allocation first — the documented convention —
    is unaffected: the row then equals the simple sum over the whole stream.

    A portfolio with NO cash-asset transaction has no cash row at all —
    none is synthesized (a book that never booked deposits has no
    liquidity figure to derive, and a phantom negative row would distort
    its equity). Non-cash BUY/SELL legs adjust the cash row only when the
    portfolio actually has one.

    Invariants:

    1. **Ledger-only.** The balance is a function of the ``transactions``
       table alone. No venue API is consulted; withdrawals, deposits,
       staking drift and dust on the exchange are NOT reconciled in.
    2. **The row's asset key stays the prefixed form** (``kraken:EUR``,
       not ``EUR``) — it is the key seen in the transactions and the key
       risk-engine's ``cash_available`` resolution matches against.
    3. **Equity = derived cash + sum(position market value).** Buying
       moves value from cash into a position instead of double-counting
       both, so closing a swing no longer registers its notional as
       drawdown.

    A NEGATIVE derived balance is permitted and reported as-is: a book
    that deployed more than its cash row is over-deployed, and the
    negative figure is the truthful signal. Never clamp, never abs().

    Returns ``{portfolio_id: {cash_asset_key: balance}}``. Portfolios with
    no base-ccy-cash transaction are absent from the map.
    """
    conn = get_db(db_path)
    try:
        base_ccys = {row["id"]: row["base_ccy"] for row in conn.execute("SELECT id, base_ccy FROM portfolios")}
        rows = _fetch_transactions_sorted(conn, portfolio_id)
    finally:
        conn.close()

    # Pass 1: which portfolios actually have a cash row, and its key.
    # The row is identified by its own deposits/withdrawals (BUY/SELL of
    # the base-ccy asset); its key is whatever prefixed key the ledger
    # uses. No phantom row is created for a portfolio without one.
    cash_keys: dict[int, str] = {}
    for tx in rows:
        pid = tx["portfolio_id"]
        base_ccy = base_ccys.get(pid)
        if base_ccy is None:
            continue
        if tx["asset"].split(":", 1)[-1].upper() == base_ccy.upper():
            cash_keys.setdefault(pid, tx["asset"])

    # Pass 2: walk the stream once, applying the formula. A per-portfolio
    # boundary flag scopes the walk to the row's own lifetime: flows
    # before the portfolio's first cash-asset transaction are ignored.
    balances: dict[int, dict[str, float]] = {}
    boundary_seen: set[int] = set()
    for tx in rows:
        pid = tx["portfolio_id"]
        base_ccy = base_ccys.get(pid)
        if base_ccy is None:
            continue
        asset = tx["asset"]
        side = tx["side"]
        qty = tx["qty"] or 0
        price = tx["price"] or 0
        fee = tx["fee"] or 0
        if asset.split(":", 1)[-1].upper() == base_ccy.upper():
            boundary_seen.add(pid)
            per_pid = balances.setdefault(pid, {})
            balance = per_pid.setdefault(asset, 0.0)
            if side == "BUY":
                per_pid[asset] = balance + qty * price
            elif side == "SELL":
                per_pid[asset] = balance - qty * price
        elif pid in boundary_seen:
            cash_key = cash_keys[pid]
            per_pid = balances.setdefault(pid, {cash_key: 0.0})
            balance = per_pid[cash_key]
            if side == "BUY":
                per_pid[cash_key] = balance - (qty * price + fee)
            elif side == "SELL":
                per_pid[cash_key] = balance + (qty * price - fee)
    return balances


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

    # The base-ccy cash row's value is the DERIVED running balance from the
    # portfolio's transactions (see derive_cash_balances), not the frozen
    # notional of its deposit lots. Flows before the row first exists are
    # not charged to it (booking the allocation first keeps the row equal
    # to the simple sum over the whole stream). The row stays visible even
    # at balance 0 so risk-engine correctly reports 0 free cash rather
    # than dropping it.
    cash_balances = derive_cash_balances(db_path, portfolio_id)
    for pid, assets in cash_balances.items():
        for asset, balance in assets.items():
            key = (pid, asset)
            g = grouped.get(key)
            if g is None:
                g = {"qty": 0, "cost_basis": 0, "portfolio_id": pid, "asset": asset}
                grouped[key] = g
            g["qty"] = balance
            g["cost_basis"] = balance
            g["is_cash_row"] = True

    result = []
    for (pid, asset), g in sorted(grouped.items()):
        if g.get("is_cash_row"):
            balance = round(g["qty"], 2)
            result.append(
                {
                    "portfolio_id": pid,
                    "asset": asset,
                    "qty": round(g["qty"], 10),
                    "avg_cost": 1.0,
                    "cost_basis": balance,
                    "current_price": 1.0,
                    "current_value": balance,
                    "unrealized_pnl": 0.0,
                    "unrealized_pnl_pct": None,
                }
            )
            continue
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
    for pid, asset in sorted(all_keys):
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


def compute_portfolio_drawdown(
    db_path: str, portfolio_id: int, current_prices: dict[str, float] | None = None
) -> float:
    """Equity drawdown vs the persisted high-water mark, in percent.

    Equity = derived cash row (see :func:`derive_cash_balances`) + sum of
    position market values (falling back to cost basis when a position has
    no live price). ``portfolios.peak_value`` is maintained as
    ``max(peak, equity)`` on each call.

    **One-time peak re-baseline (``portfolios.peak_model``).** Persisted
    peaks produced by the pre-derived-cash model double-counted closed
    swings: the frozen cash notional and position notionals were both on
    the books, so every position close registered as drawdown. On the
    first call for a portfolio whose ``peak_model`` marker is unset, the
    stale peak is reset to 0 (only if the portfolio actually has a
    base-ccy cash row — its valuation semantics changed) and the peak is
    re-seeded from the corrected equity by the usual ``max`` logic. The
    marker is stamped in the same transaction, so the reset happens at
    most once per portfolio and never touches a portfolio without a cash
    row. Portfolios without a cash row keep their persisted high-water
    mark untouched.
    """
    positions = compute_positions(db_path, portfolio_id, current_prices)
    current_value = 0.0
    for p in positions:
        val = p.get("current_value")
        if val is not None:
            current_value += float(val)
            continue
        qty = p.get("qty") or 0
        if qty:
            current_value += float(p.get("cost_basis", 0) or 0)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_peak_model_column(conn)
        row = conn.execute("SELECT peak_value, peak_model FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
        if row is None:
            return 0.0
        peak = float(row["peak_value"] or 0.0)

        # One-time re-baseline for the derived-cash valuation model.
        if row["peak_model"] != PEAK_MODEL_DERIVED_CASH:
            base_ccy_row = conn.execute("SELECT base_ccy FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
            base_ccy = base_ccy_row["base_ccy"] if base_ccy_row else None
            has_cash_row = bool(base_ccy) and any(
                (p["asset"].split(":", 1)[-1].upper() == str(base_ccy).upper()) for p in positions
            )
            if has_cash_row:
                peak = 0.0
                conn.execute(
                    "UPDATE portfolios SET peak_value = 0, peak_model = ? WHERE id = ?",
                    (PEAK_MODEL_DERIVED_CASH, portfolio_id),
                )
                conn.commit()
            else:
                conn.execute(
                    "UPDATE portfolios SET peak_model = ? WHERE id = ?",
                    (PEAK_MODEL_DERIVED_CASH, portfolio_id),
                )
                conn.commit()

        new_peak = max(peak, current_value)
        if new_peak != peak:
            conn.execute(
                "UPDATE portfolios SET peak_value = ? WHERE id = ?",
                (new_peak, portfolio_id),
            )
            conn.commit()
            peak = new_peak
    finally:
        conn.close()

    if peak <= 0:
        return 0.0
    if current_value >= peak:
        return 0.0
    return round((peak - current_value) / peak * 100, 4)


def compute_performance(
    db_path: str, portfolio_id: int | None = None, current_prices: dict[str, float] | None = None
) -> list[dict]:
    conn = get_db(db_path)
    rows = _fetch_transactions_sorted(conn, portfolio_id)
    conn.close()
    fifo = compute_fifo(rows)

    result = []
    for pid, asset in sorted(set(fifo["n_buys"].keys()) | set(fifo["n_sells"].keys())):
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

    remaining_map: dict[int, float] = {}
    for lots in open_lots.values():
        for lot in lots:
            remaining_map[lot["id"]] = lot["qty"]

    for ev in events:
        if ev["side"] == "BUY":
            ev["remain_qty"] = remaining_map.get(ev["tx_id"], 0)

    return events


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
