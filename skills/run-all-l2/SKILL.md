---
name: run-all-l2
description: "Runner that fetches candles once per ticker, then runs all 6 L2 pattern skills in-process. Replaces N×M individual fetches with N fetches (one per ticker)."
version: 0.1.0
metadata:
  hermes:
    tags: [runner, batch, l2, optimization]
    category: utility
compatibility: "Requires Python 3.12+ and uv"
---

# run-all-l2

Fetches candles once per ticker, then runs all L2 pattern skills on the cached data. Use this from batch runners or agents that need to evaluate multiple tickers across all L2s.

## When NOT to use

- For a deep single-ticker dive — call the individual L2 skill (`market-breakout`, `market-accumulation`, etc.) directly for focused output and flags.
- As a trade signal — it returns pattern detections (present/classification), not trade ideas. Feed fired patterns into `run-all-l3` / an L3 strategy before acting.
- When you want trade ideas directly — use `run-all-l3` (or `l3-conviction-scan` for ranking); run-all-l2 is the L2 layer only.

## Why

Each L2 script's `run.py` calls `fetch_ohlc()` independently. A morning brief for 3 tickers × 5 L2s = 15 fetches. This runner reduces that to 3 fetches (one per ticker).

## Quick Start

```bash
# One ticker
uv run skills/run-all-l2/scripts/run.py SPY

# Multiple tickers
uv run skills/run-all-l2/scripts/run.py SPY BTC-USD AAPL --json

# Explicit provider
uv run skills/run-all-l2/scripts/run.py hl:LIT --json

# Custom timeframe (e.g. 4h candles for the past month)
uv run skills/run-all-l2/scripts/run.py AAPL --interval=4h --period=1mo --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER`... (positional, repeatable) | — | At least one ticker required. Supports `provider:ticker`. |
| `--json` | human | Emit JSON envelope to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider. |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. Passed to each L2. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1w`/`2w`/`3w`/`4w`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. Passed to each L2. |
| `--include-notes` | off | Auto-load active [`market-notes`](../market-notes/) for each ticker. |
| `--fired-only` | off | Drop L2 skills whose pattern didn't fire (present=False or classification=None). |
| `--fields=<csv>` | minimal | Project each per-ticker block to the listed keys. |
| `--full` | — | Ship the complete envelope payload. |

Both `--flag value` (space-separated) and `--flag=value` (equals) syntaxes are accepted; both are validated against `analysis/intervals.VALID_INTERVALS` / `VALID_PERIODS` — a bad value exits 2 with a friendly error. JSON output includes top-level `interval`/`period` so the consumed timeframe is always visible to downstream agents.

**yfinance caveat:** when the resolved provider is yfinance and the requested (interval, period) is outside yfinance's per-interval lookback cap (e.g. `4h` beyond `1mo`, `1h` beyond `1mo`, `5m` beyond `5d`), the call returns `[]` and emits a stderr warning instead of letting yfinance 404 on the unknown token. Route around by using `hl:<ticker>` or `kraken:<ticker>` for non-daily intraday data.

## Runs

| L2 Skill | Purpose |
|----------|---------|
| market-accumulation | Wyckoff accumulation patterns |
| market-breakout | Fresh/stale/confirmed breakouts |
| market-exhaustion | Capitulation, blowoff, impulse exhaustion |
| market-liquidity-sweep | Support/resistance sweeps |
| market-trend-quality | Trend health (HEALTHY/WEAKENING/DEGRADING) |

## Output

- `tickers[ticker].skills[l2_name]` — full L2 result for each skill
- `tickers[ticker].skills[l2_name].error` — error string if skill failed
- Non-JSON mode: one line per skill showing present + classification

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Candle cache (opt-in)

This runner fetches candles once per ticker, then runs all skills in-process —
but each invocation still re-fetches from the venue unless the OHLC cache is
enabled. For cron / repeated scans, set a TTL so identical
`provider:ticker:interval:period` requests are served from disk:

```bash
MARKET_SKILLS_OHLC_CACHE_TTL=3600 uv run skills/run-all-l2/scripts/run.py SPY BTC-USD
```

`0` (the default) means always fetch live. See the README "Candle cache"
section for the full contract (store path, entry cap, staleness guidance).

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
