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

Fetches candles once per ticker, then runs all L2 pattern skills on the cached data. Use this from cron jobs or agents that need to evaluate multiple tickers across all L2s.

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
| `--interval=INTERVAL` | `1d` | `1m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. Passed to each L2. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. Passed to each L2. |
| `--include-notes` | off | Auto-load active [`market-notes`](../market-notes/) for each ticker. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. JSON output includes top-level `interval`/`period` so the consumed timeframe is always visible to downstream agents.

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
