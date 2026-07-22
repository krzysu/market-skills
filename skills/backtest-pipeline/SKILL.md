---
name: backtest-pipeline
description: "Nightly backtest pipeline — runs every L3 strategy against every active watchlist ticker on 1d + 4h intervals, compares against a rolling 7-night Sharpe baseline, detects strategy decay, and produces five cross-boundary output files consumed by downstream skills (DTP conviction floor, ESD conviction modulation, Position Watchdog regime, Swing Scan skip list, Morning Brief)."
version: 0.1.0
metadata:
  hermes:
    tags: [backtest, pipeline, cron, nightly, sharpe, fitness, decay, regime]
    category: backtest
  compatibility: "Requires Python 3.12+ and uv"
---

# backtest-pipeline

Nightly backtest pipeline — the sole producer of five cross-boundary analysis files consumed by downstream skills. Runs every L3 strategy against every active watchlist ticker, compares against a rolling 7-night Sharpe baseline, and detects strategy decay or improvement.

## When to use

- Scheduled cron at 02:00 CEST — after the feedback absorber (01:00), before the morning brief (05:00).
- Manual re-run after a new strategy or ticker is added to the registry / watchlist.
- Diagnostics: "what does the backtest say about this ticker/strategy right now?"

## When NOT to use

- As a real-time signal — the pipeline uses 1y of daily bars; it's a regime trend, not an entry trigger.
- For ad-hoc single-ticker backtests — use `backtest-engine` directly.
- To generate a heatmap on custom tickers/intervals — the heatmap was merged into this pipeline's `fitness_matrix.json` output.

## Quick Start

```bash
# The only required env var
export MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR=/path/to/data/backtest-nightly

# Optional: needed only for watchdog regime output
export MARKET_SKILLS_BACKTEST_PIPELINE_OPEN_POSITIONS_PATH=/path/to/open-positions.json

uv run skills/backtest-pipeline/scripts/run.py
```

## Output files

All files are written to `$MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR`:

| File | Consumer | Path resolution |
|------|----------|-----------------|
| `conviction_thresholds_private.json` | DTP via `analysis/conviction_thresholds.py` | `MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH` or `<OUT_DIR>/conviction_thresholds_private.json` |
| `fitness_matrix.json` | ESD (emerging-setup-detector) | `<OUT_DIR>/fitness_matrix.json` |
| `watchdog_regime_state.json` | Position Watchdog | `MARKET_SKILLS_REGIME_STATE_PATH` or `<OUT_DIR>/watchdog_regime_state.json` |
| `swing_scan_skip_list.json` | Swing Scan | `<OUT_DIR>/swing_scan_skip_list.json` |
| `regime_health_brief.md` | Morning Brief | `<OUT_DIR>/regime_health_brief.md` |

Consumer-side env vars are **overrides** — unset them to use the default `<OUT_DIR>/<filename>`. The typical cron config sets only ``OUT_DIR`` and the two existing consumer-side overrides for backward compat.

## Contracts

All output file contracts are defined in `lib.py` as TypedDicts with validation functions:

- `FitnessMatrix` / `validate_fitness_matrix()` — Sharpe pivot table (intervals → tickers × strategies)
- `WatchdogRegimeState` / `validate_watchdog_regime()` — per-position per-strategy regime status
- `SwingScanSkipList` / `validate_swing_scan_skip()` — ticker triage list
- `validate_regime_brief()` — Markdown structural check
- `conviction_thresholds_private.json` contract is owned by `analysis/conviction_thresholds.py`

## Configuration

| Env var | Required | Purpose |
|---------|----------|---------|
| `MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR` | **Yes** | Base directory for all 5 files + rolling state |
| `MARKET_SKILLS_BACKTEST_PIPELINE_OPEN_POSITIONS_PATH` | No | Source for watchdog regime output |
| `MARKET_SKILLS_WATCHLIST_PATH` | No | Watchlist JSON (falls back to repo default) |

## Architecture

- **Ticker discovery**: `analysis.watchlist.basket()` on each `ACTIVE_BASKET`
- **Strategy discovery**: `analysis.registry.l3_strategies()` — top 3 are "primary" (full coverage), remainder limited to 3 secondary
- **Per-pair execution**: shells out to `uv run skills/backtest-engine/scripts/run.py` with `--fill-sim --metrics --json`
- **Baseline**: rolling 7-night average Sharpe per `{interval}×{strategy}×{ticker}`
- **Decay detection**: Sharpe zero-crossing (improvement or decay), ≥0.5 absolute delta, benchmark vs strategy comparison
- **Runtime**: ~180 pairs max, 120s timeout per pair
