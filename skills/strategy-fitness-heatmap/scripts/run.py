#!/usr/bin/env python3
"""strategy-fitness-heatmap - cross-strategy x ticker backtest fitness matrix.

DEFAULT reads the nightly backtest pipeline's artifacts from disk (zero
network, ~instant):

  - ``fitness_matrix.json`` — the precomputed Sharpe matrix per interval
    (``1d``, ``4h``). Its own ``tickers`` / ``strategies`` lists and cell
    values (including JSON ``null`` cells) are emitted verbatim - the file is
    the source of truth; the registry and watchlist are not consulted.
  - ``runs.jsonl`` — the LATEST run (last non-empty line) enriches each
    combo's ``details`` entry with the stored metrics (trades, Sharpe,
    profit factor, max drawdown, benchmark, bars, provider,
    insufficient_data).

The artifact directory resolves from
``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR``, defaulting to the repo-relative
``data/backtest-nightly/``.

``--fresh`` opts into the expensive recompute: loops over every L3 strategy
in ``analysis.registry.l3_strategies()`` x every T1+T2 ticker from the
market-watchlist baskets ``tier_1`` and ``tier_2``, runs the backtest-engine
walk-forward replay + FillSimulator + metrics for each combo, and emits a
Sharpe-ratio fitness matrix per interval (``1d``, ``4h``). Explicit
``--tickers`` / ``--strategies`` overrides also force the fresh grid (they
select recompute inputs). When the nightly artifacts are missing or
unreadable the skill automatically degrades to the fresh grid and surfaces a
note in ``errors`` / ``help``.

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
    uv run skills/strategy-fitness-heatmap/scripts/run.py --json --fresh
    uv run skills/strategy-fitness-heatmap/scripts/run.py --json \\
        --tickers BTCUSD ETHUSD --strategies strategy-trend-follow
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from analysis.data import fetch_ohlc
from analysis.output import emit_envelope_json, parse_axi_flags, resolve_fields
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

# ── Nightly artifacts (same contract as backtest-trend-miner) ──────

# skills/strategy-fitness-heatmap/scripts/run.py -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_DEFAULT_OUT_DIRNAME = Path("data") / "backtest-nightly"

ENV_OUT_DIR = "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR"

_SEPARATOR = "\u00d7"  # × (U+00D7 MULTIPLICATION SIGN)

_ERR_MATRIX_MISSING = "nightly fitness_matrix.json not found — falling back to fresh grid"
_ERR_MATRIX_UNREADABLE = "nightly fitness_matrix.json unreadable — falling back to fresh grid"
_ERR_RUNS_MISSING = "nightly runs.jsonl not found or empty — falling back to fresh grid"

_HELP_LINES: list[str] = [
    "uv run skills/strategy-fitness-heatmap/scripts/run.py --json",
    "uv run skills/strategy-fitness-heatmap/scripts/run.py --json --tickers BTCUSD ETHUSD",
    "uv run skills/strategy-fitness-heatmap/scripts/run.py --help",
]

_HELP_NIGHTLY: list[str] = [
    "Default source: nightly backtest-pipeline artifacts (fitness_matrix.json + latest runs.jsonl run) - zero network.",
    f"Artifact dir: {ENV_OUT_DIR} env var, defaulting to the repo-relative data/backtest-nightly/.",
    "Pass --fresh to recompute the grid live (walk-forward + FillSimulator); --tickers/--strategies also force a fresh run.",
]

_HELP_FALLBACK = (
    "Nightly artifacts missing: run the backtest-pipeline to regenerate them, or set "
    f"{ENV_OUT_DIR} to the directory containing fitness_matrix.json and runs.jsonl."
)


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


# ── Nightly artifact readers (mirror backtest-trend-miner) ─────────


def _resolve_out_dir() -> Path:
    """Resolve the pipeline output directory.

    ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR`` wins; otherwise fall back to
    the repo-relative ``data/backtest-nightly/`` default.
    """
    env = os.environ.get(ENV_OUT_DIR)
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / _DEFAULT_OUT_DIRNAME


def _load_fitness_matrix(out_dir: Path) -> dict[str, Any] | None:
    """Load + structurally validate ``fitness_matrix.json``.

    Returns ``None`` when the file is absent, unreadable, or malformed
    (missing/bad ``intervals`` or ``generated_at``, ragged ``values`` grid) -
    the caller degrades to the fresh grid rather than crashing.
    """
    path = out_dir / "fitness_matrix.json"
    if not path.is_file():
        return None
    try:
        with path.open() as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    intervals = data.get("intervals")
    if not isinstance(intervals, dict) or not intervals:
        return None
    if not isinstance(data.get("generated_at"), str):
        return None
    for iv in intervals.values():
        if not isinstance(iv, dict):
            return None
        tickers = iv.get("tickers")
        strategies = iv.get("strategies")
        values = iv.get("values")
        if not isinstance(tickers, list) or not isinstance(strategies, list) or not isinstance(values, list):
            return None
        if len(values) != len(tickers):
            return None
        for row in values:
            if not isinstance(row, list) or len(row) != len(strategies):
                return None
    return data


def _load_runs(out_dir: Path) -> list[dict[str, Any]]:
    """Parse ``runs.jsonl`` (one JSON object per line, oldest first), skipping malformed lines."""
    runs_file = out_dir / "runs.jsonl"
    runs: list[dict[str, Any]] = []
    if not runs_file.is_file():
        return runs
    try:
        with runs_file.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    runs.append(obj)
    except OSError:
        return runs
    return runs


def _nightly_detail(
    interval: str,
    strategy: str,
    ticker: str,
    cell: float | None,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one ``details`` entry from the latest nightly run.

    ``result`` is the combo's record from the latest ``runs.jsonl`` run keyed
    ``"{interval}×{strategy}×{ticker}"`` (or ``None`` when the run has no
    record for the combo, or the value is not a dict). The stored metrics are
    mapped onto the fresh-path detail field names (``sharpe`` /
    ``trade_count`` / ``bars`` / ...) and the ``metrics`` / ``benchmark``
    dicts carry what the nightly stored - nothing beyond that is fabricated.
    ``sharpe`` mirrors the matrix cell: the pipeline excludes insufficient
    combos from ``fitness_matrix.json`` (cell stays JSON ``null``), so a
    combo flagged ``insufficient_data`` emits ``sharpe`` ``null`` too, not
    the stored placeholder ``0.0``. ``error`` stays ``null`` unless the
    nightly flagged the combo ``insufficient_data`` or the combo has no
    record at all.
    """
    if not isinstance(result, dict):
        result = None
    if result is None:
        return {
            "ticker": ticker,
            "strategy": strategy,
            "interval": interval,
            "sharpe": cell,
            "trade_count": None,
            "trades": None,
            "profit_factor": None,
            "max_dd": None,
            "benchmark_sharpe": None,
            "bars": None,
            "provider": None,
            "insufficient_data": None,
            "metrics": {},
            "benchmark": {},
            "error": "no result in latest nightly run",
        }
    insufficient = bool(result.get("insufficient_data"))
    # Nightly matrix cell for an insufficient combo is JSON null (excluded from
    # the matrix), so the detail sharpe mirrors null, not the stored 0.0.
    # Rich metric fields (trade_count/bars/provider) stay populated from the
    # stored run record.
    sharpe = None if insufficient else result.get("strategy_sharpe")
    return {
        "ticker": ticker,
        "strategy": strategy,
        "interval": interval,
        "sharpe": sharpe,
        "strategy_sharpe": sharpe,
        "trade_count": result.get("trades"),
        "trades": result.get("trades"),
        "profit_factor": result.get("strategy_profit_factor"),
        "max_dd": result.get("strategy_max_dd"),
        "benchmark_sharpe": result.get("benchmark_sharpe"),
        "bars": result.get("bars"),
        "provider": result.get("provider"),
        "insufficient_data": insufficient,
        "asof": result.get("asof"),
        "ideas": result.get("ideas"),
        "windows": result.get("windows"),
        "strategy_total_return": result.get("strategy_total_return"),
        "benchmark_total_return": result.get("benchmark_total_return"),
        "metrics": {
            "trade_count": result.get("trades"),
            "total_return": result.get("strategy_total_return"),
            "sharpe": sharpe,
            "max_drawdown": result.get("strategy_max_dd"),
            "profit_factor": result.get("strategy_profit_factor"),
        },
        "benchmark": {
            "sharpe": result.get("benchmark_sharpe"),
            "total_return": result.get("benchmark_total_return"),
        },
        "error": "insufficient_data" if insufficient else None,
    }


