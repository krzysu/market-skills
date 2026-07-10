"""Portfolio tracking — SQLite-backed, multi-portfolio, FIFO cost basis.

All functions take a ``db_path`` as the first argument. The caller manages
the database file location. Tests use ``:memory:`` or a temp file.

Submodules:
  - ``schema``       — table definitions, migrations, connection factory
  - ``transactions`` — portfolio / transaction / decision CRUD
  - ``fifo``         — FIFO cost-basis engine and open-lot computation
  - ``positions``    — position, P&L, drawdown, allocation, performance, replay, reconcile
  - ``prices``       — price cache fetch/refresh
"""

from portfolio.db.fifo import compute_fifo, compute_lots
from portfolio.db.positions import (
    compute_allocation,
    compute_performance,
    compute_pnl,
    compute_portfolio_drawdown,
    compute_positions,
    get_portfolio_summary,
    reconcile,
    replay_fifo,
)
from portfolio.db.prices import get_cached_prices, refresh_prices
from portfolio.db.schema import VALID_SIDES, get_db, init_db
from portfolio.db.transactions import (
    add_decision,
    add_portfolio,
    add_transaction,
    delete_decision,
    delete_portfolio,
    edit_transaction,
    export_transactions,
    get_decision,
    get_portfolio,
    get_transaction,
    list_decisions,
    list_portfolios,
    list_transactions,
    remove_transaction,
    rename_portfolio,
)

__all__ = [
    "VALID_SIDES",
    "add_decision",
    "add_portfolio",
    "add_transaction",
    "compute_allocation",
    "compute_fifo",
    "compute_lots",
    "compute_performance",
    "compute_pnl",
    "compute_portfolio_drawdown",
    "compute_positions",
    "delete_decision",
    "delete_portfolio",
    "edit_transaction",
    "export_transactions",
    "get_cached_prices",
    "get_db",
    "get_decision",
    "get_portfolio",
    "get_portfolio_summary",
    "get_transaction",
    "init_db",
    "list_decisions",
    "list_portfolios",
    "list_transactions",
    "reconcile",
    "refresh_prices",
    "remove_transaction",
    "rename_portfolio",
    "replay_fifo",
]
