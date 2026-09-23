"""market-breadth — cross-sectional altcoin breadth (% of basket outperforming the benchmark).

Breadth is a *rotation* read, not an entry-timing signal: it answers "should the
multi-day swing book lean into alts or into the benchmark this week?" by
measuring what share of a watchlist basket has a strictly greater N-day return
than the benchmark asset (default BTC).

This skill is intentionally NOT registered in ``analysis.registry`` — registry
is per-ticker L2/L3-only. Breadth is cross-sectional: it has no single ticker
and cannot be run per-ticker (docs/adr/0010-cross-sectional-breadth-is-not-an-l2).
The logic lives skill-local following the ``market-movers`` precedent and stays
importable via ``analysis.skill_loader.load_skill("market-breadth")``.

Universe resolution is config-driven: the member list and the benchmark both
come from the ``market-watchlist`` data file (``MARKET_SKILLS_WATCHLIST_PATH``
env var or the resolver default). No ticker literals live in this module.
"""

from __future__ import annotations

import statistics
from typing import Any

from analysis.data import fetch_ohlc
from analysis.output import empty_state
from analysis.watchlist import by_category, categories, provider_for, resolve

_HISTORY_HELP = [
    "Reduce --window-days, or pick a basket whose members have longer daily history",
    "Run `uv run skills/market-breadth/scripts/run.py --json --window-days=3` for a shorter window",
]

_UNCALIBRATED_NOTE = "Regime bands (>=60 / <=40) are uncalibrated first guesses - trust pct_beating, not the label."


def _usable_closes(candles: list[list]) -> list[float]:
    """Numeric closes of a candle series, in order. Non-numeric / non-positive rows are skipped."""
    out: list[float] = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        if close > 0:
            out.append(close)
    return out


def _return_pct(closes: list[float], effective: int) -> float:
    return (closes[-1] / closes[-1 - effective] - 1) * 100


def _narrative(
    *,
    window_days: int,
    effective: int,
    pct_beating: float,
    members: int,
    benchmark: str,
    benchmark_return: float,
    median_return: float,
    regime: str,
) -> str:
    line = (
        f"Breadth {effective}d: {pct_beating:.1f}% of {members} members beating {benchmark} "
        f"({benchmark} {benchmark_return:+.2f}%, median {median_return:+.2f}%) -> {regime}. "
        f"{_UNCALIBRATED_NOTE}"
    )
    if effective < window_days:
        line += f" Window truncated: requested {window_days}d, used {effective}d (shortest available history)."
    return line


