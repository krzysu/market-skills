---
name: market-trend
description: "Computes comprehensive trend analysis: EMA alignment (FULL_BULL to FULL_BEAR), HH/HL swing structure detection, slope metrics, and price position. Use for trend direction, strength assessment, and structure integrity. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, trend, structure]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-trend

Comprehensive trend structure analysis combining EMA alignment with swing point pattern recognition.

## Quick Start

```bash
uv run skills/market-trend/scripts/run.py AAPL --json
```

## What it returns

- **ema_21**, **ema_50**, **ema_100**, **ema_200** — current EMA values
- **alignment** — FULL_BULL / PARTIAL_BULL / TANGLED / PARTIAL_BEAR / FULL_BEAR
- **higher_high**, **higher_low** — swing structure booleans
- **slope_21_pct**, **slope_50_pct** — EMA slope as % change over 5 days
- **crossover** — golden_cross / death_cross / null
- **price_above_emas** — count of EMAs price is above (0–4)
- **score** — -4 (strong downtrend) to +4 (strong uptrend)
- **zone** — bullish / bearish / neutral
- **signal** — STRONG_UPTREND / UPTREND / SIDEWAYS / DOWNTREND / STRONG_DOWNTREND

## Signal Interpretation

| Score | Signal | Structure |
|-------|--------|-----------|
| +4 | STRONG_UPTREND | FULL_BULL + HH + HL — maximum conviction |
| +3 | UPTREND | FULL_BULL + HH or HL |
| +2 | UPTREND | FULL_BULL or PARTIAL_BULL |
| +1 | UPTREND | PARTIAL_BULL with improving structure |
| 0 | SIDEWAYS | TANGLED or mixed signals |
| -1 to -4 | DOWNTREND | Corresponding bearish structure |

## Edge Cases

- Requires 220+ daily candles for EMA(200); falls back to available EMAs with a warning.
- Swing structure lags price by nature — best for confirmation, not timing.
- Golden/death cross events boost/discount score by 1 level.
- Combine with `market-rsi` for oversold entries in uptrends, `market-volume` for confirmation.
