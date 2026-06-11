---
name: market-overview
description: "Unified market scan: runs trend-analysis + squeeze + RSI on multiple tickers in parallel, scores them 0-100, and ranks them with actions (BUY/SELL/WATCH). Use for a full market landscape, screening, or daily brief. Supports any yfinance tickers."
compatibility: "Requires Python 3.12+ and uv"
---

# market-overview

High-level market scanner that runs all indicators on a watchlist of tickers and produces a unified, ranked overview with per-ticker actions.

## Quick Start

```bash
# Default watchlist (SPY, QQQ, AAPL, GOOGL, BTC-USD, GLD)
uv run skills/market-overview/scripts/run.py --json

# Custom tickers
uv run skills/market-overview/scripts/run.py AAPL MSFT NVDA --json

# Filter by action
uv run skills/market-overview/scripts/run.py --action BUY --json

# Top N only
uv run skills/market-overview/scripts/run.py --top 5 --json
```

## What it returns

- Per-ticker: price, trend verdict, RSI, squeeze signal, unified score (0-100)
- Action classification: STRONG_BUY (>=75), BUY (>=55), WATCH (>=35), AVOID (<35)
- Ranked list sorted by unified score descending
- Macro context (VIX if available)

## Scoring Model

The unified score weights each component and normalizes to 0-100. For details, see [references/scoring.md](references/scoring.md).

## Edge Cases

- Tickers with insufficient data are skipped and noted in errors.
- The default watchlist is a suggestion — pass your own tickers for custom screening.
- This is a screening tool, not a trading signal. Verify individual setups with `market-trend-analysis`.
