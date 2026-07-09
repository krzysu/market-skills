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
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. Passed to each L3. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1w`/`2w`/`3w`/`4w`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. Passed to each L3. |
| `--include-notes` | off | Auto-load active [`market-notes`](../market-notes/) for each ticker. |
| `--top=N` | all | Cap each strategy's ideas list to the N highest-conviction entries (sorted by `conviction` desc, ticker asc). |
| `--fired-only` | off | Drop strategies that emitted an empty `ideas[]`. |
| `--fields=<csv>` | minimal | Project each idea to the listed keys. |
| `--full` | — | Ship the complete envelope payload. |

Both `--flag value` (space-separated) and `--flag=value` (equals) syntaxes are accepted; both are validated against `analysis/intervals.VALID_INTERVALS` / `VALID_PERIODS` — a bad value exits 2 with a friendly error. JSON output includes top-level `interval`/`period` so the consumed timeframe is always visible to downstream agents.

**yfinance caveat:** when the resolved provider is yfinance and the requested (interval, period) is outside yfinance's per-interval lookback cap (e.g. `4h` beyond `1mo`, `1h` beyond `1mo`, `5m` beyond `5d`), the call returns `[]` and emits a stderr warning instead of letting yfinance 404 on the unknown token. Route around by using `hl:<ticker>` or `kraken:<ticker>` for non-daily intraday data.

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
- `tickers[ticker].strategies[strategy_name].rejection_reasons[]` — when `ideas` is empty, this lists stable tags (`insufficient_data`, `missing_trend`, `missing_breakout_confirmation`, etc.) explaining why the strategy had no signal. Lets LLM agents branch on structured tags without parsing `narrative`.
- Non-JSON mode: shows count of ideas per strategy and direction summary

### Idea normalization (canonical shape)

Every emitted idea in `strategies[*].ideas[]` is normalized in-process to a canonical schema before the envelope is emitted, regardless of which strategy produced it:

| Field | Always present | Notes |
|-------|----------------|-------|
| `strategy_name` | yes | Producer (e.g. `strategy-trend-follow`) — saves consumers from re-reading the outer envelope key. |
| `idea_id` | yes | Deterministic sha1-derived id from `(strategy, ticker, direction, entry, stop, tp[])`. Stable across re-runs; lets backtests/paper-traders address specific ideas without uuid persistence. |
| `pair` | yes | The ticker (already in `tickers[*]` keys, but mirrored for downstream filtering). |
| `entry_price` | yes | Canonical; padded with `null` only when the strategy genuinely omitted it. |
| `entry_range` | yes | `[low, high]`. Auto-mirrored from `entry_price` when the strategy only emitted a single price. |
| `stop_loss` | yes | Canonical; same as the legacy `stop` flat field which is also populated. |
| `take_profit` | yes | Always a 3-element list. Padded with `None` if the strategy emitted fewer TPs. |
| `take_profit_ideal` | when emitted | Unrounded construction values (mirrors `take_profit` when present). |
| `rr_to_tp` | yes | `[rr_tp1, rr_tp2, rr_tp3]` — computed from entry/stop/TP when missing. |
| `conviction`, `version`, `direction`, `entry_type`, `reasoning`, `source_skills`, `veto_reasons`, `move_maturity_pct`, `entry_window_validity_pct`, `asset_class` | per-strategy | Whatever the strategy emitted. |

The flat-mirror fields (`stop`, `tp1`/`tp2`/`tp3`, `rr_tp1`/`rr_tp2`/`rr_tp3`, `tp1_pct`) are also populated for the `l3-conviction-scan` extractor and any consumer that prefers them.

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

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
