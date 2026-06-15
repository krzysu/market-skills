---
name: strategy-breakout-confirm
description: "L3 momentum strategy. Enters confirmed breakouts with volume and squeeze confirmation. Composes breakout, volume, and squeeze."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, breakout, momentum, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-breakout-confirm

L3 momentum strategy that only enters breakouts when volume confirms and squeeze is firing. Filters out fakeouts.

## Quick Start

```bash
uv run skills/strategy-breakout-confirm/scripts/run.py SPY
uv run skills/strategy-breakout-confirm/scripts/run.py SPY --json
```

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-breakout | Detect breakout with type + confirmation |
| market-volume | Volume confirmation and OBV trend |
| market-squeeze | Squeeze momentum direction |

## Entry Logic

- **Long**: breakout confirmed + volume_ratio > 1.2 + squeeze bullish
- **Short**: breakdown confirmed + volume_ratio > 1.2 + squeeze bearish
- **Stop**: below breakout level (long) / above (short) — ~0.5 ATR
- **Targets**: next S/R level, 2x ATR

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
- `narrative` — summary for user briefing
