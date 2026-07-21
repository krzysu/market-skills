---
name: strategy-fitness-heatmap
description: "Cross-strategy x ticker backtest fitness heatmap. Runs every L3 strategy (from analysis.registry.l3_strategies) against every T1+T2 ticker (from the market-watchlist tier_1 / tier_2 baskets) through the backtest-engine walk-forward replay + deterministic FillSimulator, and emits one Sharpe-ratio matrix per interval (1d, 4h). Rows=tickers, cols=strategies, cells=Sharpe. Use it to pick which strategy to run on which ticker, and to spot strategy/ticker mismatches."
version: 0.1.0
metadata:
  hermes:
    tags: [backtest, fitness, heatmap, sharpe, l3, strategy, matrix, watchlist]
    category: backtest
  compatibility: "Requires Python 3.12+ and uv"
---

# strategy-fitness-heatmap

Cross-strategy x ticker backtest fitness heatmap. For every L3 strategy in
`analysis.registry.l3_strategies()` x every T1+T2 ticker from the
market-watchlist baskets `tier_1` and `tier_2`, the skill runs the
`backtest-engine` walk-forward replay + deterministic `FillSimulator` +
`compute` metrics and collects the Sharpe ratio into a 2D matrix. One matrix
per interval (`1d`, `4h`).

The matrix answers two questions at once:

- **Which strategy fits which ticker?** A row's highest cell is the strategy
  that back-tested best on that ticker for that interval.
- **Which strategies are robust vs one-trick?** A strategy with positive
  Sharpe across most tickers is robust; one that only wins on a single ticker
  is a specialist (and likely overfit).

## Purpose

Pick a strategy for a ticker on evidence, not vibes. The heatmap is a
batch-run over the full L3 x T1+T2 grid, so the agent (or the user) can read
off the best (strategy, ticker, interval) combo at a glance and then drill
into the per-combo `details` for the full metrics (Sortino, max drawdown,
profit factor, trade count) plus the buy-and-hold benchmark.

## When to use

- Choosing which L3 strategy to run live on a given ticker.
- Spotting tickers where every strategy underperforms buy-and-hold (don't
  trade there, or hold).
- Sanity-checking a newly shipped L3 strategy across the watchlist before
  trusting it live.
- Comparing 1d vs 4h fitness for the same (strategy, ticker) - a strategy
  that wins on 1d but loses on 4h (or vice versa) is timeframe-sensitive.

## NOT to use

- Live signal generation - use the strategy skill's own `scripts/run.py`.
- Real execution - `FillSimulator` is a deterministic model, not a venue
  adapter; use `execution-kraken-spot` / `execution-kraken-perps` for fills.
- Drawing conclusions from a single Sharpe cell without checking `trade_count`
  in the details - a Sharpe on 1-2 trades is small-sample noise (see the
  backtest-engine pitfalls doc).
- Comparing absolute Sharpes across intervals without reading the
  `periods_per_year` note below - 1d and 4h use different annualization
  factors so their Sharpes are directly comparable, but a 4h run on a thin
  alt may have far fewer bars than a 1d run on a major.

## Quick Start

```bash
# Full heatmap (all L3 strategies x tier_1 + tier_2 tickers, 1d + 4h):
uv run skills/strategy-fitness-heatmap/scripts/run.py --json

# Narrow the grid for a quick check (testing):
uv run skills/strategy-fitness-heatmap/scripts/run.py --json \
    --tickers BTCUSD ETHUSD --strategies strategy-trend-follow strategy-mean-reversion

# Human-readable matrix print (no --json):
uv run skills/strategy-fitness-heatmap/scripts/run.py
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--json` | off | Emit the AXI envelope `{"data", "count", "errors", "help"}` instead of the human-readable matrix print. |
| `--tickers TICKER ...` | `tier_1` + `tier_2` | Override the ticker list. Supports `provider:ticker` notation (e.g. `hl:LIT`). Pass once with multiple values. |
| `--strategies NAME ...` | all L3 strategies | Override the strategy list (from `analysis.registry.l3_strategies()`). Useful for narrowing a slow run. |

