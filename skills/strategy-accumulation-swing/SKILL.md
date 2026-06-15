---
name: strategy-accumulation-swing
description: "L3 swing strategy. Enters on Wyckoff accumulation patterns within healthy trends. Composes accumulation and trend-quality."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, accumulation, swing, wyckoff, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-accumulation-swing

L3 swing strategy that identifies Wyckoff accumulation patterns (spring, reaccumulation) within healthy trends and enters for multi-swing moves.

## Quick Start

```bash
uv run skills/strategy-accumulation-swing/scripts/run.py SPY
uv run skills/strategy-accumulation-swing/scripts/run.py SPY --json
```

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-accumulation | Detect accumulation pattern (spring, reaccumulation, UTAD) |
| market-trend-quality | Confirm trend health to avoid catching falling knives |

## Entry Logic

- **Long**: accumulation detected (spring or reaccumulation) + trend quality is HEALTHY_UPTREND or WEAKENING (improving)
- **Stop**: below spring low
- **Targets**: 2R, 3R where R = entry - stop

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
- `narrative` — summary for user briefing
