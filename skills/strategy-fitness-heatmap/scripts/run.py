#!/usr/bin/env python3
"""strategy-fitness-heatmap - cross-strategy x ticker backtest fitness matrix.

Loops over every L3 strategy in ``analysis.registry.l3_strategies()`` x every
T1+T2 ticker from the market-watchlist baskets ``tier_1`` and ``tier_2``, runs
the backtest-engine walk-forward replay + FillSimulator + metrics for each
combo, and emits a Sharpe-ratio fitness matrix per interval (``1d``, ``4h``).

Candles are fetched once per (ticker, interval) and reused across all
strategies so the per-ticker network cost is paid twice (one per interval),
not once per combo. The FillSimulator is deterministic (next-bar-open entry,
stop-first intrabar tie, fixed fee + slippage), so two runs over the same
candles produce identical Sharpe values.

Output (AXI envelope, ``--json``):

    data.intervals.1d.matrix  = {tickers, strategies, values}
    data.intervals.4h.matrix  = {tickers, strategies, values}
    data.intervals.1d.details = [per-combo metrics, ...]
    data.intervals.4h.details = [per-combo metrics, ...]

``values`` is ``rows=tickers, cols=strategies, cells=Sharpe``.

Usage:
    uv run skills/strategy-fitness-heatmap/scripts/run.py --json
    uv run skills/strategy-fitness-heatmap/scripts/run.py --json \\
        --tickers BTCUSD ETHUSD --strategies strategy-trend-follow
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from analysis.data import fetch_ohlc
from analysis.output import emit_envelope_json
from analysis.registry import l3_strategies
from analysis.skill_loader import load_skill
from analysis.watchlist import by_category

# Per-interval backtest config: (period, warmup, periods_per_year).
#   1d  -> 2y lookback, 200-bar warmup, 365 periods/year (daily bars, 24/7 crypto).
#   4h  -> 1y lookback, 500-bar warmup, 2190 periods/year (6 bars/day x 365).
# periods_per_year drives the Sharpe annualization in bt.compute; using the
# bar-aware count keeps 4h Sharpes comparable to 1d in absolute terms.
_INTERVAL_CONFIG: dict[str, tuple[str, int, int]] = {
    "1d": ("2y", 200, 365),
    "4h": ("1y", 500, 2190),
}

_INTERVALS: list[str] = ["1d", "4h"]

_BASE_CAPITAL: float = 100_000.0
_FEE_BPS: int = 26
_SLIPPAGE_BPS: int = 2
_QTY: float = 1.0

# Matches bt.compute's empty-input contract (no inf, no nan).
_ZERO_METRICS: dict[str, Any] = {
    "trade_count": 0,
    "total_return": 0.0,
    "annualized_return": 0.0,
    "sharpe": 0.0,
    "sortino": 0.0,
    "max_drawdown": 0.0,
    "profit_factor": 0.0,
    "average_trade": 0.0,
}

_HELP_LINES: list[str] = [
    "uv run skills/strategy-fitness-heatmap/scripts/run.py --json",
    "uv run skills/strategy-fitness-heatmap/scripts/run.py --json --tickers BTCUSD ETHUSD",
    "uv run skills/strategy-fitness-heatmap/scripts/run.py --help",
]


def _default_tickers() -> list[str]:
    """T1 + T2 tickers from the watchlist (``tier_1`` + ``tier_2`` baskets).

    Dedupes while preserving insertion order (tier_1 first, then tier_2).
    Returns ``[]`` when the watchlist file or those baskets are absent - the
    caller is expected to pass ``--tickers`` explicitly in that case.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in by_category("tier_1") + by_category("tier_2"):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _build_equity_curve(records: list[dict[str, Any]], warmup: int, n_bars: int) -> list[float]:
    """Cumulative realized P&L equity curve anchored at ``_BASE_CAPITAL``.

    Replicates the backtest-engine ``_run_metrics`` convention: one point per
    bar from ``warmup`` to the last bar,
    ``equity_curve[t] = base_capital + sum(pnl_quote)`` for trades whose
    ``exit_bar_index <= t``. Open trades (``status == "open"``) are excluded -
    only realized P&L is counted. Returns ``[_BASE_CAPITAL]`` when no trade has
    a non-``None`` pnl.
    """
    pnl_by_bar: dict[int, float] = {}
    for r in records:
        exit_bar = r.get("exit_bar_index")
        pnl = r.get("pnl_quote")
        if exit_bar is not None and pnl is not None:
            pnl_by_bar[exit_bar] = pnl_by_bar.get(exit_bar, 0.0) + pnl
    if not pnl_by_bar:
        return [_BASE_CAPITAL]
    cum = 0.0
    curve: list[float] = []
    for t in range(warmup, n_bars):
        cum += pnl_by_bar.get(t, 0.0)
        curve.append(_BASE_CAPITAL + cum)
    return curve


