#!/usr/bin/env python3
"""backtest-engine — walk-forward replay CLI.

Bead bt-1 scope: dry-run replay only — fetches candles, walks an L3 strategy
bar-by-bar, and prints idea counts.

Bead bt-2 scope: ``--fill-sim`` runs the deterministic
:class:`~skills.backtest-engine.lib.FillSimulator` over each fired idea and
prints a per-trade summary (entry/exit price, exit reason, realized P&L).
Default OFF — ``--dry-run`` (bt-1) behavior is unchanged when the flag is absent.

Bead bt-4 scope: ``--metrics`` (requires ``--fill-sim``) prints per-strategy
metrics (total/annualized return, Sharpe, Sortino, max drawdown, profit factor,
average trade, trade count) plus a buy-and-hold benchmark, as stable
``sort_keys`` JSON. Default OFF — ``--fill-sim`` output is unchanged without it.

Bead bt-5 scope: the canonical CLI form is
``--strategy <name> --ticker <provider:ticker> --interval <tf>`` with
``--from``/``--to`` as post-fetch date filters, ``--json`` for the AXI envelope,
and ``--forensic-drill <BAR>`` to print the per-bar decision, fill, and a
preliminary risk verdict for one chosen bar (audit-trail reconstruction). The
positional ``STRATEGY TICKER INTERVAL`` form still works (backwards compat); an
optional flag overrides its positional counterpart.

Risk-engine wiring (bt-3) is forthcoming; the forensic drill's risk verdict is
derived locally from the idea's stop/TP ladder and direction only.

Usage:
    uv run skills/backtest-engine/scripts/run.py \\
        --strategy strategy-trend-follow --ticker DEMO --interval 1d \\
        [--warmup=N] [--bars=N] [--from=YYYY-MM-DD] [--to=YYYY-MM-DD] \\
        [--dry-run] [--demo] [--fill-sim [--fee-bps=26] [--slippage-bps=2]] \\
        [--metrics] [--forensic-drill=BAR] [--json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime

from analysis.contracts import conviction_version
from analysis.data import fetch_ohlc
from analysis.intervals import validate_timeframe
from analysis.output import emit_envelope_json
from analysis.skill_loader import load_lib_for_script, load_skill

# Daily-and-up intervals get a 2y default lookback; intraday gets 1y so the
# yfinance ~60d (sub-hour) / ~2y (hourly) caps don't truncate the series.
_DAILY_PLUS_INTERVALS = frozenset({"1d", "3d", "1wk", "1M"})


def _default_period(interval: str) -> str:
    return "2y" if interval in _DAILY_PLUS_INTERVALS else "1y"


def _make_demo_candles(n: int, seed: int = 42) -> list[list]:
    """Deterministic synthetic OHLC for offline demo (no network).

    The series is a downtrend-then-V-recovery: price declines steadily for the
    first ~72.5% of the series, then reverses sharply upward. This exercises the
    walk-forward replay over a non-trivial regime change — a trend-follow
    strategy can fire at the reversal bar (where the impulse flips from down to
    up) — instead of a flat random walk that never fires. With ``n=200`` the
    reversal lands at bar ~145, so the strategy fires across bars 149-151 (the
    ``--forensic-drill 150`` live repro targets the middle of that window).
    """
    rng = random.Random(seed)
    candles: list[list] = []
    price = 100.0
    decline_until = n * 29 // 40
    for i in range(n):
        if i < decline_until:
            price = price - 0.6 + rng.uniform(-0.05, 0.05)
        else:
            price = price + 3.0 + rng.uniform(-0.05, 0.05)
        candles.append([i * 86400, price, price + 1.0, price - 1.0, price, rng.randint(100000, 500000)])
    return candles


def _parse_iso_to_epoch(s: str, *, end_of_day: bool = False) -> int:
    """Parse an ISO date (``YYYY-MM-DD``) or full ISO 8601 timestamp to Unix seconds.

    Date-only strings are read as UTC midnight; when ``end_of_day`` is True (used
    for the inclusive ``--to`` bound) a date-only string is read as the end of
    that day (23:59:59 UTC) so the bound is inclusive on a daily candle. Naive
    timestamps are assumed UTC.
    """
    dt = datetime.fromisoformat(s)
    is_date_only = "T" not in s
    if is_date_only and end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _slice_candles_by_iso(candles: list[list], from_iso: str | None, to_iso: str | None) -> list[list]:
    """Filter candles by timestamp (``candles[t][0]``, Unix seconds), inclusive both ends.

    Post-fetch slice: the data layer ``fetch_ohlc(ticker, interval, period)`` has
    no ``--from``/``--to`` yet, so the caller fetches the full period then narrows
    the window here. Inclusive on both bounds so a single-day ``--from``/``--to``
    keeps that day's candle.
    """
    lo = _parse_iso_to_epoch(from_iso, end_of_day=False) if from_iso else None
    hi = _parse_iso_to_epoch(to_iso, end_of_day=True) if to_iso else None
    out: list[list] = []
    for c in candles:
        ts = int(c[0])
        if lo is not None and ts < lo:
            continue
        if hi is not None and ts > hi:
            continue
        out.append(c)
    return out


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backtest-engine",
        description="Walk-forward replay of an L3 strategy over OHLC bars (bt-1 dry-run).",
    )
    parser.add_argument(
        "strategy",
        nargs="?",
        default=None,
        help="L3 strategy skill name (e.g. strategy-trend-follow). Optional if --strategy is given.",
    )
    parser.add_argument(
        "ticker",
        nargs="?",
        default=None,
        help="Ticker symbol; supports provider:ticker (e.g. yf:BTC-USD). Optional if --ticker is given.",
    )
    parser.add_argument(
        "interval", nargs="?", default=None, help="Candle interval (e.g. 1d, 4h, 1h). Optional if --interval is given."
    )
    parser.add_argument(
        "--strategy",
        dest="strategy_opt",
        default=None,
        help="L3 strategy skill name; overrides the positional STRATEGY.",
    )
    parser.add_argument(
        "--ticker",
        dest="ticker_opt",
        default=None,
        help="Ticker symbol (provider:ticker); overrides the positional TICKER.",
    )
    parser.add_argument(
        "--interval", dest="interval_opt", default=None, help="Candle interval; overrides the positional INTERVAL."
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="Inclusive start filter (ISO date YYYY-MM-DD or full ISO 8601); slices candles by timestamp post-fetch.",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        default=None,
        help="Inclusive end filter (ISO date YYYY-MM-DD or full ISO 8601); slices candles by timestamp post-fetch.",
    )
    parser.add_argument("--warmup", type=int, default=0, help="Bars to skip before emitting ideas.")
    parser.add_argument("--bars", type=int, default=0, help="Use the N most-recent candles (0 = all).")
    parser.add_argument("--period", default=None, help="Lookback period (default: 2y daily+, 1y intraday).")
    parser.add_argument("--asset-class", default=None, help="Asset class hint forwarded to the strategy.")
    parser.add_argument(
        "--mode",
        default=None,
        choices=["current", "add", "add_minus_one", "max_plus_one"],
        help="(strategy-liquidity-sweep) Conviction formula mode forwarded to "
        "analyze() as conviction_mode=. Use to A/B compare Sharpe per formula "
        "via FillSimulator. Omit to use the strategy's default.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Replay and print idea counts; do not trade.")
    parser.add_argument("--demo", action="store_true", help="Offline demo: synthetic candles, no network.")
    parser.add_argument(
        "--fill-sim",
        action="store_true",
        help="Run the FillSimulator over each fired idea and print a trade summary (bt-2). Default off.",
    )
    parser.add_argument(
        "--fee-bps", type=int, default=26, help="FillSimulator taker fee in bps (default 26 = Kraken 0.26%%)."
    )
    parser.add_argument(
        "--slippage-bps", type=int, default=2, help="FillSimulator per-side slippage floor in bps (default 2)."
    )
    parser.add_argument(
        "--qty",
        type=float,
        default=1.0,
        help="Position size in base units for --fill-sim (default 1.0; sizing is bt-3).",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Print per-strategy metrics + buy-and-hold benchmark as stable JSON (bt-4). Requires --fill-sim.",
    )
    parser.add_argument(
        "--forensic-drill",
        dest="forensic_drill",
        type=int,
        default=None,
        help="Replay one bar and print its decision, fill, and (preliminary) risk verdict.",
    )
    parser.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Emit the AXI envelope {data, count, errors, help} instead of human-readable text.",
    )
    return parser.parse_args(argv)


def _resolve_inputs(args: argparse.Namespace) -> tuple[str | None, str | None, str | None]:
    """Resolve strategy/ticker/interval: an optional flag overrides its positional counterpart."""
    strategy_name = args.strategy_opt if args.strategy_opt is not None else args.strategy
    ticker = args.ticker_opt if args.ticker_opt is not None else args.ticker
    interval = args.interval_opt if args.interval_opt is not None else args.interval
    return strategy_name, ticker, interval


def _help_lines(strategy_name: str, ticker: str, interval: str) -> list[str]:
    return [
        f"backtest-engine --strategy {strategy_name} --ticker {ticker} --interval {interval} --fill-sim --metrics --json",
        "backtest-engine --help",
    ]


def _forensic_decision(idea: dict) -> dict:
    """Extract the decision block (L3Idea fields) for the forensic drill."""
    return {
        "pair": idea.get("pair"),
        "direction": idea.get("direction"),
        "conviction": idea.get("conviction"),
        "version": idea.get("version") or conviction_version(int(idea.get("conviction", 1) or 1)),
        "entry_price": idea.get("entry_price"),
        "stop_loss": idea.get("stop_loss"),
        "take_profit": idea.get("take_profit") or [],
        "entry_type": idea.get("entry_type"),
        "reasoning": idea.get("reasoning"),
    }


def _risk_verdict_from_idea(idea: dict) -> dict:
    """Derive a preliminary risk verdict from the idea's stop/TP ladder and direction.

    bt-3 risk-engine wiring is forthcoming; this verdict is derived locally from
    the L3Idea shape only (entry/stop/TP1/conviction/direction), so it is marked
    ``preliminary: True`` and ``would_vet: True`` to signal that the real
    ``risk-engine.vet`` call is not yet in the replay loop.
    """
    direction = idea.get("direction", "long")
    entry = idea.get("entry_price")
    stop = idea.get("stop_loss")
    tps = idea.get("take_profit") or []
    target = tps[0] if tps else None
    stop_distance_pct = None
    target_distance_pct = None
    rr_to_tp1 = None
    if entry and stop:
        stop_distance_pct = round(abs(entry - stop) / entry * 100, 6)
    if entry and target:
        target_distance_pct = round(abs(target - entry) / entry * 100, 6)
    if entry and stop and target:
        if direction == "short":
            denom = stop - entry
            if denom > 0:
                rr_to_tp1 = round((entry - target) / denom, 6)
        else:
            denom = entry - stop
            if denom > 0:
                rr_to_tp1 = round((target - entry) / denom, 6)
    return {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "stop_distance_pct": stop_distance_pct,
        "target": target,
        "target_distance_pct": target_distance_pct,
        "rr_to_tp1": rr_to_tp1,
        "conviction": idea.get("conviction"),
        "version": idea.get("version") or conviction_version(int(idea.get("conviction", 1) or 1)),
        "would_vet": True,
        "preliminary": True,
    }


def _forensic_fill_summary(rec: dict) -> dict:
    """Extract the fill block (entry + exit summaries) from a TradeRecord."""
    ent = rec["entry"]
    ext = rec["exit"]
    return {
        "status": rec["status"],
        "exit_reason": rec["exit_reason"],
        "exit_bar_index": rec["exit_bar_index"],
        "pnl_quote": rec["pnl_quote"],
        "entry": {
            "fill_price": ent["fill_price"],
            "qty": ent["filled_volume"],
            "fee": ent["fee"],
            "slippage_paid": ent.get("raw", {}).get("slippage_paid"),
            "status": ent["status"],
        },
        "exit": {
            "fill_price": ext["fill_price"],
            "status": ext["status"],
        },
    }


def _print_forensic_human(bar_index: int, asof_ts: int, decision: dict, fill: dict, verdict: dict) -> None:
    """Print the forensic drill as human-readable text (decision / fill / risk verdict)."""
    iso = datetime.fromtimestamp(int(asof_ts), UTC).isoformat()
    print(f"forensic-drill: bar {bar_index}  asof_ts={asof_ts}  ({iso})")
    print("--- decision ---")
    print(f"pair: {decision['pair']}")
    print(f"direction: {decision['direction']}")
    print(f"conviction: {decision['conviction']} ({decision['version']})")
    print(f"entry_price: {decision['entry_price']}")
    print(f"stop_loss: {decision['stop_loss']}")
    print(f"take_profit: {decision['take_profit']}")
    print(f"entry_type: {decision['entry_type']}")
    print(f"reasoning: {decision['reasoning']}")
    print("--- fill ---")
    ent = fill["entry"]
    ext = fill["exit"]
    print(f"status: {fill['status']}")
    print(f"exit_reason: {fill['exit_reason']}")
    print(f"exit_bar_index: {fill['exit_bar_index']}")
    pnl = fill["pnl_quote"]
    print(f"pnl_quote: {pnl if pnl is not None else '-'}")
    print(
        f"entry: fill_price={ent['fill_price']} qty={ent['qty']} fee={ent['fee']} "
        f"slippage_paid={ent['slippage_paid']} status={ent['status']}"
    )
    print(f"exit:  fill_price={ext['fill_price']} status={ext['status']}")
    print("--- risk verdict (preliminary — bt-3 risk-engine wiring forthcoming) ---")
    print(f"direction: {verdict['direction']}")
    print(f"entry: {verdict['entry']}")
    print(f"stop: {verdict['stop']}  (stop_distance_pct={verdict['stop_distance_pct']}%)")
    print(f"target: {verdict['target']}  (target_distance_pct={verdict['target_distance_pct']}%)")
    print(f"rr_to_tp1: {verdict['rr_to_tp1']}")
    print(f"conviction: {verdict['conviction']} ({verdict['version']})")
    print(f"would_vet: {verdict['would_vet']}")
    print(f"preliminary: {verdict['preliminary']}")


def _run_forensic_drill(
    bt_lib,
    windows: list[dict],
    candles: list[list],
    bar_index: int,
    args: argparse.Namespace,
    *,
    strategy_name: str,
    ticker: str,
    interval: str,
) -> None:
    """Replay one bar and print its decision, fill, and (preliminary) risk verdict.

    Exits cleanly with a useful message when the bar is out of range, has no
    idea, or is the last bar (no next bar to fill the entry). Out-of-range and
    no-idea exit non-zero; the last-bar case exits 0 (clean — the data is valid,
    there is just no next bar to fill).
    """
    n = len(candles)
    warmup = args.warmup
    json_mode = args.json
    help_lines = _help_lines(strategy_name, ticker, interval)

    if bar_index < warmup or bar_index >= n:
        msg = f"forensic-drill: bar {bar_index} out of range [warmup={warmup}, last={n - 1}]"
        if json_mode:
            emit_envelope_json(None, count=0, errors=[msg], help=help_lines)
        else:
            print(msg, file=sys.stderr)
        sys.exit(2)

    window = next((w for w in windows if w["bar_index"] == bar_index), None)
    idea = window["idea"] if window is not None else None
    if idea is None:
        msg = f"forensic-drill: no idea fired at bar {bar_index}"
        if json_mode:
            emit_envelope_json(None, count=0, errors=[msg], help=help_lines)
        else:
            print(msg, file=sys.stderr)
        sys.exit(2)

    if bar_index == n - 1:
        msg = f"forensic-drill: bar {bar_index} is the last bar; no next bar to fill the entry"
        if json_mode:
            emit_envelope_json(
                {
                    "decision": _forensic_decision(idea),
                    "fill": None,
                    "risk_verdict": _risk_verdict_from_idea(idea),
                    "note": msg,
                },
                count=1,
                help=help_lines,
            )
        else:
            print(msg)
        sys.exit(0)

    sim = bt_lib.FillSimulator(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
    rec = sim.simulate(idea, candles, bar_index, {"qty": args.qty})
    decision = _forensic_decision(idea)
    fill = _forensic_fill_summary(rec)
    verdict = _risk_verdict_from_idea(idea)
    if json_mode:
        emit_envelope_json({"decision": decision, "fill": fill, "risk_verdict": verdict}, count=1, help=help_lines)
    else:
        _print_forensic_human(bar_index, window["asof_ts"], decision, fill, verdict)
    sys.exit(0)


def _trade_detail(rec: dict) -> dict:
    ent = rec["entry"]
    ext = rec["exit"]
    return {
        "side": ent["side"],
        "pair": ent["pair"],
        "status": rec["status"],
        "entry_price": ent["fill_price"],
        "exit_price": ext["fill_price"],
        "exit_reason": rec["exit_reason"],
        "exit_bar_index": rec["exit_bar_index"],
        "pnl_quote": rec["pnl_quote"],
    }


def _run_fill_sim(
    bt_lib, windows: list[dict], candles: list[list], args: argparse.Namespace
) -> tuple[list[dict], dict]:
    """Run the FillSimulator over each fired idea. Returns ``(records, summary)``.

    The entry fills at the NEXT bar's open after the idea's bar
    (``window["bar_index"]``), so ideas at the very last bar are skipped (no
    next bar). Position sizing is fixed at ``--qty`` (risk-engine sizing is
    bt-3); per-strategy fee/slippage come from ``--fee-bps`` / ``--slippage-bps``.
    """
    sim = bt_lib.FillSimulator(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
    ctx = {"qty": args.qty}
    records: list[dict] = []
    for w in windows:
        idea = w["idea"]
        if idea is None:
            continue
        records.append(sim.simulate(idea, candles, w["bar_index"], ctx))

    filled = sum(1 for r in records if r["status"] == "filled")
    open_n = sum(1 for r in records if r["status"] == "open")
    skipped = sum(1 for r in records if r["status"] == "skipped")
    total_pnl = sum(r["pnl_quote"] or 0.0 for r in records)
    summary = {
        "trades": len(records),
        "filled": filled,
        "open": open_n,
        "skipped": skipped,
        "fee_bps": args.fee_bps,
        "slippage_bps": args.slippage_bps,
        "qty": args.qty,
        "pnl_quote": total_pnl,
        "trades_detail": [_trade_detail(r) for r in records],
    }
    return records, summary


def _print_fill_sim_text(summary: dict) -> None:
    print(
        f"fill-sim: trades={summary['trades']} filled={summary['filled']} open={summary['open']} skipped={summary['skipped']} "
        f"fee_bps={summary['fee_bps']} slippage_bps={summary['slippage_bps']} qty={summary['qty']} "
        f"pnl_quote={summary['pnl_quote']:.4f}"
    )
    for td in summary["trades_detail"]:
        ep = td["entry_price"]
        xp = td["exit_price"]
        pnl = td["pnl_quote"]
        pnl_s = f"{pnl:.4f}" if pnl is not None else "-"
        print(
            f"  {td['side']:4s} {td['pair']:8s} status={td['status']:7s} "
            f"entry={ep if ep is not None else '-':>10} exit={xp if xp is not None else '-':>10} "
            f"reason={td['exit_reason']:6s} pnl={pnl_s}"
        )


def _run_metrics(bt_lib, records: list[dict], candles: list[list], args: argparse.Namespace) -> dict:
    """Compute per-strategy metrics + buy-and-hold benchmark. Returns the payload dict.

    Per-strategy equity curve: cumulative realized P&L anchored at a positive
    ``base_capital`` (default 1.0), one point per bar from ``warmup`` to the
    last bar — ``equity_curve[t] = base_capital + sum(pnl_quote)`` for trades
    whose ``exit_bar_index <= warmup + t``. A positive base keeps
    ``total_return`` and ``max_drawdown`` well-defined (a P&L curve starting at
    0.0 has no return-on-base, and the ``peak > 0`` drawdown guard would skip
    early non-positive stretches). If no trades have a non-None pnl,
    ``equity_curve = [base_capital]``.

    Open trades (``status == "open"`` — still open at series end) are excluded
    from the equity curve; only realized (closed) trade PnL is counted. Marking
    open positions to the last close injected forward-looking noise and produced
    absurd returns, so comparability with the benchmark is maintained via the
    benchmark curve (same base capital, cost-basis + close), not via M2M.

    The benchmark curve is the cost basis (open + slippage + fee at
    ``warmup + 1``) followed by one close per bar from ``warmup + 1`` onward,
    so it enters on the same bar the strategy's earliest possible fill occurs
    and its ``total_return`` carries the entry cost — like-for-like with the
    strategy curve.
    """
    n_bars = len(candles)
    base_capital: float = 100_000.0  # $100K account — PnL in USD maps to sensible % returns
    pnl_by_bar: dict[int, float] = {}
    for r in records:
        exit_bar = r["exit_bar_index"]
        pnl = r["pnl_quote"]
        if exit_bar is not None and pnl is not None:
            pnl_by_bar[exit_bar] = pnl_by_bar.get(exit_bar, 0.0) + pnl

    if not pnl_by_bar:
        curve = [base_capital]
    else:
        cum = 0.0
        curve = []
        for t in range(args.warmup, n_bars):
            cum += pnl_by_bar.get(t, 0.0)
            curve.append(base_capital + cum)

    strategy_metrics = bt_lib.compute(records, curve)
    bench_curve = bt_lib.buy_and_hold_benchmark(
        candles, args.warmup, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps
    )
    benchmark_metrics = bt_lib.compute([], bench_curve)
    return {"strategy": strategy_metrics, "benchmark": benchmark_metrics}


def _emit_no_data(args: argparse.Namespace, strategy_name: str, ticker: str, interval: str) -> None:
    payload = {
        "strategy": strategy_name,
        "ticker": ticker,
        "interval": interval,
        "warmup": args.warmup,
        "bars": 0,
        "windows": 0,
        "ideas": 0,
    }
    if args.json:
        emit_envelope_json(payload, count=0, help=_help_lines(strategy_name, ticker, interval))
    else:
        print("0 ideas (no data)")
    sys.exit(0)


def _emit_warmup_too_large(
    args: argparse.Namespace, strategy_name: str, ticker: str, interval: str, n_candles: int
) -> None:
    payload = {
        "strategy": strategy_name,
        "ticker": ticker,
        "interval": interval,
        "warmup": args.warmup,
        "bars": n_candles,
        "windows": 0,
        "ideas": 0,
    }
    if args.json:
        emit_envelope_json(payload, count=0, help=_help_lines(strategy_name, ticker, interval))
    else:
        print(f"0 ideas (warmup={args.warmup} >= bars={n_candles})")
    sys.exit(0)


def main() -> None:
    args = _parse_argv(sys.argv[1:])
    if args.metrics and not args.fill_sim and args.forensic_drill is None:
        print("error: --metrics requires --fill-sim", file=sys.stderr)
        sys.exit(2)

    strategy_name, ticker, interval = _resolve_inputs(args)
    missing = []
    if strategy_name is None:
        missing.append("strategy (--strategy or positional)")
    if ticker is None:
        missing.append("ticker (--ticker or positional)")
    if interval is None:
        missing.append("interval (--interval or positional)")
    if missing:
        print(f"error: missing required argument(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    period = args.period or _default_period(interval)
    try:
        validate_timeframe(interval, period)
    except ValueError as e:
        print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
        sys.exit(2)

    bt_lib = load_lib_for_script(__file__)
    strategy = load_skill(strategy_name)
    if strategy is None:
        print(f"error: strategy skill {strategy_name!r} not found", file=sys.stderr)
        sys.exit(2)

    if args.demo:
        raw = _make_demo_candles(args.bars if args.bars > 0 else 300)
    else:
        raw = fetch_ohlc(ticker, interval=interval, period=period)
        if not raw:
            _emit_no_data(args, strategy_name, ticker, interval)
            return

    if args.from_date or args.to_date:
        raw = _slice_candles_by_iso(raw, args.from_date, args.to_date)

    candles = raw[-args.bars :] if args.bars > 0 else raw

    if len(candles) <= args.warmup:
        _emit_warmup_too_large(args, strategy_name, ticker, interval, len(candles))
        return

    runner = bt_lib.WalkForwardRunner()
    # Strategy-level kwargs: only forward when explicitly set so strategies
    # that haven't accepted a given kwarg (e.g. strategy-trend-follow has no
    # ``conviction_mode``) don't get an unexpected keyword error from the
    # runner forwarding None through to analyze().
    strategy_kwargs: dict = {}
    if args.mode is not None:
        strategy_kwargs["conviction_mode"] = args.mode
    windows = runner.run(
        strategy,
        ticker,
        candles,
        warmup=args.warmup,
        interval=interval,
        period=period,
        asset_class=args.asset_class,
        **strategy_kwargs,
    )
    fired = sum(1 for w in windows if w["idea"] is not None)

    if args.forensic_drill is not None:
        _run_forensic_drill(
            bt_lib,
            windows,
            candles,
            args.forensic_drill,
            args,
            strategy_name=strategy_name,
            ticker=ticker,
            interval=interval,
        )
        return

    if args.json:
        data: dict = {
            "strategy": strategy_name,
            "ticker": ticker,
            "interval": interval,
            "warmup": args.warmup,
            "bars": len(candles),
            "windows": len(windows),
            "ideas": fired,
        }
        if args.fill_sim:
            _records, summary = _run_fill_sim(bt_lib, windows, candles, args)
            data["fill_sim"] = summary
            if args.metrics:
                data["metrics"] = _run_metrics(bt_lib, _records, candles, args)
        emit_envelope_json(data, count=fired, help=_help_lines(strategy_name, ticker, interval))
        sys.exit(0)

    mode = "demo" if args.demo else "dry-run"
    print(
        f"backtest-engine {mode}: strategy={strategy_name} ticker={ticker} "
        f"interval={interval} warmup={args.warmup} bars={len(candles)}"
    )
    print(f"windows: {len(windows)}  ideas: {fired}")

    if args.fill_sim:
        records, summary = _run_fill_sim(bt_lib, windows, candles, args)
        _print_fill_sim_text(summary)
        if args.metrics:
            metrics_payload = _run_metrics(bt_lib, records, candles, args)
            print(json.dumps(metrics_payload, indent=2, sort_keys=True))

    sys.exit(0)


if __name__ == "__main__":
    main()
