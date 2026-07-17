"""backtest-engine — walk-forward replay loop + fill simulator for L1/L2/L3 strategies.

Bead bt-1: per-bar, per-ticker walk-forward replay. The runner feeds each
strategy callable only the past at bar t (``candles[:t+1]``) so no idea can
depend on future bars.

Bead bt-2: :class:`FillSimulator` — a deterministic, conservative intrabar
fill model. Entries fill at the next bar's open; the stop wins on a same-bar
stop+target tie; per-strategy fee + slippage defaults (Kraken taker 0.26% +
2bps slippage floor).

Risk-engine wiring (bt-3), metrics/benchmark (bt-4), and full docs (bt-5) are
subsequent beads.

Bead bt-4: :func:`compute` (per-strategy metrics — total/annualized return,
Sharpe, Sortino, max drawdown, profit factor, average trade, trade count) and
:func:`buy_and_hold_benchmark` (one unit bought at the entry open and held to
the end, same worst-case fee + slippage rule as :class:`FillSimulator`).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any, TypedDict

from analysis.contracts import L3Idea
from analysis.providers.execution.base import FillConfirmation


class Bar(TypedDict):
    """One OHLCV candle — the existing list-of-lists shape used everywhere.

    The codebase carries candles as ``[[ts, open, high, low, close, volume], ...]``
    at runtime; this TypedDict is the named logical contract for a single bar
    so consumers can reason about fields by name instead of by index.
    """

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int


Candles = list[Bar]
"""Type alias for a candle series. Runtime representation is ``list[list]``."""


class IdeaWindow(TypedDict):
    """The strategy's verdict at one bar of the walk-forward replay.

    ``bar_index`` is the position in the input ``candles`` list. ``asof_ts`` is
    ``candles[bar_index][0]`` — the timestamp of the bar the idea was built
    from. ``idea`` is the first emitted :data:`L3Idea` or ``None`` when the
    strategy fired no idea at this bar (kept so the window length stays
    observable, not silently truncated).
    """

    bar_index: int
    ticker: str
    asof_ts: int
    idea: L3Idea | None


class NoLookaheadError(Exception):
    """Raised when a strategy exposes a precomputed idea stream.

    The walk-forward loop forbids look-ahead: the idea for bar t must be built
    from ``candles[:t+1]`` only. A strategy that exposes ``precomputed_ideas``
    is attempting the "compute once, walk the output" anti-pattern and is
    rejected before the loop starts.
    """

    def __init__(self, strategy, message: str | None = None) -> None:
        self.strategy = strategy
        name = getattr(strategy, "__name__", None) or type(strategy).__name__
        msg = message or (
            f"{name}: strategy exposes precomputed_ideas — walk-forward loop forbids "
            f"look-ahead; build the idea per bar instead"
        )
        super().__init__(msg)


class WalkForwardRunner:
    """Drive per-ticker bar iteration, calling ``strategy.analyze`` per bar.

    The runner is stateless across calls: ``strategy`` is passed per
    :meth:`run`. The walk-forward loop replays each strict prefix of the
    candle series exactly once, so there is no repeated prefix to cache — the
    loop is intentionally cache-free. Two runs with identical inputs return
    identical outputs because the strategy is deterministic and the runner
    retains no state between calls.

    The strategy argument is duck-typed: any module or object exposing
    ``analyze(candles, *, ticker, interval, period, asset_class)`` works.
    """

    def __init__(self) -> None:
        pass

    def run(
        self,
        strategy,
        ticker: str,
        candles: Candles,
        *,
        warmup: int = 0,
        interval: str = "1d",
        period: str = "1y",
        asset_class: str | None = None,
        **strategy_kwargs: Any,
    ) -> list[IdeaWindow]:
        if hasattr(strategy, "precomputed_ideas"):
            raise NoLookaheadError(strategy)

        windows: list[IdeaWindow] = []
        start = max(warmup, 0)
        n = len(candles)
        for t in range(start, n):
            prefix = candles[: t + 1]
            result = strategy.analyze(
                prefix,
                ticker=ticker,
                interval=interval,
                period=period,
                asset_class=asset_class,
                **strategy_kwargs,
            )
            ideas = result.get("ideas", []) if isinstance(result, dict) else []
            # L3 strategies emit at most one idea per call; take the first so
            # the window length stays observable even when nothing fired.
            idea = ideas[0] if ideas else None
            windows.append(
                IdeaWindow(
                    bar_index=t,
                    ticker=ticker,
                    asof_ts=candles[t][0],
                    idea=idea,
                )
            )
        return windows


logger = logging.getLogger(__name__)


def _ts_iso(ts: int) -> str:
    """Bar timestamp (Unix seconds) -> ISO 8601 UTC.

    Deterministic by design: the fill simulator never reads the wall clock, so
    two backtest runs over the same candles produce identical fill timestamps
    (unlike the live adapters, which stamp with ``datetime.now(UTC)``).
    """
    return datetime.fromtimestamp(int(ts), UTC).isoformat()


def _build_fill(
    *,
    intent_id: str,
    order_id: str,
    pair: str,
    side: str,
    order_type: str,
    requested_volume: float,
    filled_volume: float,
    fill_price: float | None,
    cost_quote: float | None,
    fee: float,
    status: str,
    reason: str,
    timestamp: str,
    venue: str,
    raw: dict[str, Any],
) -> FillConfirmation:
    """Construct a :data:`FillConfirmation` with the simulator's conventions.

    ``fee_currency`` is left empty — the simulator has no venue to report the
    fee currency from (the live adapter populates it from the venue response).
    ``cl_ord_id`` mirrors ``intent_id``; the LLM owns intent_id uniqueness.
    """
    return FillConfirmation(
        intent_id=intent_id,
        order_id=order_id,
        cl_ord_id=intent_id,
        pair=pair,
        side=side,
        order_type=order_type,
        requested_volume=requested_volume,
        filled_volume=filled_volume,
        fill_price=fill_price,
        cost_quote=cost_quote,
        fee=fee,
        fee_currency="",
        status=status,
        reason=reason,
        timestamp=timestamp,
        venue=venue,
        raw=raw,
    )


class TradeRecord(TypedDict):
    """One round-trip trade produced by :class:`FillSimulator`.

    Composed of two real :data:`FillConfirmation` TypedDicts — ``entry`` (the
    open fill at the next bar's open, slippage + entry fee applied) and
    ``exit`` (the close fill at the stop or target price). Both are the exact
    shape live trading produces, so a future orchestrator can run the same
    post-fill handling on either side (see ``LLM-ORCHESTRATION.md``).

    ``status`` is the trade-level outcome:
      ``"filled"``  — entry filled AND an exit (stop or target) fired.
      ``"open"``    — entry filled but no stop/target touched by the end of the
                      series; ``exit.fill_price`` is ``None``.
      ``"skipped"`` — no next bar to fill the entry (backtest-only
                      pseudo-status; NOT in the live ``FillConfirmation``
                      status set).

    ``exit_reason`` is ``"stop"`` / ``"target"`` / ``"none"``. ``pnl_quote`` is
    the realized quote P&L for a ``"filled"`` trade (exit vs entry, minus the
    entry fee; exit fees are not modelled in v1); ``None`` otherwise.
    """

    entry: FillConfirmation
    exit: FillConfirmation
    status: str
    exit_reason: str
    exit_bar_index: int | None
    pnl_quote: float | None


class FillSimulator:
    """Deterministic, conservative intrabar fill simulator for backtests.

    Decision model
    --------------
    * **Next-bar-open entry.** The entry fills at
      ``candles[entry_bar_index+1].open`` — a strategy decides at bar ``t`` and
      the earliest it can act is bar ``t+1``'s open, never the signal bar's
      close. When ``entry_bar_index+1`` is out of range the fill is skipped
      (``status="skipped"``) and a debug log line is emitted.
    * **Stop-first intrabar tie.** When a single bar's range touches BOTH the
      stop and the target, the STOP is assumed to fire first (worst case) and
      the trade exits at ``stop_loss``. A target fills only on a bar where the
      stop is not touched. Intrabar ordering is unknowable from OHLC, so the
      conservative (adverse) outcome is assumed.
    * **Slippage** is applied to the entry as a worst-case fill: longs pay
      ``open * (1 + slippage_bps/1e4)``, shorts receive
      ``open * (1 - slippage_bps/1e4)``. The quote cost of slippage lands in
      ``entry.raw["slippage_paid"]``.

    Fee model (v1 — entry only)
    ---------------------------
    ``fee = cost_quote * fee_bps / 10_000`` where
    ``cost_quote = filled_volume * fill_price`` (the entry fill price,
    post-slippage). Defaults: ``fee_bps=26`` (Kraken taker tier 0.26%),
    ``slippage_bps=2`` (per-side slippage floor). Exit fees are NOT modelled
    in v1 — planned for a v1.1 follow-up if requested.

    Configuration priority: per-call kwargs > ``ctx`` dict > instance defaults.

    Return shape
    ------------
    :meth:`simulate` returns a :class:`TradeRecord` (NOT a bare
    ``FillConfirmation``) so the entry and exit are each a real
    ``FillConfirmation`` — the exact shape live trading produces — and a
    future orchestrator can reuse the same post-fill handling on either side.
    This resolves the entry-fee vs exit-price tension cleanly: the entry fill
    carries the entry price + entry fee; the exit fill carries the exit price.

    Slot plumbing
    -------------
    Simulator-specific named slots are plumbed under each fill's ``raw`` dict
    to keep the ``FillConfirmation`` TypedDict contract intact (no field is
    added to ``analysis/providers/execution/base.py``):

      ``raw["qty"]``           — mirror of ``filled_volume`` (position size).
      ``raw["fee_paid"]``      — mirror of ``fee`` (entry fee, quote).
      ``raw["slippage_paid"]`` — quote cost of entry slippage (abs), entry only.
      ``raw["entry_price"]``   — post-slippage entry fill (exit fill only).
      ``raw["exit_reason"]``   — ``"stop"`` / ``"target"`` / ``"none"`` / ``"skipped"``.

    ``status="skipped"`` is a backtest-only pseudo-status (no next bar to fill
    the entry); it is NOT in the live ``FillConfirmation`` status set
    (``filled`` / ``partial`` / ``open`` / ``rejected`` / ``cancelled`` /
    ``expired`` / ``error``).
    """

    def __init__(self, *, fee_bps: int = 26, slippage_bps: int = 2) -> None:
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def simulate(
        self,
        idea: L3Idea,
        candles: Candles,
        entry_bar_index: int,
        ctx: dict[str, Any] | None = None,
        *,
        fee_bps: int | None = None,
        slippage_bps: int | None = None,
    ) -> TradeRecord:
        ctx = ctx or {}
        # Config priority: per-call kwargs > ctx dict > instance defaults.
        f_bps = fee_bps if fee_bps is not None else int(ctx.get("fee_bps", self.fee_bps))
        s_bps = slippage_bps if slippage_bps is not None else int(ctx.get("slippage_bps", self.slippage_bps))
        qty = float(ctx.get("qty", 1.0))
        pair = str(idea.get("pair", ""))
        direction = idea.get("direction", "long")
        is_long = direction == "long"
        stop_loss = idea.get("stop_loss")
        tps = idea.get("take_profit") or []
        # The TP ladder is a list; for v1 "target" means TP[0] (L3 strategies
        # validate ladders via analysis.contracts.validate_l3_tp_ladder).
        target = tps[0] if tps else None
        intent_id = str(ctx.get("intent_id", f"bt-{pair}-{entry_bar_index}"))
        venue = "backtest"
        entry_order_type = str(idea.get("entry_type", "market"))
        entry_side = "buy" if is_long else "sell"

        n = len(candles)
        entry_idx = entry_bar_index + 1

        # --- Skipped: no next bar to fill the entry. ---
        if entry_idx >= n:
            logger.debug("FillSimulator skip: entry_bar_index=%d has no next bar (candles=%d)", entry_bar_index, n)
            sig_ts = _ts_iso(int(candles[min(entry_bar_index, n - 1)][0])) if n else ""
            skipped = _build_fill(
                intent_id=intent_id,
                order_id=f"bt-entry-{entry_bar_index}",
                pair=pair,
                side=entry_side,
                order_type=entry_order_type,
                requested_volume=qty,
                filled_volume=0.0,
                fill_price=None,
                cost_quote=None,
                fee=0.0,
                status="skipped",
                reason="no next bar to fill entry",
                timestamp=sig_ts,
                venue=venue,
                raw={"qty": qty, "fee_paid": 0.0, "slippage_paid": 0.0, "exit_reason": "skipped"},
            )
            return TradeRecord(
                entry=skipped,
                exit=skipped,
                status="skipped",
                exit_reason="none",
                exit_bar_index=None,
                pnl_quote=None,
            )

        # --- Entry fill at next-bar open (worst-case slippage). ---
        entry_bar = candles[entry_idx]
        open_price = float(entry_bar[1])
        slip = open_price * s_bps / 10_000.0
        entry_price = open_price + slip if is_long else open_price - slip
        slippage_paid = abs(entry_price - open_price) * qty
        cost_quote = qty * entry_price
        fee = cost_quote * f_bps / 10_000.0
        entry_ts = _ts_iso(int(entry_bar[0]))
        entry_fill = _build_fill(
            intent_id=intent_id,
            order_id=f"bt-entry-{entry_idx}",
            pair=pair,
            side=entry_side,
            order_type=entry_order_type,
            requested_volume=qty,
            filled_volume=qty,
            fill_price=entry_price,
            cost_quote=cost_quote,
            fee=fee,
            status="filled",
            reason="simulated entry at next-bar open",
            timestamp=entry_ts,
            venue=venue,
            raw={
                "qty": qty,
                "fee_paid": fee,
                "slippage_paid": slippage_paid,
                "open_price": open_price,
                "entry_bar_index": entry_idx,
            },
        )

        # --- Walk forward bar-by-bar from the entry bar; stop wins ties. ---
        # The entry fills at the OPEN of bar entry_idx, so the rest of that
        # same bar can still touch the stop/target — entry_idx is the first
        # bar checked. Stop is checked before target so a same-bar tie exits
        # at the stop (worst case).
        exit_reason = "none"
        exit_price: float | None = None
        exit_bar_index: int | None = None
        for b in range(entry_idx, n):
            bar = candles[b]
            hi = float(bar[2])
            lo = float(bar[3])
            if is_long:
                stop_touched = stop_loss is not None and lo <= float(stop_loss)
                target_touched = target is not None and hi >= float(target)
            else:
                stop_touched = stop_loss is not None and hi >= float(stop_loss)
                target_touched = target is not None and lo <= float(target)
            if stop_touched:
                exit_reason = "stop"
                exit_price = float(stop_loss)
                exit_bar_index = b
                break
            if target_touched:
                exit_reason = "target"
                exit_price = float(target)
                exit_bar_index = b
                break

        # --- Exit fill at stop/target, or still-open at end of series. ---
        if exit_reason in ("stop", "target") and exit_price is not None and exit_bar_index is not None:
            exit_side = "sell" if is_long else "buy"
            exit_order_type = "stop-loss" if exit_reason == "stop" else "take-profit"
            exit_ts = _ts_iso(int(candles[exit_bar_index][0]))
            exit_fill = _build_fill(
                intent_id=intent_id,
                order_id=f"bt-exit-{exit_bar_index}",
                pair=pair,
                side=exit_side,
                order_type=exit_order_type,
                requested_volume=qty,
                filled_volume=qty,
                fill_price=exit_price,
                cost_quote=qty * exit_price,
                fee=0.0,
                status="filled",
                reason=f"simulated {exit_reason} exit",
                timestamp=exit_ts,
                venue=venue,
                raw={
                    "qty": qty,
                    "fee_paid": 0.0,
                    "slippage_paid": 0.0,
                    "exit_reason": exit_reason,
                    "entry_price": entry_price,
                    "exit_bar_index": exit_bar_index,
                },
            )
            gross = (exit_price - entry_price) * qty if is_long else (entry_price - exit_price) * qty
            pnl = gross - fee
            return TradeRecord(
                entry=entry_fill,
                exit=exit_fill,
                status="filled",
                exit_reason=exit_reason,
                exit_bar_index=exit_bar_index,
                pnl_quote=pnl,
            )

        # Still open at end of series — exit fill is unfilled.
        open_exit = _build_fill(
            intent_id=intent_id,
            order_id="",
            pair=pair,
            side="sell" if is_long else "buy",
            order_type="",
            requested_volume=qty,
            filled_volume=0.0,
            fill_price=None,
            cost_quote=None,
            fee=0.0,
            status="open",
            reason="no stop/target touched by end of series",
            timestamp=entry_ts,
            venue=venue,
            raw={
                "qty": qty,
                "fee_paid": 0.0,
                "slippage_paid": 0.0,
                "exit_reason": "none",
                "entry_price": entry_price,
            },
        )
        return TradeRecord(
            entry=entry_fill,
            exit=open_exit,
            status="open",
            exit_reason="none",
            exit_bar_index=None,
            pnl_quote=None,
        )


def _stdev(values: list[float]) -> float:
    """Sample standard deviation (Bessel's ``n-1``). Returns ``0.0`` for fewer than 2 values.

    Pure-Python (no numpy/pandas) so the metrics path stays lightweight and
    import-cheap; the backtest engine must not pull a heavy dataframe stack
    just to compute Sharpe.
    """
    m = len(values)
    if m < 2:
        return 0.0
    mean = sum(values) / m
    return (sum((v - mean) ** 2 for v in values) / (m - 1)) ** 0.5


def compute(
    trades: list[dict[str, Any]],
    equity_curve: list[float],
    risk_free_rate: float = 0.0,
    *,
    periods_per_year: int = 365,
) -> dict[str, Any]:
    """Compute per-strategy metrics from a trade list and an equity curve.

    Returns a dict with keys in canonical insertion order: ``trade_count``,
    ``total_return``, ``annualized_return``, ``sharpe``, ``sortino``,
    ``max_drawdown``, ``profit_factor``, ``average_trade``.

    Empty-input contract: when ``equity_curve`` is empty the curve carries no
    information, so the function returns the all-zero shape below (no ``inf``,
    no ``nan``)::

        {"trade_count": 0, "total_return": 0.0, "annualized_return": 0.0,
         "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
         "profit_factor": 0.0, "average_trade": 0.0}

    A strategy that fired no trades (``trades=[]``) but has a non-empty equity
    curve (e.g. the buy-and-hold benchmark) still gets the curve-derived
    metrics (``total_return``, ``annualized_return``, ``sharpe``, ``sortino``,
    ``max_drawdown``); only the trade-derived fields collapse to zero
    (``trade_count=0``, ``profit_factor=0.0``, ``average_trade=0.0``). This is
    what lets the benchmark report a meaningful return and Sharpe alongside a
    strategy's, while an empty-trade strategy is reported as "0 trades" rather
    than producing a misleading Sharpe.

    Metric notes:

      * ``total_return`` — ``(equity_curve[-1] - equity_curve[0]) / equity_curve[0]``
        when the curve has >= 2 points and a non-zero base; ``0.0`` otherwise
        (a cumulative-P&L curve starting at 0.0 has no defined return-on-base).
      * ``annualized_return`` — geometric compounding:
        ``(1 + total_return) ** (periods_per_year / (n - 1)) - 1`` where
        ``n = len(equity_curve)``. Returns ``0.0`` when ``n < 2`` or
        ``total_return`` is ``0.0``.
      * ``sharpe`` — ``mean(daily_returns) / stdev(daily_returns) * sqrt(p)``.
        Daily returns are ``equity_curve[i] / equity_curve[i-1] - 1``. A series
        with fewer than two daily returns, or zero variance, returns ``0.0``
        (a single-trade series has no variance -> Sharpe = 0). A zero base
        (``equity_curve[i-1] == 0``) yields a ``0.0`` return to avoid
        division-by-zero on P&L curves that start at 0.0.
      * ``sortino`` — same numerator, downside deviation (stdev of the
        negative returns only) as the denominator. Returns ``0.0`` when there
        are fewer than two daily returns, no negative returns, or zero
        downside variance.
      * ``max_drawdown`` — largest peak-to-trough drop of the curve. A
        monotonically non-decreasing curve has no drawdown -> ``0.0``. Points
        where the running peak is ``<= 0`` contribute ``0.0`` (drawdown from a
        non-positive base is undefined).
      * ``profit_factor`` — ``sum(positive pnl) / abs(sum(negative pnl))``.
        ``None`` ``pnl_quote`` values are treated as 0 (excluded from the
        sums). When there are no losing trades the denominator is 0; if there
        is at least one positive pnl the function returns ``float("inf")`` as
        a sentinel (``json.dumps(..., allow_nan=True)`` serializes it as
        ``Infinity`` — stable across runs but non-strict JSON); if there are
        no numeric pnls at all it returns ``0.0``.
      * ``average_trade`` — mean of the non-``None`` ``pnl_quote`` values;
        ``0.0`` when no trade has a numeric pnl.

    ``risk_free_rate`` is subtracted from the mean per-period return before
    scaling (treated as a per-period rate; default ``0.0`` leaves the formula
    as ``mean / stdev * sqrt(p)``). ``periods_per_year`` defaults to 365 (daily
    bars / 24-7 crypto convention).
    """
    if not equity_curve:
        return {
            "trade_count": 0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "average_trade": 0.0,
        }

    n = len(equity_curve)
    trade_count = len(trades)

    # total_return: guard the zero base (a P&L curve starting at 0.0 has no
    # defined return-on-base; dividing would raise / produce inf).
    if n >= 2 and equity_curve[0] != 0:
        total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
    else:
        total_return = 0.0

    # annualized_return: geometric compounding from total_return.
    if n < 2 or total_return == 0.0:
        annualized_return = 0.0
    else:
        annualized_return = (1 + total_return) ** (periods_per_year / (n - 1)) - 1

    # Daily returns; a zero base yields 0.0 to avoid division-by-zero on P&L
    # curves that start flat at 0.0.
    daily_returns: list[float] = []
    for i in range(1, n):
        prev = equity_curve[i - 1]
        daily_returns.append(equity_curve[i] / prev - 1 if prev != 0 else 0.0)

    # sharpe: mean / stdev * sqrt(p); 0.0 when variance is absent.
    if len(daily_returns) < 2:
        sharpe = 0.0
    else:
        sd = _stdev(daily_returns)
        if sd == 0:
            sharpe = 0.0
        else:
            mean_r = sum(daily_returns) / len(daily_returns)
            sharpe = (mean_r - risk_free_rate) / sd * math.sqrt(periods_per_year)

    # sortino: mean / downside-deviation * sqrt(p); downside = stdev of the
    # negative returns only. 0.0 when there are no negative returns or the
    # downside variance is zero (incl. a single negative return).
    if len(daily_returns) < 2:
        sortino = 0.0
    else:
        neg = [r for r in daily_returns if r < 0]
        if not neg:
            sortino = 0.0
        else:
            dd = _stdev(neg)
            if dd == 0:
                sortino = 0.0
            else:
                mean_r = sum(daily_returns) / len(daily_returns)
                sortino = (mean_r - risk_free_rate) / dd * math.sqrt(periods_per_year)

    # max_drawdown: largest (peak - v) / peak; 0.0 for a monotonic rise.
    max_drawdown = 0.0
    peak = equity_curve[0]
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_drawdown:
                max_drawdown = dd

    # profit_factor + average_trade from pnl_quote (None -> treated as 0).
    pnls = [t.get("pnl_quote") for t in trades if t.get("pnl_quote") is not None]
    pos = sum(p for p in pnls if p > 0)
    neg = sum(p for p in pnls if p < 0)
    if not pnls:
        profit_factor = 0.0
    elif neg == 0:
        profit_factor = float("inf") if pos > 0 else 0.0
    else:
        profit_factor = pos / abs(neg)
    average_trade = sum(pnls) / len(pnls) if pnls else 0.0

    return {
        "trade_count": trade_count,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "profit_factor": profit_factor,
        "average_trade": average_trade,
    }


def buy_and_hold_benchmark(
    candles: Candles,
    warmup: int,
    *,
    fee_bps: int = 26,
    slippage_bps: int = 2,
) -> list[float]:
    """Buy-and-hold equity curve: one unit bought at the entry open and held.

    Buys one unit at ``candles[warmup + 1].open`` — the same bar the
    strategy's earliest possible fill occurs (the strategy decides at bar ``t``
    and fills at ``candles[t + 1].open``) — applying the same worst-case
    slippage + fee rule :class:`FillSimulator` uses for a long entry::

        entry_price = open * (1 + slippage_bps / 10_000)
        cost_basis  = entry_price * (1 + fee_bps / 10_000)

    The returned curve is the portfolio's mark-to-market value over time::

        curve[0] = cost_basis                              # capital invested at entry
        curve[t] = candles[(warmup + 1) + t - 1].close    # mark-to-market, t >= 1

    So ``curve[0]`` is what you paid (open + slippage + fee) and every later
    point is the close of one bar from ``warmup + 1`` to the end. The
    :func:`compute` ``total_return`` derived from this curve is
    ``(last_close - cost_basis) / cost_basis`` — it carries the entry cost, so
    the benchmark is directly comparable to a strategy that pays the same
    fee + slippage on its fills.

    Returns ``[]`` when ``len(candles) <= warmup + 1`` (no bar to hold
    over). Otherwise returns ``len(candles) - warmup`` floats: the cost basis
    followed by one close per bar from ``warmup + 1`` to the end. The runtime
    candle shape is ``[ts, o, h, l, c, v]`` so ``candles[t][1]`` is the open
    and ``candles[t][4]`` is the close.
    """
    if len(candles) <= warmup + 1:
        return []
    entry_bar = warmup + 1
    entry_open = float(candles[entry_bar][1])
    entry_price = entry_open * (1 + slippage_bps / 10_000)
    cost_basis = entry_price * (1 + fee_bps / 10_000)
    curve = [cost_basis]
    for t in range(entry_bar, len(candles)):
        curve.append(float(candles[t][4]))
    return curve
