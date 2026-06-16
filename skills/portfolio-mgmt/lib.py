"""portfolio-mgmt — thin wrapper over portfolio.db for skill interface."""

import os

from portfolio.db import (
    get_portfolio_summary,
    list_portfolios,
)

DB_DEFAULT = os.path.expanduser("~/.market-skills/portfolio.db")


def analyze(db_path: str | None = None):
    """Return a portfolio summary dict (matches skill convention)."""
    return get_portfolio_summary(db_path or DB_DEFAULT)


__all__ = ["analyze", "list_portfolios", "get_portfolio_summary"]
