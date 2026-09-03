"""backtest-trend-miner — mine runs.jsonl + 7n baseline for per-combo decay/improvement.

Read-side analyzer over the nightly backtest pipeline's ``runs.jsonl`` batch
history and ``backtest-pipeline-state.json`` rolling baseline. Zero network,
zero writes — pure functions over local files.

Inputs (read from ``$MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR``):

  ``runs.jsonl`` — one JSON object per run::

      {"ts": "...", "strategies": [...], "tickers": [...],
       "results": {"1d×strategy-trend-follow×<TICKER>": {...}, ...},
       "errors": [...], "insufficient_data": [...]}

  where each result carries ``strategy_sharpe``, ``trades``, ``bars``,
  ``provider``, and ``insufficient_data``. The key is
  ``"{interval}×{strategy}×{ticker}"`` (the ticker is the bare watchlist key,
  not the ``provider:ticker`` form stored in ``result["ticker"]``).

  ``backtest-pipeline-state.json`` — ``{"baseline": {KEY: {"history": [...],
  "avg_sharpe_7n": float, "n_samples": int}}}``.

Output (the AXI ``data`` payload, built by :func:`analyze`)::

    {
      "combos":   [{"strategy", "ticker", "interval"}, ...],   # all combos present
      "analysis": {"1d": [<combo entry>, ...], "4h": [...]},  # min_runs-filtered
      "flags":    {"decay": [...], "improving": [...], "stable": [...]},
    }

``analyze`` is pure and unit-tested in ``tests/test_backtest_trend_miner.py``;
``scripts/run.py`` owns file I/O, the env-var resolution, and the AXI envelope.
"""

from __future__ import annotations

import statistics

__all__ = [
    "analyze",
    "split_key",
    "trend_slope",
    "volatility",
]

ENV_OUT_DIR = "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR"

# Tunable analysis constants.
SERIES_CAP = 14  # max sharpe_series length (readability)
DEFAULT_MIN_RUNS = 5  # only analyze combos seen in >= this many runs
DOWN_SLOPE = -0.05  # normalized-slope threshold for a decay signal
UP_SLOPE = 0.05  # normalized-slope threshold for an improvement signal
STABLE_MIN_RUNS = 7  # min valid sharpe observations before a combo can be "stable"
STABLE_MAX_VOLATILITY = 0.5  # sharpe std below which an edge is "low volatility"
LOW_TRADES_GUARD = 10  # combos with min_trades consistently below this are a guard

_SEPARATOR = "\u00d7"  # × (U+00D7 MULTIPLICATION SIGN)


def split_key(key: str) -> tuple[str, str, str] | None:
    """Split a ``"{interval}×{strategy}×{ticker}"`` key into its three parts.

    Returns ``None`` when the key does not split into exactly three non-empty
    parts (malformed key — the caller skips it rather than crashing).
    """
    parts = key.split(_SEPARATOR)
    if len(parts) != 3:
        return None
    interval, strategy, ticker = parts
    if not interval or not strategy or not ticker:
        return None
    return interval, strategy, ticker


def trend_slope(series: list[float]) -> float:
    """Least-squares slope of ``sharpe`` against run index (sharpe per run).

    The x-axis is the run ordinal (0 .. n-1), so every step is one nightly run
    and the slope is a per-run Sharpe drift directly comparable across combos
    regardless of how many runs each has. Monotonic-up series yield a positive
    slope, monotonic-down a negative one, flat a zero. Returns ``0.0`` for
    fewer than two points.
    """
    n = len(series)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = (n - 1) / 2.0
    mean_y = statistics.fmean(series)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series))
    return num / denom


def volatility(series: list[float]) -> float:
    """Population standard deviation of a sharpe series (``0.0`` when empty)."""
    if not series:
        return 0.0
    return statistics.pstdev(series)


