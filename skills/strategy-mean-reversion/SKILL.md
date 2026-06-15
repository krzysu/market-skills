---
name: strategy-mean-reversion
description: "L3 mean-reversion strategy. Buys oversold at support, sells overbought at resistance. Composes RSI, S/R, and volatility."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, mean-reversion, range, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-mean-reversion

L3 strategy that fades extreme moves by entering when price reaches a key S/R level and RSI confirms exhaustion.

## Quick Start

```bash
uv run skills/strategy-mean-reversion/scripts/run.py SPY
uv run skills/strategy-mean-reversion/scripts/run.py SPY --json
```

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-rsi | Detect oversold/overbought conditions (L1, composed by L3 directly) |
| market-s-r | Identify nearest support/resistance levels |
| market-volatility | Confirm low vol for tighter reversal (L1, composed by L3 directly) |

## Entry Logic

- **Long**: RSI < 30 (oversold) + price at or below support + low vol regime
- **Short**: RSI > 70 (overbought) + price at or above resistance + low vol regime
- **Stop**: below support (long) / above resistance (short) — ~1 ATR beyond level
- **Targets**: return to middle (50% S/R range), 1:1 R:R

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
- `narrative` — summary for user briefing
