---
name: recipe-scanner
description: "Scans a watchlist for momentum/breakout setups using composite trend analysis. Filters to actionable setups (BUY/SELL) and ranks by conviction. Use for daily opportunity screening, watchlist triage, or sector rotation. Supports any yfinance tickers."
compatibility: "Requires Python 3.12+ and uv"
---

# recipe-scanner

Multi-ticker momentum scanner that identifies actionable setups from a watchlist. Runs composite trend analysis on each ticker and filters to those with clear signals.

## Quick Start

```bash
# Full watchlist scan
uv run skills/recipe-scanner/scripts/run.py --json

# Filter for buy signals only
uv run skills/recipe-scanner/scripts/run.py --action BUY --json

# Top 5 strongest signals
uv run skills/recipe-scanner/scripts/run.py --top 5 --json

# Custom tickers
uv run skills/recipe-scanner/scripts/run.py AAPL MSFT NVDA TSLA --json
```

## What it returns

- Filtered list of tickers matching the action criteria
- Each match includes: price, trend direction, RSI, squeeze signal, unified score, and a human-readable rationale
- Sorted by score descending (strongest signal first)

## How it Works

1. Fetches daily OHLC for each ticker
2. Runs EMA structure (21/50), RSI, squeeze, and OBV volume trend
3. Scores each ticker on a unified 0-100 scale
4. Filters by action (STRONG_BUY / BUY / WATCH / AVOID)
5. Returns ranked matches

## Workflow

After getting scanner results:

1. Review the top matches — check the rationale for conflicts
2. For high-conviction setups, deep-dive with `market-trend-analysis`
3. Check support/resistance levels with `market-ema`
4. Confirm timing with `market-squeeze`
5. Execute only after all confirmations align

## Dependencies

Internally uses the same analysis functions as `market-trend-analysis`. You don't need to run that skill separately — the scanner runs everything needed.
