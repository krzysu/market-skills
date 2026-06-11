---
name: market-squeeze
description: "Detects Bollinger Band / Keltner Channel squeeze with momentum direction. Use for pre-breakout compression signals and breakout confirmation. Supports any yfinance ticker."
compatibility: "Requires Python 3.12+ and uv"
---

# market-squeeze

Squeeze momentum indicator: when Bollinger Bands are inside Keltner Channels, volatility is compressing — a breakout is likely. The momentum histogram shows which direction.

Based on John Carter's TTM Squeeze (LazyBear variant).

## Quick Start

```bash
uv run skills/market-squeeze/scripts/run.py AAPL --json
```

## What it returns

- `squeeze_on`: whether BB bands are inside KC bands (compression)
- `momentum`: oscillator value — positive = bullish, negative = bearish
- `direction`: increasing or decreasing momentum
- `signal`: BULLISH, BEARISH, BULLISH FADING, BEARISH FADING, FLAT

## Signal Interpretation

| Squeeze | Momentum | Signal | Meaning |
|---------|----------|--------|---------|
| ON | rising | Squeeze building | Coiling for upside breakout |
| ON | falling | Squeeze building | Coiling for downside breakout |
| OFF | positive | BULLISH | Breakout in progress (up) |
| OFF | negative | BEARISH | Breakout in progress (down) |
| OFF | fading | Respective FADING | Momentum losing steam |

## Edge Cases

- "FADING" signals after a squeeze release may indicate the breakout is exhausting.
- Squeeze fires are strongest when confirmed by volume — use with OBV analysis.
- Requires 50+ days of data for reliable BB/KC calculation.
- Best used as a timing overlay on trend direction from `market-ema` or `market-trend-analysis`.
