"""portfolio-mgmt — thin wrapper over portfolio.db for skill interface."""

import os

from portfolio import db as _db
from portfolio.db import (
    get_portfolio_summary,
    list_portfolios,
)


def default_db_path() -> str:
    """Resolve the portfolio-mgmt SQLite path from env.

    Reads ``$MARKET_SKILLS_PORTFOLIO_DB``. Raises :class:`OSError` when
    unset — the library deliberately does not paper over with a
    host-specific fallback (see AGENTS.md "What to avoid"). Callers may
    pass ``db_path=`` to use an explicit location.
    """
    path = os.environ.get("MARKET_SKILLS_PORTFOLIO_DB")
    if not path:
        raise OSError(
            "MARKET_SKILLS_PORTFOLIO_DB is not set; cannot resolve the "
            "portfolio-mgmt SQLite path. Set the env var to point at "
            "your portfolio SQLite database, or pass --db=PATH to "
            "override for a single invocation."
        )
    return path


def analyze(db_path: str | None = None):
    """Return a portfolio summary dict (matches skill convention)."""
    return get_portfolio_summary(db_path or default_db_path())


def drawdown(
    db_path: str | None = None,
    portfolio: int | str | None = None,
    current_prices: dict[str, float] | None = None,
) -> float:
    """Return current portfolio drawdown as a percentage (0.0 to 100.0).

    Delegates to ``portfolio.db.compute_portfolio_drawdown``. ``portfolio``
    may be a numeric id or a portfolio name (case-insensitive); omitted/
    ``None`` returns the drawdown for the first portfolio found.

    Persists the updated peak value to ``portfolios.peak_value`` on each call.
    """
    db = db_path or default_db_path()
    if portfolio is None:
        all_pfs = list_portfolios(db)
        if not all_pfs:
            return 0.0
        pid = all_pfs[0]["id"]
    elif isinstance(portfolio, int):
        pid = portfolio
    else:
        pf = _db.get_portfolio(db, portfolio)
        if pf is None:
            return 0.0
        pid = pf["id"]
    return _db.compute_portfolio_drawdown(db, pid, current_prices)


__all__ = [
    "analyze",
    "default_db_path",
    "drawdown",
    "get_portfolio_summary",
    "list_portfolios",
]
