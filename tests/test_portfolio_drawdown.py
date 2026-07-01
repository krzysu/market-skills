"""Tests for portfolio.db.compute_portfolio_drawdown.

Drawdown is high-water-mark based — peak_value is persisted on the
``portfolios`` row and updated to MAX(peak, current_value) on each call.
First call seeds the peak from the current value.

Covers:
    - Migration adds peak_value to existing DBs (created before the column)
    - Drawdown from cost-basis approximation vs peak tracking
    - Cash position contributes to current_value
    - Per-asset fallback to cost basis when no live price
    - Peak persists across calls (does not reset)
"""

import os
import sqlite3

from portfolio.db import (
    add_portfolio,
    add_transaction,
    compute_portfolio_drawdown,
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
        conn.close()


class TestDrawdownBasics:
    def test_empty_portfolio_returns_zero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        assert compute_portfolio_drawdown(db_path, pid) == 0.0

    def test_first_call_seeds_peak_from_current_value(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
        # HYPEUSD: cost_basis 500, no live price yet → falls back to cost basis 500.
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
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
        # First call with no live price: peak seeds at 500.
        compute_portfolio_drawdown(db_path, pid)

        # Now supply a lower live price → current_value drops, peak stays.
        dd = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:HYPEUSD": 40.0})
        # current=400, peak=500, drawdown = (500-400)/500 * 100 = 20%
        assert dd == 20.0

    def test_new_high_keeps_drawdown_at_zero_and_updates_peak(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
        compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:HYPEUSD": 50.0})  # peak=500

        # New high at $60 → peak updates, drawdown = 0.
        dd = compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:HYPEUSD": 60.0})
        assert dd == 0.0
        conn = sqlite3.connect(db_path)
        peak = conn.execute("SELECT peak_value FROM portfolios WHERE id = ?", (pid,)).fetchone()[0]
        conn.close()
        assert peak == 600.0

    def test_peak_persists_across_calls(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
        compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:HYPEUSD": 80.0})  # peak=800
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


class TestDrawdownErrors:
    def test_unknown_portfolio_returns_zero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        assert compute_portfolio_drawdown(db_path, portfolio_id=99999) == 0.0

    def test_asset_with_no_price_falls_back_to_cost_basis(self, tmp_path):
        """Without current_prices, positions use cost basis. Peak seeds from there."""
        db_path = str(tmp_path / "test.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
        dd = compute_portfolio_drawdown(db_path, pid)
        # No live price → current = cost_basis = 500 → peak = 500 → 0%.
        assert dd == 0.0


class TestRiskEngineDrawdownIntegration:
    """build_context auto-populates ctx.current_drawdown_pct from portfolio-mgmt."""

    def test_auto_drawdown_flows_into_risk_context(self, tmp_path, monkeypatch):
        import importlib.util

        db_path = str(tmp_path / "risk.db")
        pid = _init_db_with_peak(db_path)
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
        # Seed the peak with a high-water-mark scenario.
        from portfolio.db import compute_portfolio_drawdown

        compute_portfolio_drawdown(db_path, pid, current_prices={"kraken:HYPEUSD": 80.0})
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
        add_transaction(db_path, pid, "2026-06-22T08:00:00+00:00", "BUY", "kraken:HYPEUSD", qty=10.0, price=50.0)
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
