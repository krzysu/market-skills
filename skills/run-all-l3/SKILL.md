---
name: run-all-l3
description: "Runner that fetches candles once per ticker, then runs all 6 L3 strategy skills in-process. Returns aggregated trade ideas."
version: 0.1.0
metadata:
  hermes:
    tags: [runner, batch, l3, strategy, optimization]
    category: utility
compatibility: "Requires Python 3.12+ and uv"
---

# run-all-l3

Fetches candles once per ticker, then runs all L3 strategy skills on the cached data. Use this from batch runners or agents that need to evaluate trade ideas across all strategies.

## Why

Same reasoning as `run-all-l2/` — each L3 script's `run.py` calls `fetch_ohlc()` independently. This runner reduces N×6 fetches to N fetches.

## Quick Start

```bash
uv run skills/run-all-l3/scripts/run.py SPY
uv run skills/run-all-l3/scripts/run.py SPY BTC-USD AAPL --json

# Custom timeframe (e.g. 4h candles for the past month)
uv run skills/run-all-l3/scripts/run.py AAPL --interval=4h --period=1mo --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER`... (positional, repeatable) | — | At least one ticker required. Supports `provider:ticker`. |
| `--json` | human | Emit JSON envelope to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider. |
| `--interval=INTERVAL` | `1d` | `1m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. Passed to each L3. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. Passed to each L3. |
| `--include-notes` | off | Auto-load active [`market-notes`](../market-notes/) for each ticker. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. JSON output includes top-level `interval`/`period` so the consumed timeframe is always visible to downstream agents.

## Runs

| L3 Strategy | Entry Logic |
|-------------|-------------|
| strategy-trend-follow | Long/short in healthy trends |
| strategy-mean-reversion | Fade extremes at S/R |
| strategy-breakout-confirm | Confirmed breakouts with volume + squeeze |
| strategy-accumulation-swing | Wyckoff spring/reaccumulation in trend |
| strategy-exhaustion-fade | Fade blowoff/capitulation at S/R |
| strategy-liquidity-sweep | Sweep + accumulation + volume |

## Output

- `tickers[ticker].strategies[strategy_name].ideas[]` — trade ideas from each strategy. Each idea includes `version: "v1".."v5"` derived from `conviction` via `analysis/contracts.conviction_version`.
- `tickers[ticker].strategies[strategy_name].narrative` — strategy summary
- Non-JSON mode: shows count of ideas per strategy and direction summary

## Idea-state tracking (stale-idea detection)

This runner does **not** maintain persistent idea state. The `--track-ideas`
flag that previously lived here was removed — it was a workflow concern
(persistent on-disk state plus a hardcoded "30 ticks without 50% progress =
stale" policy) that didn't belong in a reusable analysis library.

Workflows that need stale-idea reports should consume the JSON
output of this runner and run their own state-tracking step. The runner's
JSON envelope (`tickers[ticker].strategies[*].ideas[]`) is stable and
self-describing — any consumer can read `entry_price`, `take_profit[0]`,
and `direction` to compute progress and staleness on their own terms.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