def _run_strategy(
    bt_lib,
    strategy,
    ticker: str,
    candles: list[list],
    *,
    warmup: int,
    interval: str,
    period: str,
    periods_per_year: int,
) -> tuple[dict[str, Any], str | None]:
    """Walk-forward + fill sim + metrics for one (strategy, ticker) combo.

    Returns ``(strategy_metrics, error)``. On any exception (e.g.
    ``strategy.analyze`` raising on a particular candle prefix) the metrics
    collapse to the all-zero shape and ``error`` carries the exception text.
    """
    if not candles or len(candles) <= warmup:
        return dict(_ZERO_METRICS), "insufficient candles"
    try:
        runner = bt_lib.WalkForwardRunner()
        windows = runner.run(
            strategy,
            ticker,
            candles,
            warmup=warmup,
            interval=interval,
            period=period,
        )
        sim = bt_lib.FillSimulator(fee_bps=_FEE_BPS, slippage_bps=_SLIPPAGE_BPS)
        ctx = {"qty": _QTY}
        records = [sim.simulate(w["idea"], candles, w["bar_index"], ctx) for w in windows if w["idea"]]
        curve = _build_equity_curve(records, warmup, len(candles))
        metrics = bt_lib.compute(records, curve, periods_per_year=periods_per_year)
        return metrics, None
    except Exception as e:  # record per-combo and continue
        return dict(_ZERO_METRICS), f"{type(e).__name__}: {e}"


def _benchmark_for(
    bt_lib,
    candles: list[list],
    *,
    warmup: int,
    periods_per_year: int,
) -> dict[str, Any]:
    """Buy-and-hold benchmark metrics for one (ticker, interval) candle set.

    The benchmark only depends on the candles, not the strategy, so it is
    computed once per ticker and reused across all strategies. Returns the
    all-zero shape when there are too few candles to hold over.
    """
    if not candles or len(candles) <= warmup:
        return dict(_ZERO_METRICS)
    bench_curve = bt_lib.buy_and_hold_benchmark(candles, warmup, fee_bps=_FEE_BPS, slippage_bps=_SLIPPAGE_BPS)
    return bt_lib.compute([], bench_curve, periods_per_year=periods_per_year)