No positional arguments - the skill loops over the full grid, it does not
take a single ticker.

## Output shape

The AXI envelope `data` payload:

```json
{
  "intervals": {
    "1d": {
      "matrix": {"tickers": [...], "strategies": [...], "values": [[sharpe, ...], ...]},
      "details": [{ticker, strategy, interval, sharpe, metrics, benchmark, trade_count, bars, error}, ...]
    },
    "4h": { "matrix": {...}, "details": [...] }
  },
  "strategies": [...],
  "tickers": [...],
  "config": {base_capital, fee_bps, slippage_bps, qty, intervals: {1d: {...}, 4h: {...}}}
}
```

- `matrix.values` is `rows=tickers, cols=strategies, cells=Sharpe` -
  `values[i][j]` is the Sharpe of `tickers[i]` under `strategies[j]`.
- `details` has one entry per (ticker, strategy) combo per interval, carrying
  the full `compute` metrics dict (Sharpe, Sortino, max drawdown, profit
  factor, average trade, trade count, total/annualized return), the
  buy-and-hold benchmark metrics, the bar count, and an `error` field that is
  `null` on success or carries the exception text / "fetch_ohlc returned no
  candles" / "insufficient candles" on failure.

`count` at the envelope level is `len(strategies) * len(tickers) * 2`
(total combos across both intervals). `errors` carries the deduped per-combo
error strings (e.g. one "fetch_ohlc returned no candles" per missing ticker).

## Interpretation guide

### Per strategy

| Strategy | Tends to fit | Watch out for |
|----------|--------------|---------------|
| `strategy-trend-follow` | Tickers with sustained directional moves (BTC, ETH in trending regimes). Daily timeframe preferred. | Choppy / range-bound alts - whipsaw entries, stop-out streaks. |
| `strategy-mean-reversion` | Range-bound or high-variance tickers where overshoots revert. 4h swings. | Strong trends - fades a runner and keeps fading. |
| `strategy-breakout-confirm` | Tickers with clean consolidation->expansion cycles (BTC, liquid majors). 1d confirmation. | False breakouts on thin alts - low volume confirms nothing. |
| `strategy-accumulation-swing` | Tickers with visible accumulation footprints before mark-ups. 1d structure. | Tickers that never accumulate (continuous drift). |
| `strategy-exhaustion-fade` | Tickers prone to climax/flush moves (low-float, perp-DEX). 4h. | Trending majors - exhaustion calls fire early and keep fighting the trend. |
| `strategy-funding-carry` | Perp tickers with persistently negative/positive funding. Interval-agnostic. | Spot tickers (PAXGUSD) - no funding signal, expect 0 trades. |
| `strategy-liquidity-sweep` | Tickers with liquidity-grab / stop-hunt behavior (alts on perp DEXes). 4h. | Gold/stable majors - not the sweep regime. |

### Per interval

- **1d** - trend-follow suitability. Daily bars smooth out intraday noise,
  so directional strategies (`strategy-trend-follow`,
  `strategy-breakout-confirm`, `strategy-accumulation-swing`) get a cleaner
  signal. A ticker whose 1d row lights up on trend-follow but not on 4h is a
  trend candidate on the daily timeframe.
- **4h** - mean-reversion / breakout suitability. The 4h timeframe captures
  swing cycles and intraday liquidity moves where mean-reversion
  (`strategy-mean-reversion`, `strategy-exhaustion-fade`) and sweep
  (`strategy-liquidity-sweep`) patterns are more visible. A ticker whose 4h
  row beats its 1d row on mean-reversion is a swing-trade candidate.

### Reading a cell

