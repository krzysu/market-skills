---
name: market-rsi
description: "Computes RSI(14) momentum oscillator for a ticker. Use for oversold/overbought identification, DCA entry timing, or momentum confirmation. Supports any yfinance ticker."
compatibility: "Requires Python 3.12+ and uv"
---

# market-rsi

Computes the Relative Strength Index (RSI) using Wilder smoothing on daily OHLC from Yahoo Finance.

## Quick Start

```bash
uv run skills/market-rsi/scripts/run.py AAPL --json
```

## What it returns

- RSI(14) value
- Classification into oversold/neutral/overbought zones
- 7-day RSI delta and trend direction (rising/falling/stable)
- Score: +2 (oversold, buy) to -2 (overbought, skip)

## Signal Interpretation

| RSI Range | Zone | Signal | Score |
|-----------|------|--------|-------|
| < 30 | Oversold | Strong buy / accumulation | +2 |
| 30-40 | Approaching Oversold | Buy signal | +1 |
| 40-60 | Neutral | No signal | 0 |
| 60-70 | Approaching Overbought | Caution / reduce | -1 |
| > 70 | Overbought | Skip or trim | -2 |

## Edge Cases

- RSI can remain overbought in strong uptrends — combine with `market-ema` for context.
- RSI can remain oversold in strong downtrends — don't buy blindly.
- Requires 30+ days of data minimum.
- RSI is a single oscillator — use `market-trend-analysis` for a full picture.
