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
# One ticker (default)
uv run skills/run-all-l2/scripts/run.py SPY

# Multiple tickers
uv run skills/run-all-l2/scripts/run.py SPY BTC-USD AAPL --json

# Explicit provider
uv run skills/run-all-l2/scripts/run.py hl:LIT --json
```

## Runs

| L2 Skill | Purpose |
|----------|---------|
| market-accumulation | Wyckoff accumulation patterns |
| market-breakout | Fresh/stale/confirmed breakouts |
| market-exhaustion | Capitulation, blowoff, impulse exhaustion |
| market-liquidity-sweep | Support/resistance sweeps |
| market-trend-analysis | Composite trend verdict |
| market-trend-quality | Trend health (HEALTHY/WEAKENING/DEGRADING) |

## Output

- `tickers[ticker].skills[l2_name]` — full L2 result for each skill
- `tickers[ticker].skills[l2_name].error` — error string if skill failed
- Non-JSON mode: one line per skill showing present + classification
