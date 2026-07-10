"""Smoke test: every public name in ``portfolio.db`` is importable
from the top-level package. Catches missed re-exports after submodule splits.
"""

from __future__ import annotations


def test_all_public_names_importable():
    import portfolio.db

    public = [
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

    for name in public:
        obj = getattr(portfolio.db, name, None)
        assert obj is not None, f"portfolio.db.{name} is not re-exported"
        assert callable(obj) or isinstance(obj, tuple), f"portfolio.db.{name} has unexpected type {type(obj)}"
