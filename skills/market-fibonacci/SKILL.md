---
name: market-fibonacci
description: "Computes Fibonacci retracement levels (0.236/0.382/0.5/0.618/0.786) and extensions (1.272/1.618) from the most recent swing high/low. Use for identifying potential support/resistance zones and price targets. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, fibonacci, levels]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-fibonacci

Fibonacci retracement and extension analysis based on the most recent significant swing high and swing low.

## Quick Start

```bash
uv run skills/market-fibonacci/scripts/run.py AAPL --json
```

## What it returns

- **fib_levels** — dict of all retracement and extension levels
- **swing_high**, **swing_low** — the identified swing points
- **current_position** — above_swing_high / below_swing_low / inside_swing
- **nearest_fib_support** — closest fib level below current price
- **nearest_fib_resistance** — closest fib level above current price
- **nearest_fib_distance_pct** — distance to nearest fib level

## Interpretation

| Key Level | Significance |
|-----------|-------------|
| 0.382 | Shallow retracement — trend may continue |
| 0.5 | Psychological midpoint — watch for reversal |
| 0.618 | Golden ratio — strongest retracement level |
| 0.786 | Deep retracement — trend may be ending |
| 1.272 / 1.618 | Extension targets if price breaks beyond swing |

## Edge Cases

- Context skill: no directional score or signal.
- Swing point detection uses a 5-bar window — less sensitive than parameterized versions.
- Requires 25+ candles minimum for reliable swing identification.
- Combine with `market-s-r` for multi-level confluence confirmation.
