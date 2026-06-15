---
name: market-macd
description: "Computes MACD(12,26,9): MACD line, signal line, histogram, histogram flip direction, and signal classification (BULLISH / BEARISH / crossovers). Use for momentum confirmation, divergence detection, and trend direction changes. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, macd, momentum]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-macd

MACD (Moving Average Convergence Divergence) momentum indicator using standard parameters 12/26/9.

## Quick Start

```bash
uv run skills/market-macd/scripts/run.py AAPL --json
```

## What it returns

- **macd_line** — fast EMA(12) minus slow EMA(26)
- **signal_line** — EMA(9) of the MACD line
- **histogram** — MACD line minus signal line
- **histogram_direction** — rising or falling vs previous bar
- **histogram_flip** — positive→negative (bearish) or negative→positive (bullish)
- **signal** — BULLISH, BEARISH, BULLISH_CROSS, BEARISH_CROSS, or NEUTRAL
- **score** — +2 (strong bullish) to -2 (strong bearish)
- **zone** — bullish / bearish / neutral

## Signal Interpretation

| Condition | Signal | Score |
|-----------|--------|-------|
| Histogram > 0, MACD > Signal, rising | BULLISH | +2 |
| Histogram > 0, MACD > Signal | BULLISH | +1 |
| MACD crossed above Signal (bullish cross) | BULLISH_CROSS | +1 |
| Histogram < 0, MACD < Signal | BEARISH | -1 |
| Histogram < 0, MACD < Signal, falling | BEARISH | -2 |
| MACD crossed below Signal (bearish cross) | BEARISH_CROSS | -1 |

## Edge Cases

- Requires 35+ daily candles for reliable MACD calculation.
- Histogram flips signal potential trend changes before price confirms.
- Best used with `market-trend` for trend context and `market-volume` for confirmation.