def _build_nightly_payload(matrix_doc: dict[str, Any], latest_run: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Build the AXI ``data`` payload from the nightly artifacts.

    The matrix (``tickers`` / ``strategies`` / ``values``, including JSON
    ``null`` cells) is emitted exactly as read from ``fitness_matrix.json``;
    ``details`` are enriched from the latest ``runs.jsonl`` run. The file's
    own ticker/strategy lists are the source of truth - the registry and the
    watchlist are not consulted. Returns ``(payload, count)`` where ``count``
    is the total combo count across all intervals in the file.
    """
    run_results = latest_run.get("results")
    if not isinstance(run_results, dict):
        run_results = {}

    intervals_payload: dict[str, Any] = {}
    tickers_flat: list[str] = []
    strategies_flat: list[str] = []
    count = 0

    for interval, iv in matrix_doc["intervals"].items():
        tickers = iv["tickers"]
        strategies = iv["strategies"]
        values = iv["values"]
        details = [
            _nightly_detail(
                interval,
                strategy,
                ticker,
                values[ti][si],
                run_results.get(f"{interval}{_SEPARATOR}{strategy}{_SEPARATOR}{ticker}"),
            )
            for ti, ticker in enumerate(tickers)
            for si, strategy in enumerate(strategies)
        ]
        intervals_payload[interval] = {
            "matrix": {"tickers": list(tickers), "strategies": list(strategies), "values": values},
            "details": details,
        }
        count += len(tickers) * len(strategies)
        for t in tickers:
            if t not in tickers_flat:
                tickers_flat.append(t)
        for s in strategies:
            if s not in strategies_flat:
                strategies_flat.append(s)

    payload = {
        "intervals": intervals_payload,
        "strategies": strategies_flat,
        "tickers": tickers_flat,
        "config": {
            "source": "nightly",
            "env_var": ENV_OUT_DIR,
            "generated_at": matrix_doc.get("generated_at"),
            "run_ts": latest_run.get("ts"),
        },
    }
    return payload, count


# ── Fresh grid (opt-in recompute / missing-artifact fallback) ───────


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


def _build_parser() -> argparse.ArgumentParser:
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
        "--fresh",
        dest="fresh",
        action="store_true",
        help="Recompute the grid live (walk-forward + FillSimulator) instead of reading the nightly artifacts.",
    )
    parser.add_argument(
        "--tickers",
        dest="tickers",
        nargs="*",
        default=None,
        help="Override the ticker list (implies --fresh; default: tier_1 + tier_2 from the watchlist).",
    )
    parser.add_argument(
        "--strategies",
        dest="strategies",
        nargs="*",
        default=None,
        help="Override the strategy list (implies --fresh; default: all L3 strategies from the registry).",
    )
    return parser


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _print_human(payload: dict[str, Any]) -> None:
    """Print a human-readable summary of the fitness matrices."""
    print("strategy-fitness-heatmap")
    config = payload.get("config") or {}
    if config.get("source") == "nightly":
        print(f"source: nightly matrix (generated_at={config.get('generated_at')}, run_ts={config.get('run_ts')})")
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
            cells = []
            for j in range(len(strategies)):
                v = values[i][j]
                cells.append(f"{v:{col_w}.4f}" if isinstance(v, (int, float)) else "-".rjust(col_w))
            row = t[:16].ljust(16) + "".join(cells)
            print(row)
    print("\nuse --json for the full AXI envelope (matrices + per-combo details)")


def main() -> None:
    raw_argv = sys.argv[1:]
    if "-h" in raw_argv or "--help" in raw_argv:
        # parse_axi_flags intercepts -h/--help with the generic AXI usage,
        # which implies a positional TICKER this skill does not accept -
        # show this skill's own flags instead.
        print(_build_parser().format_help())
        sys.exit(0)
    fields_arg, full, toon, _from_state, _ttl, filtered_argv = parse_axi_flags(raw_argv)
    args = _parse_argv(filtered_argv)

    # Explicit grid overrides select fresh-recompute inputs, so they force the
    # fresh path exactly like --fresh does.
    wants_fresh = args.fresh or args.tickers is not None or bool(args.strategies)

    errors: list[str] = []
    help_lines = list(_HELP_LINES)
    payload: dict[str, Any] | None = None
    count = 0
    fallback = False

    if not wants_fresh:
        # Default: read the nightly artifacts (zero network). Any missing or
        # unreadable artifact degrades to the fresh grid below.
        out_dir = _resolve_out_dir()
        matrix_doc = _load_fitness_matrix(out_dir)
        runs = _load_runs(out_dir)
        if matrix_doc is None:
            errors.append(
                _ERR_MATRIX_UNREADABLE if (out_dir / "fitness_matrix.json").is_file() else _ERR_MATRIX_MISSING
            )
            fallback = True
        elif not runs:
            errors.append(_ERR_RUNS_MISSING)
            fallback = True
        else:
            payload, count = _build_nightly_payload(matrix_doc, runs[-1])
            detail_errors = [
                d["error"] for iv_data in payload["intervals"].values() for d in iv_data["details"] if d["error"]
            ]
            errors = list(dict.fromkeys(detail_errors))
            help_lines.extend(_HELP_NIGHTLY)

    if fallback:
        help_lines.append(_HELP_FALLBACK)

    if payload is None:
        # Fresh grid: opt-in via --fresh (or --tickers/--strategies), or the
        # automatic fallback when the nightly artifacts are missing/unreadable.
        strategies = args.strategies if args.strategies else l3_strategies()
        tickers = args.tickers if args.tickers is not None else _default_tickers()

        bt_lib = load_skill("backtest-engine")
        if bt_lib is None:
            if args.json:
                emit_envelope_json(
                    None,
                    count=0,
                    errors=[*errors, "backtest-engine skill not found"],
                    help=help_lines,
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
        errors.extend(dict.fromkeys(combo_errors))

        payload = {
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
        emit_envelope_json(
            payload,
            count=count,
            errors=errors,
            help=help_lines,
            fields=resolve_fields(fields_arg, full=full, default=None),
            toon=toon,
        )
    else:
        # Human mode has no envelope to carry errors/help - surface the
        # fallback note and any per-combo errors on stderr so a
        # fallback-triggered network recompute is never silent.
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        if fallback:
            print(_HELP_FALLBACK, file=sys.stderr)
        _print_human(payload)
    sys.exit(0)


if __name__ == "__main__":
    main()
