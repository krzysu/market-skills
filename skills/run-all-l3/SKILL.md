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

Fetches candles once per ticker, then runs all L3 strategy skills on the cached data. Use this from cron jobs or agents that need to evaluate trade ideas across all strategies.

## Why

Same reasoning as `run-all-l2/` — each L3 script's `run.py` calls `fetch_ohlc()` independently. This runner reduces N×6 fetches to N fetches.

## Quick Start

```bash
uv run skills/run-all-l3/scripts/run.py SPY
uv run skills/run-all-l3/scripts/run.py SPY BTC-USD AAPL --json
```

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

- `tickers[ticker].strategies[strategy_name].ideas[]` — trade ideas from each strategy
- `tickers[ticker].strategies[strategy_name].narrative` — strategy summary
- Non-JSON mode: shows count of ideas per strategy and direction summary
