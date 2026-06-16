"""Portfolio tracking contracts — TypedDict return shapes."""

from typing import TypedDict


class PortfolioInfo(TypedDict):
    id: int
    name: str
    base_ccy: str
    notes: str | None
    created_at: str


class PositionInfo(TypedDict):
    portfolio_id: int
    asset: str
    qty: float
    avg_cost: float
    cost_basis: float
    current_price: float | None
    current_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


class LotInfo(TypedDict):
    portfolio_id: int
    asset: str
    entry_price: float
    entry_ts: str
    qty: float
    tx_id: int


class PnLAsset(TypedDict):
    portfolio_id: int
    portfolio_name: str
    asset: str
    buys: int
    sells: int
    total_bought_qty: float
    total_sold_qty: float
    total_invested: float
    total_proceeds: float
    total_fees: float
    realized_pnl: float
    realized_pnl_pct: float
    remaining_qty: float
    avg_entry_price: float
    current_price: float | None
    current_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    total_pnl: float | None


class PortfolioSummary(TypedDict):
    portfolios: list[dict]
    by_portfolio: list[dict]
    pnl: list[PnLAsset]
    positions: list[PositionInfo]


class ReplayLot(TypedDict):
    tx_id: int
    qty_consumed: float
    cost_basis: float
    entry_price: float
    pnl: float


class ReplayEvent(TypedDict):
    tx_id: int
    ts: str
    side: str
    asset: str
    qty: float
    price: float
    fee: float
    remain_qty: float
    consumed_lots: list[ReplayLot]
    total_realized_pnl: float


class ReconcileDiff(TypedDict):
    asset: str
    computed_qty: float
    external_qty: float
    delta: float
    status: str
