---
name: strategy-exhaustion-fade
description: "L3 contrarian strategy. Fades exhaustion patterns (blowoff, capitulation) at extreme levels. Composes exhaustion, trend, and S/R."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, exhaustion, fade, contrarian, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-exhaustion-fade

L3 contrarian strategy that fades exhaustion patterns when the trend is extended and price is at a key S/R level.

## Quick Start

```bash
uv run skills/strategy-exhaustion-fade/scripts/run.py SPY
uv run skills/strategy-exhaustion-fade/scripts/run.py SPY --json
```

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-exhaustion | Detect exhaustion pattern (blowoff, capitulation, impulse) |
| market-s-r | Identify nearest support/resistance for reversal level |
| market-trend | Assess trend strength for extension context |

## Entry Logic

- **Short**: exhaustion blowoff + price above resistance + extended uptrend
- **Long**: exhaustion capitulation + price below support + extended downtrend
- **Stop**: beyond the extreme candle high/low (~1 ATR)
- **Targets**: return to nearest EMA, 1:1 R:R

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
- `narrative` — summary for user briefing
