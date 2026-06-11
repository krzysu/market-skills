---
name: market-ema
description: "Computes moving averages (EMA 21/50/100/200) for a ticker. Detects trend alignment, golden/death crosses, and EMA slope. Use for trend direction, support/resistance proxy, or DCA timing. Supports any yfinance ticker."
compatibility: "Requires Python 3.12+ and uv"
---

# market-ema

Computes exponential moving averages and trend structure from daily OHLC data via Yahoo Finance.

## Quick Start

```bash
uv run skills/market-ema/scripts/run.py AAPL --json
```

## What it returns

- Current price vs EMA(21), EMA(50), EMA(100), EMA(200)
- Alignment: FULL_BULL, PARTIAL_BULL, TANGLED, PARTIAL_BEAR, FULL_BEAR
- Price position: how many EMAs is price above (0-4)
- Slope of EMA(21) and EMA(50) as % change over 5 days
- Crossover: golden_cross or death_cross if 21/50 crossed within last 5 days

## Signal Interpretation

| Alignment | Price vs EMAs | Signal |
|-----------|--------------|--------|
| FULL_BULL + above all 4 | STRONG UPTREND — DCA with conviction |
| PARTIAL_BULL + above 3+ | UPTREND — mostly bullish structure |
| FULL_BEAR + above 0-1 | DOWNTREND — bearish, DCA into weakness |
| TANGLED / mixed | TRANSITION — wait for clarity |

Golden cross (21 crossing above 50) is bullish reversal. Death cross is bearish.

## Edge Cases

- Needs 220+ daily candles for EMA(200). Tickers with less history get an error.
- EMA alignment lags price — best used for structure, not timing.
- Combine with `market-rsi` for oversold entries aligned with trend.
