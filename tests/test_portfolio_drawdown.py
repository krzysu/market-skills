"""Tests for portfolio.db.compute_portfolio_drawdown.

Drawdown is high-water-mark based — peak_value is persisted on the
``portfolios`` row and updated to MAX(peak, current_value) on each call.
First call seeds the peak from the current value.

Covers:
    - Migration adds peak_value/peak_model to existing DBs (created before the columns)
    - Drawdown from cost-basis approximation vs peak tracking
    - Cash position contributes to current_value (cash-asset SELL = withdrawal)
    - The base-ccy cash row is a DERIVED running balance from the ledger
    - Per-asset fallback to cost basis when no live price
    - Peak persists across calls (does not reset)
    - One-time peak re-baseline for portfolios with a cash row
"""

import os
import sqlite3

from portfolio.db import (
    add_portfolio,
    add_transaction,
    compute_portfolio_drawdown,
    compute_positions,
    init_db,
)


def _init_db_with_peak(db_path: str) -> int:
    init_db(db_path)
    return add_portfolio(db_path, "spot", base_ccy="EUR")


class TestMigration:
    """The peak_value migration runs at the end of init_db."""

    def test_init_db_adds_peak_value_column(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
        assert "peak_value" in cols
        conn.close()

    def test_migration_idempotent(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        init_db(db_path)  # second call must not raise
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
        assert "peak_value" in cols
        conn.close()

    def test_existing_db_without_peak_value_gets_altered(self, tmp_path):
        """Simulate a pre-migration DB by creating the portfolios table without peak_value."""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_ccy TEXT NOT NULL DEFAULT 'EUR',
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()
        conn.close()
        # init_db should add peak_value via migration
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
        assert "peak_value" in cols
        assert "peak_model" in cols
        conn.close()

    def test_init_db_adds_peak_model_column(self, tmp_path):
        """The peak re-baseline marker column ships with init_db."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
        assert "peak_model" in cols
        conn.close()

    def test_drawdown_call_migrates_pre_existing_db(self, tmp_path):
        """compute_portfolio_drawdown lazily adds peak_model to a DB that
        never ran the new init_db (the risk-engine read path has no
        init_db call — the live book gets the column on first drawdown).
        """
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_ccy TEXT NOT NULL DEFAULT 'EUR',
                peak_value REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER,
                ts TEXT,
                side TEXT,
                asset TEXT,
                qty REAL,
                price REAL,
                fee REAL DEFAULT 0
            );
            INSERT INTO portfolios (id, name, base_ccy) VALUES (1, 'spot', 'EUR');
            """
        )
        conn.commit()
        conn.close()
        # No init_db here — the drawdown call itself must migrate + not crash.
        assert compute_portfolio_drawdown(db_path, 1) == 0.0
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
        assert "peak_model" in cols
        conn.close()


class TestDrawdownBasics:
    def test_empty_portfolio_returns_zero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        assert compute_portfolio_drawdown(db_path, pid) == 0.0

    def test_first_call_seeds_peak_from_current_value(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        # <PRIVATE_PERP>USD: cost_basis 500, no live price yet → falls back to cost basis 500.
        dd = compute_portfolio_drawdown(db_path, pid)
        assert dd == 0.0  # current=500, peak=500, no drawdown

        # peak should now be 500
        conn = sqlite3.connect(db_path)
        peak = conn.execute("SELECT peak_value FROM portfolios WHERE id = ?", (pid,)).fetchone()[0]
        conn.close()
        assert peak == 500.0

    def test_drawdown_when_current_below_peak(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        # First call with no live price: peak seeds at 500.
        compute_portfolio_drawdown(db_path, pid)

        # Now supply a lower live price → current_value drops, peak stays.
        dd = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:<PRIVATE_PERP>USD": 40.0})
        # current=400, peak=500, drawdown = (500-400)/500 * 100 = 20%
        assert dd == 20.0

    def test_new_high_keeps_drawdown_at_zero_and_updates_peak(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:<PRIVATE_PERP>USD": 50.0})  # peak=500

        # New high at $60 → peak updates, drawdown = 0.
        dd = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:<PRIVATE_PERP>USD": 60.0})
        assert dd == 0.0
        conn = sqlite3.connect(db_path)
        peak = conn.execute("SELECT peak_value FROM portfolios WHERE id = ?", (pid,)).fetchone()[0]
        conn.close()
        assert peak == 600.0

    def test_peak_persists_across_calls(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:<PRIVATE_PERP>USD": 80.0})  # peak=800
        # Subsequent call without live price: current falls back to cost basis (500),
        # peak stays 800 → drawdown = (800-500)/800 * 100 = 37.5%.
        dd = compute_portfolio_drawdown(db_path, pid)
        assert dd == 37.5


class TestDrawdownWithCash:
    def test_cash_position_counts_fully(self, tmp_path):
        """A EUR cash position contributes its full qty (no FX)."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        # First call: cash position cost_basis=1000 → current_value=1000 → peak=1000.
        dd = compute_portfolio_drawdown(db_path, pid)
        assert dd == 0.0
        # Now "withdraw" half by selling back EUR — qty goes to 500, cost basis to 500.
        add_transaction(db_path, pid, "2026-06-22T09:00:00+00:00", "SELL", "kraken:EUR", qty=500.0, price=1.0)
        dd = compute_portfolio_drawdown(db_path, pid)
        # current=500, peak=1000, drawdown = (1000-500)/1000 * 100 = 50%
        assert dd == 50.0


class TestDerivedCashRow:
    """The base-ccy cash row is a derived running balance from the ledger."""

    def _seed_cash(self, db_path: str, pid: int) -> None:
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)

    def test_cash_row_value_derives_from_transactions(self, tmp_path):
        """Non-cash buy reduces the row by qty*price + fee; non-cash sell
        raises it by qty*price - fee; a cash-asset SELL (withdrawal)
        reduces it by qty*price."""
        from portfolio.db import derive_cash_balances

        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        self._seed_cash(db_path, pid)
        add_transaction(
            db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0, fee=4.0
        )
        # 1000 - (400 + 4) = 596
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 596.0}}

        add_transaction(
            db_path, pid, "2026-06-22T10:00:00+00:00", "SELL", "kraken:COINUSD", qty=100.0, price=1.2, fee=2.0
        )
        # 596 + (120 - 2) = 714
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 714.0}}

        add_transaction(db_path, pid, "2026-06-22T11:00:00+00:00", "SELL", "kraken:EUR", qty=100.0, price=1.0)
        # 714 - 100 = 614 (withdrawal)
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 614.0}}

        cash = next(p for p in compute_positions(db_path, pid) if p["asset"] == "kraken:EUR")
        assert cash["qty"] == 614.0
        assert cash["avg_cost"] == 1.0
        assert cash["cost_basis"] == 614.0
        assert cash["current_value"] == 614.0
        assert cash["unrealized_pnl"] == 0.0
        assert cash["unrealized_pnl_pct"] is None

    def test_cash_row_stays_visible_when_balance_is_zero(self, tmp_path):
        """A fully deployed row must still be reported (risk-engine then
        correctly reports 0 free cash) — never dropped."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        self._seed_cash(db_path, pid)
        add_transaction(db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=1000.0, price=1.0)
        cash = next(p for p in compute_positions(db_path, pid) if p["asset"] == "kraken:EUR")
        assert cash["qty"] == 0.0
        assert cash["current_value"] == 0.0

    def test_negative_derived_balance_permitted_when_over_deployed(self, tmp_path):
        """A book that deployed more than its cash row is over-deployed;
        the negative figure is the truthful signal — never clamped."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        self._seed_cash(db_path, pid)
        add_transaction(
            db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=1200.0, price=1.0, fee=5.0
        )
        # 1000 - (1200 + 5) = -205
        cash = next(p for p in compute_positions(db_path, pid) if p["asset"] == "kraken:EUR")
        assert cash["qty"] == -205.0
        assert cash["current_value"] == -205.0

    def test_no_phantom_cash_row_without_cash_transactions(self, tmp_path):
        """A portfolio that never booked a cash-asset transaction has no
        cash row — none is synthesized (no phantom negative row)."""
        from portfolio.db import derive_cash_balances

        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0)
        assert derive_cash_balances(db_path, pid) == {}
        assert [p["asset"] for p in compute_positions(db_path, pid)] == ["kraken:COINUSD"]

    def test_deposit_booked_after_earlier_trades_ignores_them(self, tmp_path):
        """A cash row booked AFTER earlier trades derives its balance from
        its own lifetime only: the pre-deposit flows are not charged to it."""
        from portfolio.db import derive_cash_balances

        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0, fee=1.0
        )
        # Deposit booked LAST — the 400 buy predates the row's existence.
        add_transaction(db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        # Row = its own deposit (1000); the earlier buy is NOT charged.
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 1000.0}}

        # Flows after the deposit still adjust the row normally.
        add_transaction(
            db_path, pid, "2026-06-22T10:00:00+00:00", "BUY", "kraken:COINUSD", qty=100.0, price=2.0, fee=1.0
        )
        # 1000 - (200 + 1) = 799
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 799.0}}

        # A cash-asset SELL (withdrawal) after the boundary also applies.
        add_transaction(db_path, pid, "2026-06-22T11:00:00+00:00", "SELL", "kraken:EUR", qty=50.0, price=1.0)
        # 799 - 50 = 749
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 749.0}}

    def test_deposit_booked_first_charges_earlier_none(self, tmp_path):
        """Deposit FIRST then the same buy: the row charges the buy —
        600, unchanged from the whole-stream behaviour."""
        from portfolio.db import derive_cash_balances

        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        add_transaction(db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0)
        # 1000 - 400 = 600
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 600.0}}

    def test_multiple_transactions_before_deposit_are_all_ignored(self, tmp_path):
        """Several pre-deposit flows (buys and sells) are all outside the
        row's lifetime; only post-deposit flows count."""
        from portfolio.db import derive_cash_balances

        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T06:00:00+00:00", "BUY", "kraken:COINUSD", qty=300.0, price=1.0, fee=2.0
        )
        add_transaction(
            db_path, pid, "2026-06-22T07:00:00+00:00", "SELL", "kraken:COINUSD", qty=100.0, price=1.5, fee=1.0
        )
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        # Row = 1000 — both pre-deposit flows ignored.
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 1000.0}}

    def test_deposit_as_very_first_row_is_whole_stream(self, tmp_path):
        """When the deposit is the very first transaction of the book the
        boundary is the start of the stream — behaviour identical to the
        simple whole-stream sum."""
        from portfolio.db import derive_cash_balances

        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T06:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        add_transaction(
            db_path, pid, "2026-06-22T07:00:00+00:00", "BUY", "kraken:COINUSD", qty=300.0, price=1.0, fee=2.0
        )
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "SELL", "kraken:COINUSD", qty=100.0, price=1.5, fee=1.0
        )
        # 1000 - (300 + 2) + (150 - 1) = 847
        assert derive_cash_balances(db_path, pid) == {pid: {"kraken:EUR": 847.0}}


class TestDrawdownDerivedCashEquity:
    """Equity = derived cash + positions: breakeven/losing closes register
    fees and realized loss only — never the position notional."""

    def test_breakeven_close_drawdown_is_fees_only(self, tmp_path):
        """Open and close at the same price: peak-to-current difference is
        exactly the total fees, never the 400 notional (pre-fix it was ~28.6%)."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        # Peak seeds from the deposit alone: equity = 1000.
        assert compute_portfolio_drawdown(db_path, pid, current_prices={}) == 0.0

        add_transaction(
            db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0, fee=4.0
        )
        # Equity = 596 cash + 400 position = 996 — only the buy fee burned.
        dd_open = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:COINUSD": 1.0})
        assert dd_open == 0.4

        add_transaction(
            db_path, pid, "2026-06-22T10:00:00+00:00", "SELL", "kraken:COINUSD", qty=400.0, price=1.0, fee=4.0
        )
        # Equity = 992 cash. Peak-to-current = 1000 - 992 = 8 = total fees.
        dd_closed = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:COINUSD": 1.0})
        assert dd_closed == 0.8

        conn = sqlite3.connect(db_path)
        peak = conn.execute("SELECT peak_value FROM portfolios WHERE id = ?", (pid,)).fetchone()[0]
        conn.close()
        assert peak == 1000.0

    def test_losing_close_drawdown_equals_realized_loss_plus_fees(self, tmp_path):
        """A losing close reduces derived cash by exactly (realized loss +
        fees) and the drawdown moves by that amount."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        assert compute_portfolio_drawdown(db_path, pid, current_prices={}) == 0.0

        add_transaction(
            db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0, fee=4.0
        )
        add_transaction(
            db_path, pid, "2026-06-22T10:00:00+00:00", "SELL", "kraken:COINUSD", qty=400.0, price=0.95, fee=4.0
        )
        # Realized loss 20 (400 * (0.95 - 1.0)) + fees 8 = 28 equity drop.
        # Derived cash: 1000 - 404 + (380 - 4) = 972. Drawdown = 28/1000 = 2.8%.
        cash = next(p for p in compute_positions(db_path, pid) if p["asset"] == "kraken:EUR")
        assert cash["qty"] == 972.0
        dd = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:COINUSD": 1.0})
        assert dd == 2.8


class TestPeakRebaseline:
    """One-time peak re-baseline for the derived-cash valuation model."""

    def _set_peak(self, db_path: str, pid: int, peak: float) -> None:
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE portfolios SET peak_value = ? WHERE id = ?", (peak, pid))
        conn.commit()
        conn.close()

    def _read_row(self, db_path: str, pid: int) -> tuple[float, str | None]:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT peak_value, peak_model FROM portfolios WHERE id = ?", (pid,)).fetchone()
        conn.close()
        return row

    def test_stale_peak_resets_once_for_cash_row_portfolio(self, tmp_path):
        """Stale old-model peak (cash 1000 + position 400 double-counted)
        resets and re-seeds from the corrected equity — once."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:EUR", qty=1000.0, price=1.0)
        add_transaction(
            db_path, pid, "2026-06-22T09:00:00+00:00", "BUY", "kraken:COINUSD", qty=400.0, price=1.0, fee=4.0
        )
        self._set_peak(db_path, pid, 1400.0)  # stale old-model peak

        # Corrected equity = 596 + 400 = 996. The stale peak must not survive.
        dd = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:COINUSD": 1.0})
        assert dd == 0.0
        peak, peak_model = self._read_row(db_path, pid)
        assert peak == 996.0
        assert peak_model is not None

        # Idempotent: the marker is stamped, the reset never fires again —
        # the peak now tracks real equity highs only.
        dd2 = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:COINUSD": 1.0})
        assert dd2 == 0.0
        peak2, peak_model2 = self._read_row(db_path, pid)
        assert peak2 == 996.0
        assert peak_model2 == peak_model

    def test_no_reset_for_portfolio_without_cash_row(self, tmp_path):
        """A portfolio without a cash row keeps its persisted high-water
        mark — only the marker is stamped."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:COINUSD", qty=500.0, price=1.0)
        self._set_peak(db_path, pid, 800.0)

        dd = compute_portfolio_drawdown(db_path, pid)
        assert dd == 37.5  # (800 - 500) / 800 — peak NOT reset
        peak, peak_model = self._read_row(db_path, pid)
        assert peak == 800.0
        assert peak_model is not None

        # Marker stamped → nothing changes on later calls either.
        dd2 = compute_portfolio_drawdown(db_path, pid)
        assert dd2 == 37.5
        assert self._read_row(db_path, pid) == (800.0, peak_model)


