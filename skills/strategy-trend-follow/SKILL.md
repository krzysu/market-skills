---
name: strategy-trend-follow
description: "L3 trend-following strategy. Enters long in healthy uptrends, short in healthy downtrends. Composes market-trend-quality and market-breakout for entry timing."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, trend, follow, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-trend-follow

L3 strategy that enters with the dominant trend at pullbacks or breakouts. Composes L2 verdicts from trend-quality and breakout detection.

## Quick Start

```bash
uv run skills/strategy-trend-follow/scripts/run.py SPY
uv run skills/strategy-trend-follow/scripts/run.py SPY --json
```

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-trend-quality | Assess trend health (HEALTHY_UPTREND / HEALTHY_DOWNTREND) |
| market-breakout | Detect fresh breakouts for entry timing |

## Entry Logic

- **Long**: trend-quality is HEALTHY_UPTREND + breakout at resistance → limit at EMA pullback or market at breakout
- **Short**: trend-quality is HEALTHY_DOWNTREND + breakdown at support → limit at EMA bounce or market at breakdown
- **Stop**: below recent swing low (long) / above recent swing high (short) — approximated via ATR
- **Targets**: 1.5R, 2.5R, 4R where R = entry - stop

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
- `narrative` — summary for user briefing