- Compare the strategy Sharpe to the `benchmark` Sharpe in the same detail
  entry. Beating buy-and-hold after fees + slippage is the real bar; a
  strategy Sharpe of 1.2 looks different when the benchmark Sharpe is 2.0.
- Check `trade_count` before trusting a Sharpe. A Sharpe of 2.0 on 2 trades
  is noise; a Sharpe of 1.0 on 30 trades is signal. The
  `backtest-engine` pitfalls doc has the full small-sample Sharpe warning.
- A row of zeros across all strategies usually means `fetch_ohlc` returned
  no candles (check the `error` field in `details`) - the ticker may be
  delisted, the provider may be down, or the interval/period combo may be
  unsupported for that ticker's provider.
- A single zero in an otherwise populated row means the strategy's
  `analyze()` raised on that candle set - read the `error` text for the
  exception.

## How to update

### Add a new L3 strategy

Register it in `analysis/registry.l3_strategies()` (append to `_l3_strategies`).
The heatmap picks it up automatically on the next run - no edit to this skill
is needed. The new strategy gets a new column in both interval matrices.

### Add a new ticker

Add it to the `tier_1` or `tier_2` basket in the market-watchlist
(`skills/market-watchlist/data/watchlist.json`). The heatmap reads
`by_category("tier_1") + by_category("tier_2")` programmatically, so the new
ticker gets a new row in both interval matrices with no edit here.

For one-off checks without editing the watchlist, pass `--tickers`:

```bash
uv run skills/strategy-fitness-heatmap/scripts/run.py --json --tickers hl:NEWCOIN SOLUSD
```

## Determinism

The FillSimulator is deterministic (next-bar-open entry, stop-first intrabar
tie, fixed `fee_bps=26` / `slippage_bps=2`, `qty=1.0`), and the walk-forward
runner is cache-free and stateless across calls. So two runs over the same
candles produce identical Sharpe values. The only non-determinism is the
data fetch - once `fetch_ohlc` returns a candle set, every downstream metric
is reproducible. The `config` block in the output pins the exact
warmup / period / fee / slippage / qty used per interval so a run is
auditable.

## Config

| Knob | 1d | 4h | Notes |
|------|----|----|-------|
| `period` | `2y` | `1y` | Lookback for `fetch_ohlc`. |
| `warmup` | `200` | `500` | Bars skipped before emitting ideas. |
| `periods_per_year` | `365` | `2190` | Sharpe annualization factor (bars/year). |
| `fee_bps` | `26` | `26` | Kraken taker 0.26%. |
| `slippage_bps` | `2` | `2` | Per-side slippage floor. |
| `qty` | `1.0` | `1.0` | Position size in base units. |
| `base_capital` | `100000.0` | `100000.0` | Equity-curve anchor for `compute`. |

`periods_per_year` is bar-aware so 4h Sharpes are annualized against 2190
bars/year (6 bars/day x 365) and 1d against 365 - this keeps the two
intervals' Sharpes directly comparable in absolute terms. The backtest-engine
default of 365 (daily convention) is used only for the 1d interval.

## Architecture

- **Strategies** come from `analysis.registry.l3_strategies()` - single
  source of truth shared with `run-all-l3`.
- **Tickers** come from `analysis.watchlist.by_category("tier_1")` +
  `by_category("tier_2")` - the same watchlist the batch runners use.
- **Backtest engine** is loaded with `analysis.skill_loader.load_skill("backtest-engine")`
  and driven via `WalkForwardRunner`, `FillSimulator`, `compute`, and
  `buy_and_hold_benchmark` - the same API documented in the backtest-engine
  SKILL.md.
- **Candles are fetched once per (ticker, interval)** and reused across all
  strategies, so the per-ticker network cost is paid twice (one per
  interval), not once per combo. The benchmark is likewise computed once per
  (ticker, interval) and reused across strategies.
- **Output** rides the AXI envelope (`analysis.output.emit_envelope_json`),
  matching every other skill's `--json` contract.
