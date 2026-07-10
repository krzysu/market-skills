"""Portfolio database schema — tables, migrations, connection factory."""

import sqlite3

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
            peak_value REAL NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_id TEXT UNIQUE NOT NULL,
            portfolio_id INTEGER REFERENCES portfolios(id),
            pair TEXT NOT NULL,
            decision_context_json TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_intent ON decisions(intent_id);
        CREATE INDEX IF NOT EXISTS idx_decisions_pair ON decisions(pair);
        CREATE INDEX IF NOT EXISTS idx_decisions_portfolio ON decisions(portfolio_id);
        CREATE TABLE IF NOT EXISTS price_cache (
            asset TEXT PRIMARY KEY,
            price REAL NOT NULL,
            ts TEXT NOT NULL,
            source TEXT NOT NULL
        );
    """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
    if "peak_value" not in cols:
        conn.execute("ALTER TABLE portfolios ADD COLUMN peak_value REAL NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