def _run_interval(
    bt_lib,
    strategies: list[str],
    tickers: list[str],
    *,
    interval: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run all (ticker, strategy) combos for one interval.

    Fetches candles once per ticker and reuses them across all strategies.
    The matrix is ``rows=tickers, cols=strategies, cells=Sharpe``. Details
    carry the full per-combo metrics dict + benchmark for deeper inspection.
    """
    period, warmup, ppy = _INTERVAL_CONFIG[interval]
    values: list[list[float]] = [[0.0] * len(strategies) for _ in tickers]
    details: list[dict[str, Any]] = []

    for ti, ticker in enumerate(tickers):
        candles = fetch_ohlc(ticker, interval=interval, period=period)
        benchmark_metrics = _benchmark_for(bt_lib, candles, warmup=warmup, periods_per_year=ppy)
        no_data = not candles

        for si, sname in enumerate(strategies):
            strategy = load_skill(sname)
            if strategy is None:
                values[ti][si] = 0.0
                details.append(
                    {
                        "ticker": ticker,
                        "strategy": sname,
                        "interval": interval,
                        "sharpe": 0.0,
                        "metrics": dict(_ZERO_METRICS),
                        "benchmark": benchmark_metrics,
                        "trade_count": 0,
                        "bars": len(candles),
                        "error": f"strategy {sname!r} not found",
                    }
                )
                continue

            if no_data:
                # fetch_ohlc returned empty -> all strategy metrics 0.0, continue.
                values[ti][si] = 0.0
                details.append(
                    {
                        "ticker": ticker,
                        "strategy": sname,
                        "interval": interval,
                        "sharpe": 0.0,
                        "metrics": dict(_ZERO_METRICS),
                        "benchmark": benchmark_metrics,
                        "trade_count": 0,
                        "bars": 0,
                        "error": "fetch_ohlc returned no candles",
                    }
                )
                continue

            smetrics, err = _run_strategy(
                bt_lib,
                strategy,
                ticker,
                candles,
                warmup=warmup,
                interval=interval,
                period=period,
                periods_per_year=ppy,
            )
            sharpe = float(smetrics.get("sharpe", 0.0))
            values[ti][si] = sharpe
            details.append(
                {
                    "ticker": ticker,
                    "strategy": sname,
                    "interval": interval,
                    "sharpe": sharpe,
                    "metrics": smetrics,
                    "benchmark": benchmark_metrics,
                    "trade_count": int(smetrics.get("trade_count", 0)),
                    "bars": len(candles),
                    "error": err,
                }
            )

    matrix = {"tickers": list(tickers), "strategies": list(strategies), "values": values}
    return matrix, details


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="strategy-fitness-heatmap",
        description="Cross-strategy x ticker backtest fitness matrix (Sharpe per combo).",
    )
    parser.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Emit the AXI envelope {data, count, errors, help} (machine output).",
    )
    parser.add_argument(
        "--tickers",
        dest="tickers",
        nargs="*",
        default=None,
        help="Override the ticker list (default: tier_1 + tier_2 from the watchlist).",
    )
    parser.add_argument(
        "--strategies",
        dest="strategies",
        nargs="*",
        default=None,
        help="Override the strategy list (default: all L3 strategies from the registry).",
    )
    return parser.parse_args(argv)


def _print_human(payload: dict[str, Any]) -> None:
    """Print a human-readable summary of the fitness matrices."""
    print("strategy-fitness-heatmap")
    for interval, iv_data in payload["intervals"].items():
        matrix = iv_data["matrix"]
        tickers = matrix["tickers"]
        strategies = matrix["strategies"]
        values = matrix["values"]
        print(f"\n--- interval={interval} (Sharpe, rows=tickers, cols=strategies) ---")
        col_w = 16
        header = "ticker".ljust(16) + "".join(s[:col_w].ljust(col_w) for s in strategies)
        print(header)
        for i, t in enumerate(tickers):
            row = t[:16].ljust(16) + "".join(f"{values[i][j]:{col_w}.4f}" for j in range(len(strategies)))
            print(row)
    print("\nuse --json for the full AXI envelope (matrices + per-combo details)")


def main() -> None:
    args = _parse_argv(sys.argv[1:])

    strategies = args.strategies if args.strategies else l3_strategies()
    tickers = args.tickers if args.tickers is not None else _default_tickers()

    bt_lib = load_skill("backtest-engine")
    if bt_lib is None:
        if args.json:
            emit_envelope_json(
                None,
                count=0,
                errors=["backtest-engine skill not found"],
                help=_HELP_LINES,
            )
        else:
            print("error: backtest-engine skill not found", file=sys.stderr)
        sys.exit(2)

    intervals_payload: dict[str, Any] = {}
    for interval in _INTERVALS:
        matrix, details = _run_interval(bt_lib, strategies, tickers, interval=interval)
        intervals_payload[interval] = {"matrix": matrix, "details": details}

    # Surface per-combo errors at the envelope level (deduped, order-preserving).
    combo_errors = [d["error"] for iv_data in intervals_payload.values() for d in iv_data["details"] if d["error"]]
    errors: list[str] = list(dict.fromkeys(combo_errors))

    payload: dict[str, Any] = {
        "intervals": intervals_payload,
        "strategies": list(strategies),
        "tickers": list(tickers),
        "config": {
            "base_capital": _BASE_CAPITAL,
            "fee_bps": _FEE_BPS,
            "slippage_bps": _SLIPPAGE_BPS,
            "qty": _QTY,
            "intervals": {
                iv: {"period": p, "warmup": w, "periods_per_year": y} for iv, (p, w, y) in _INTERVAL_CONFIG.items()
            },
        },
    }

    count = len(strategies) * len(tickers) * len(_INTERVALS)

    if args.json:
        emit_envelope_json(payload, count=count, help=_HELP_LINES, errors=errors)
    else:
        _print_human(payload)
    sys.exit(0)


if __name__ == "__main__":
    main()