def _is_number(value: object) -> bool:
    """True for a real int/float (rejects ``bool``, ``None``)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def analyze(
    runs: list[dict],
    state: dict | None = None,
    *,
    min_runs: int = DEFAULT_MIN_RUNS,
    intervals: list[str] | None = None,
) -> dict:
    """Build the trend-miner payload from parsed ``runs`` and baseline ``state``.

    Args:
        runs: list of parsed ``runs.jsonl`` run records, in file order
            (oldest first).
        state: parsed ``backtest-pipeline-state.json`` (or ``None``/``{}`` when
            missing — ``avg_sharpe_7n`` then resolves to ``None``).
        min_runs: only combos appearing in ``>= min_runs`` runs are analyzed.
        intervals: optional interval filter; ``None``/``[]`` means all intervals.

    Returns:
        ``{"combos": [...], "analysis": {...}, "flags": {...}}`` (see module
        docstring). ``combos`` lists every distinct combo found across runs;
        ``analysis`` holds only the min_runs-filtered, data-bearing subset.
    """
    baseline = (state or {}).get("baseline") or {}
    if not isinstance(baseline, dict):
        baseline = {}
    interval_filter = set(intervals) if intervals else None

    # combo -> [(ts, result), ...] in run order; ``order`` preserves first-seen.
    observations: dict[tuple[str, str, str], list[tuple[str, dict]]] = {}
    order: list[tuple[str, str, str]] = []

    for run in runs:
        if not isinstance(run, dict):
            continue
        raw_ts = run.get("ts")
        ts = raw_ts if isinstance(raw_ts, str) else ""
        results = run.get("results") or {}
        if not isinstance(results, dict):
            continue
        for key, raw_result in results.items():
            parts = split_key(key)
            if parts is None:
                continue
            if interval_filter is not None and parts[0] not in interval_filter:
                continue
            if parts not in observations:
                observations[parts] = []
                order.append(parts)
            observations[parts].append((ts, raw_result if isinstance(raw_result, dict) else {}))

    combos: list[dict] = []
    analysis: dict[str, list[dict]] = {}

    for combo in order:
        interval, strategy, ticker = combo
        obs = observations[combo]
        n_runs = len(obs)

        valid: list[tuple[str, float]] = []
        trades_vals: list[int] = []
        for ts, result in obs:
            if result.get("insufficient_data"):
                continue
            sharpe = result.get("strategy_sharpe")
            if not isinstance(sharpe, (int, float)) or isinstance(sharpe, bool):
                continue
            valid.append((ts, float(sharpe)))
            trades = result.get("trades")
            if isinstance(trades, (int, float)) and not isinstance(trades, bool):
                trades_vals.append(int(trades))

        combos.append({"strategy": strategy, "ticker": ticker, "interval": interval})

        if n_runs < min_runs or not valid:
            continue

        valid.sort(key=lambda item: item[0])
        series = valid[-SERIES_CAP:]
        sharpe_vals = [sharpe for _, sharpe in series]
        sharpe_latest = sharpe_vals[-1]

        base_entry = baseline.get(f"{interval}{_SEPARATOR}{strategy}{_SEPARATOR}{ticker}")
        avg_7n: float | None = None
        if isinstance(base_entry, dict) and _is_number(base_entry.get("avg_sharpe_7n")):
            avg_7n = float(base_entry["avg_sharpe_7n"])

        slope = trend_slope(sharpe_vals)
        vol = volatility(sharpe_vals)
        min_trades = min(trades_vals) if trades_vals else None

        downtrend = bool(avg_7n is not None and slope < DOWN_SLOPE and sharpe_latest < avg_7n)
        improving = bool(avg_7n is not None and slope > UP_SLOPE and sharpe_latest > avg_7n)

        entry = {
            "strategy": strategy,
            "ticker": ticker,
            "interval": interval,
            "sharpe_latest": round(sharpe_latest, 6),
            "avg_sharpe_7n": round(avg_7n, 6) if avg_7n is not None else None,
            "sharpe_series": [{"ts": ts, "sharpe": round(sharpe, 6)} for ts, sharpe in series],
            "n_runs": n_runs,
            "n_valid": len(valid),
            "trend_slope": round(slope, 6),
            "downtrend": downtrend,
            "improving": improving,
            "volatility": round(vol, 6),
            "min_trades": min_trades,
        }
        analysis.setdefault(interval, []).append(entry)

    for iv_entries in analysis.values():
        iv_entries.sort(key=lambda entry: (entry["strategy"], entry["ticker"]))

    decay: list[dict] = []
    improving_list: list[dict] = []
    stable: list[dict] = []
    for iv_entries in analysis.values():
        for entry in iv_entries:
            if entry["downtrend"]:
                decay.append(entry)
            if entry["improving"]:
                improving_list.append(entry)
            if entry["n_valid"] >= STABLE_MIN_RUNS and entry["volatility"] < STABLE_MAX_VOLATILITY:
                stable.append(entry)

    decay.sort(key=lambda entry: entry["trend_slope"])  # steepest decay first
    improving_list.sort(key=lambda entry: entry["trend_slope"], reverse=True)
    stable.sort(key=lambda entry: entry["volatility"])  # lowest volatility first

    return {
        "combos": sorted(combos, key=lambda c: (c["interval"], c["strategy"], c["ticker"])),
        "analysis": {iv: analysis[iv] for iv in sorted(analysis)},
        "flags": {"decay": decay, "improving": improving_list, "stable": stable},
    }
