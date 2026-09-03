---
name: backtest-trend-miner
description: "Mine the nightly backtest pipeline's runs.jsonl + 7-night Sharpe baseline to detect per-(interval, strategy, ticker) strategy decay, improvement, and stable edges. Read-only analyzer (no network, no writes) that reports the rolling Sharpe time series we already collect but never analyze."
version: 0.1.0
metadata:
  hermes:
    tags: [backtest, miner, sharpe, decay, trend, regression, analytics]
    category: backtest
  compatibility: "Requires Python 3.12+ and uv"
---

# backtest-trend-miner

Read-side analyzer over the nightly backtest pipeline's batch history. The pipeline writes `runs.jsonl` (every run's per-combo results) and `backtest-pipeline-state.json` (a rolling 7-night Sharpe baseline), but nothing read them until now. This skill mines both files to answer: "does a given strategy × ticker × interval combo's edge decay, improve, or hold steady across nights?"

## When to use

- Diagnostics: "is strategy X's edge on a given ticker decaying across recent runs?"
- Triage: "which combos are trending down and should be watched / skipped?"
- Edge discovery: "which combos are stable and reliably positive?"
- After a nightly `backtest-pipeline` run, before the morning brief.

## When NOT to use

- As a real-time or entry signal — this is a trend over historical backtest Sharpes, not a trigger.
- To run backtests — use `backtest-engine` / `backtest-pipeline` directly.
- To fetch market data — zero network; it only reads local files.

## Quick Start

```bash
# Reads $MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR (or falls back to ./data/backtest-nightly/)
uv run skills/backtest-trend-miner/scripts/run.py --json

# Raise the visibility floor (only combos seen in >= 7 runs)
uv run skills/backtest-trend-miner/scripts/run.py --json --min-runs 7

# Restrict to specific intervals
uv run skills/backtest-trend-miner/scripts/run.py --json --intervals 1d 4h
```

## Input

| File | Source | Shape |
|------|--------|-------|
| `runs.jsonl` | backtest-pipeline run log | one JSON object per line: `{ts, strategies, tickers, results: {KEY: result}, errors, insufficient_data}` |
| `backtest-pipeline-state.json` | pipeline rolling baseline | `{baseline: {KEY: {history: [{ts, sharpe}], avg_sharpe_7n, n_samples}}}` |

`KEY` is `"{interval}×{strategy}×{ticker}"` (the ticker is the bare watchlist key). Each `result` carries `strategy_sharpe`, `trades`, `bars`, `provider`, and `insufficient_data`. The directory is resolved from `MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR`, falling back to the repo-relative `data/backtest-nightly/` default.

## Output (AXI envelope `data` payload)

- `combos` — every distinct `{strategy, ticker, interval}` present across runs (the full universe).
- `analysis.<interval>` — one entry per combo that passes `--min-runs` and has ≥1 valid Sharpe observation. Each entry carries `interval` (alongside `strategy` / `ticker`), so flat `flags` bucket entries are self-identifying when the same (strategy, ticker) appears under multiple intervals:
  - `sharpe_latest` — most recent run's `strategy_sharpe`.
  - `avg_sharpe_7n` — the pipeline's rolling 7-night average (from the state file; `null` when absent).
  - `sharpe_series` — the last ≤14 `{ts, sharpe}` observations, ts-ordered oldest first.
  - `n_runs` — how many runs this combo appears in.
  - `n_valid` — how many of those runs yielded a valid Sharpe observation.
  - `trend_slope` — normalized linear-regression slope (see below).
  - `downtrend` — `true` when `trend_slope < -0.05` and `sharpe_latest < avg_sharpe_7n`.
  - `improving` — `true` when `trend_slope > 0.05` and `sharpe_latest > avg_sharpe_7n`.
  - `volatility` — population std of `sharpe_series`.
  - `min_trades` — min trades across runs (a combo with `min_trades < 10` has a thin edge).
- `flags` — three summary buckets:
  - `decay` — `downtrend` combos, sorted by slope ascending (steepest decay first).
  - `improving` — `improving` combos, sorted by slope descending.
  - `stable` — combos with `n_valid >= 7` (valid Sharpe observations, not mere appearances) and `volatility < 0.5` (reliable edge).

The envelope `errors` list carries the flattened `insufficient_data` entries from the run log for context. `count` equals `len(combos)`.

### `trend_slope` definition

`trend_slope` is the least-squares slope of `sharpe` against the run ordinal, i.e. the per-run Sharpe drift (each x step = one nightly run). Because the time axis is the unit-spaced run index, the slope is measured in identical sharpe-per-night units for every combo regardless of how many runs it has — the `±0.05` thresholds mean a drift of 0.05 Sharpe per night (≈0.7 Sharpe over a 14-night window). Monotonic-up → positive, monotonic-down → negative, flat → `0.0`. Fewer than two points → `0.0`.

## Contracts

Pure analysis lives in `lib.py` (`analyze`, `split_key`, `trend_slope`, `volatility`) and is unit-tested in `tests/test_backtest_trend_miner.py`. `scripts/run.py` owns file I/O, env-var resolution, the AXI envelope, and the human text view. No registry change — this is a read-side analyzer, not an L3 strategy.

## Configuration

| Env var | Required | Purpose |
|---------|----------|---------|
| `MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR` | No | Pipeline output dir (defaults to `data/backtest-nightly/` under the repo root) |

When `runs.jsonl` is missing (pipeline never ran, or wrong dir), the skill returns an AXI `empty_state` with a `help` line rather than crashing.
