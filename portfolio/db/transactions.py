"""Portfolio, transaction, and decision CRUD operations."""

from portfolio.db.schema import VALID_SIDES, get_db

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


# ── Decision CRUD ────────────────────────────────────────────────────────


def add_decision(
    db_path: str,
    intent_id: str,
    pair: str,
    decision_context_json: str,
    portfolio_id: int | None = None,
    captured_at: str | None = None,
) -> int:
    from datetime import UTC, datetime

    conn = get_db(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO decisions
           (intent_id, portfolio_id, pair, decision_context_json, captured_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            intent_id,
            portfolio_id,
            pair,
            decision_context_json,
            captured_at or datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM decisions WHERE intent_id = ?", (intent_id,)).fetchone()
    conn.close()
    return int(row["id"]) if row else 0


def get_decision(db_path: str, intent_id: str) -> dict | None:
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM decisions WHERE intent_id = ?", (intent_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_decisions(
    db_path: str,
    portfolio_id: int | None = None,
    pair: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    conn = get_db(db_path)
    where: list[str] = []
    params: list = []
    if portfolio_id is not None:
        where.append("portfolio_id = ?")
        params.append(portfolio_id)
    if pair is not None:
        where.append("pair = ?")
        params.append(pair)
    if since is not None:
        where.append("captured_at >= ?")
        params.append(since)

    sql = "SELECT * FROM decisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY captured_at DESC, id DESC"
    if limit is not None:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_decision(db_path: str, decision_id: int) -> bool:
    conn = get_db(db_path)
    conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


# ── Export ────────────────────────────────────────────────────────────────


def export_transactions(db_path: str, portfolio_id: int | None = None) -> list[dict]:
    return list_transactions(db_path, portfolio_id=portfolio_id)