class TestDrawdownErrors:
    def test_unknown_portfolio_returns_zero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        assert compute_portfolio_drawdown(db_path, portfolio_id=99999) == 0.0

    def test_asset_with_no_price_falls_back_to_cost_basis(self, tmp_path):
        """Without current_prices, positions use cost basis. Peak seeds from there."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        dd = compute_portfolio_drawdown(db_path, pid)
        # No live price → current = cost_basis = 500 → peak = 500 → 0%.
        assert dd == 0.0


class TestRiskEngineDrawdownIntegration:
    """build_context auto-populates ctx.current_drawdown_pct from portfolio-mgmt."""

    def test_auto_drawdown_flows_into_risk_context(self, tmp_path, monkeypatch):
        import importlib.util

        db_path = str(tmp_path / "risk.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        # Seed the peak with a high-water-mark scenario.
        from portfolio.db import compute_portfolio_drawdown

        compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:<PRIVATE_PERP>USD": 80.0})
        # Now peak is 800. We force current_value to fall back to cost basis (500) by
        # making the cache miss + the live fetch fail — that pins the math.
        monkeypatch.setattr("portfolio.db.get_cached_prices", lambda db: {})

        def no_spot(_):
            raise OSError("kraken CLI not installed in tests")

        monkeypatch.setattr("analysis.data.fetch_spot_price", no_spot)
        lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "risk-engine", "lib.py")
        spec = importlib.util.spec_from_file_location("risk_engine_dd_test", lib_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from argparse import Namespace

        ctx = mod.build_context(
            Namespace(
                portfolio="spot",
                db=db_path,
                watchlist=None,
                drawdown_pct=None,
                refresh_prices=False,
            )
        )
        # current_value=500 (cost basis fallback), peak=800 → drawdown 37.5%.
        assert ctx.current_drawdown_pct == 37.5

    def test_cli_drawdown_pct_flag_overrides_auto_compute(self, tmp_path, monkeypatch):
        """--drawdown-pct wins over the auto-computed value (CLI override)."""
        import importlib.util

        db_path = str(tmp_path / "risk.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(
            db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:<PRIVATE_PERP>USD", qty=10.0, price=50.0
        )
        monkeypatch.setattr("portfolio.db.get_cached_prices", lambda db: {})
        lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "risk-engine", "lib.py")
        spec = importlib.util.spec_from_file_location("risk_engine_dd_override_test", lib_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from argparse import Namespace

        ctx = mod.build_context(
            Namespace(
                portfolio="spot",
                db=db_path,
                watchlist=None,
                drawdown_pct=2.5,  # CLI override
                refresh_prices=False,
            )
        )
        assert ctx.current_drawdown_pct == 2.5  # not the auto-computed value
