---
name: market-s-r
description: "Identifies nearest support and resistance levels from swing highs/lows with distance %, touch counts, and clustered price levels. Use for entry/exit placement, stop positioning, and level quality assessment. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, support, resistance, levels]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-s-r

Support and Resistance analysis from swing point clustering. Identifies key price levels where the market has previously reversed.

## Quick Start

```bash
uv run skills/market-s-r/scripts/run.py AAPL --json
```

## What it returns

- **nearest_support**, **nearest_resistance** — closest levels below/above price
- **support_distance_pct**, **resistance_distance_pct** — distance from current price
- **support_touches**, **resistance_touches** — how many swing points cluster at that level
- **clustered_levels** — all detected levels with price and touch counts
- **support_count**, **resistance_count** — total levels found in each direction

## Interpretation

| Metric | Meaning |
|--------|---------|
| Distance% | How close price is to a level — tighter = more reactive |
| Touches | More touches = stronger level (3+ is significant) |
| Clustered levels | Nearby swing points that merge into a zone |

## Edge Cases

- Context skill: no directional score or signal.
- Swing point window=3 for broad detection; cluster tolerance=1.5%.
- Levels closer than 0.1% to price are reported as current_price_sits_on_level.
- Requires 20+ candles minimum.