def compute_breadth(
    member_candles: dict[str, list[list]],
    benchmark_candles: list[list],
    *,
    window_days: int = 7,
) -> dict:
    """Compute cross-sectional breadth from raw daily candles. Pure, no I/O.

    Every measured return uses ONE shared effective window:
    ``min(window_days, shortest available history across every usable series
    and the benchmark)`` — so a 7d alt return is never compared against a 3d
    benchmark return. Returns are ``close[-1] / close[-1 - effective] - 1``.

    Members with fewer than 2 usable closes are excluded from the measurement
    and named in ``errors[]``. When no reading is possible at all (benchmark
    too short, no measurable members) the AXI empty-state envelope is
    returned instead of a payload.

    The strict outperformance comparison runs on unrounded returns — rounding
    first could flip a member across the benchmark (raw +5.0049 vs +4.9951
    both report as 5.00) — while every reported value is rounded to 2dp.
    """
    errors: list[str] = []
    if window_days <= 0:
        raise ValueError(f"window_days must be >= 1, got {window_days}")

    bench_closes = _usable_closes(benchmark_candles)
    if len(bench_closes) < 2:
        return empty_state(
            errors=[f"benchmark series has {len(bench_closes)} usable close(s) — need >= 2 to measure a return"],
            help=_HISTORY_HELP,
        )

    measured: dict[str, list[float]] = {}
    for ticker, candles in member_candles.items():
        closes = _usable_closes(candles)
        if len(closes) < 2:
            errors.append(f"[BREADTH {ticker} SKIPPED — {len(closes)} usable close(s), need >= 2]")
            continue
        measured[ticker] = closes

    if not measured:
        return empty_state(
            errors=[*errors, "no basket member has >= 2 usable closes — nothing to measure"],
            help=_HISTORY_HELP,
        )

    effective = min(
        window_days,
        len(bench_closes) - 1,
        min(len(closes) - 1 for closes in measured.values()),
    )
    if effective < 1:
        return empty_state(
            errors=["insufficient history: effective window is 0 days"],
            help=_HISTORY_HELP,
        )

    if effective < window_days:
        errors.append(
            f"[BREADTH WINDOW TRUNCATED — requested {window_days}d, used {effective}d (shortest available history)]"
        )

    bench_raw = _return_pct(bench_closes, effective)
    raw_returns = {ticker: _return_pct(closes, effective) for ticker, closes in measured.items()}

    bench_return = round(bench_raw, 2)
    returns = {ticker: round(r, 2) for ticker, r in raw_returns.items()}

    n_beating = sum(1 for r in raw_returns.values() if r > bench_raw)
    pct_beating = round(n_beating / len(raw_returns) * 100, 1)
    median_return = round(float(statistics.median(list(raw_returns.values()))), 2)

    if pct_beating >= 60.0:
        regime = "alt_rotation"
    elif pct_beating <= 40.0:
        regime = "btc_led"
    else:
        regime = "mixed"

    leaders = [
        {"ticker": ticker, "return_pct": ret}
        for ticker, ret in sorted(returns.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]
    laggards = [
        {"ticker": ticker, "return_pct": ret}
        for ticker, ret in sorted(returns.items(), key=lambda item: (item[1], item[0]))[:3]
    ]

    return {
        "window_days": window_days,
        "effective_window_days": effective,
        "members": len(returns),
        "pct_beating": pct_beating,
        "btc_return_pct": bench_return,
        "median_alt_return_pct": median_return,
        "regime": regime,
        "leaders": leaders,
        "laggards": laggards,
        "errors": errors,
    }


def analyze(
    *,
    basket: str = "crypto_alts",
    window_days: int = 7,
    benchmark: str = "btc",
    path: Any = None,
) -> dict:
    """Resolve the universe from the watchlist, fetch daily candles, compute breadth.

    Returns the payload dict on success; the AXI empty-state envelope
    (``data: None``, ``count: 0``) on a missing/empty basket, an unresolvable
    benchmark alias, or insufficient history — never raises at the CLI
    boundary for a missing basket.
    """
    member_tickers = by_category(basket, path=path)
    if not member_tickers:
        names = categories(path=path)
        return empty_state(
            errors=[f"basket {basket!r} not found or empty in watchlist"],
            help=[
                f"available baskets: {', '.join(names)}" if names else "watchlist has no baskets yet",
                "Run `uv run skills/market-watchlist/scripts/run.py list` to inspect the registry",
                "Run `uv run skills/market-breadth/scripts/run.py --json --basket=<BASKET>` to re-run",
            ],
        )

    try:
        bench_ticker = resolve(benchmark, path=path)
    except ValueError as e:
        return empty_state(
            errors=[f"benchmark alias {benchmark!r} is ambiguous: {e}"],
            help=[f"available baskets: {', '.join(categories(path=path))}"],
        )
    if bench_ticker is None:
        return empty_state(
            errors=[f"benchmark alias {benchmark!r} did not resolve in watchlist"],
            help=[f"available baskets: {', '.join(categories(path=path))}"],
        )

    fetch_errors: list[str] = []
    member_candles: dict[str, list[list]] = {}
    for ticker in member_tickers:
        if ticker == bench_ticker:
            continue
        candles = fetch_ohlc(ticker, interval="1d", period="1y", source=provider_for(ticker, path=path))
        if not candles:
            fetch_errors.append(f"[BREADTH {ticker} FETCH FAILED — no daily candles returned]")
            continue
        member_candles[ticker] = candles

    bench_candles = fetch_ohlc(bench_ticker, interval="1d", period="1y", source=provider_for(bench_ticker, path=path))
    if not bench_candles:
        return empty_state(
            errors=[f"[BREADTH {bench_ticker} FETCH FAILED — no daily candles returned for the benchmark]"],
            help=[
                "Check the benchmark entry's source in market-watchlist, or pass another --benchmark alias",
                f"available baskets: {', '.join(categories(path=path))}",
            ],
        )

    result = compute_breadth(member_candles, bench_candles, window_days=window_days)

    if result.get("data") is None and "count" in result:
        if fetch_errors:
            result["errors"] = [*result["errors"], *fetch_errors]
        return result

    result["benchmark"] = bench_ticker
    result["basket"] = basket
    result["narrative"] = _narrative(
        window_days=result["window_days"],
        effective=result["effective_window_days"],
        pct_beating=result["pct_beating"],
        members=result["members"],
        benchmark=bench_ticker,
        benchmark_return=result["btc_return_pct"],
        median_return=result["median_alt_return_pct"],
        regime=result["regime"],
    )
    if fetch_errors:
        result["errors"] = [*result["errors"], *fetch_errors]
    return result
