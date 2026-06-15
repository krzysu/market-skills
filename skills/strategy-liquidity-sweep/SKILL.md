---
name: strategy-liquidity-sweep
description: "L3 sweep-following strategy. Enters after liquidity sweeps with accumulation confirmation. Composes liquidity-sweep, accumulation, and volume."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, liquidity, sweep, crypto, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-liquidity-sweep

L3 strategy that enters after liquidity sweeps when accumulation confirms smart money is positioning the opposite direction. Works especially well on crypto where stop hunts are frequent.

## Quick Start

```bash
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD --json
```

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-liquidity-sweep | Detect support/resistance sweeps and double tests |
| market-accumulation | Confirm accumulation after the sweep |
| market-volume | Volume confirmation of reversal |

## Entry Logic

- **Long**: support sweep detected + accumulation pattern + volume confirms reversal
- **Short**: resistance sweep detected + distribution signals + volume confirms rejection
- **Stop**: below sweep low (long) / above sweep high (short)
- **Targets**: 2R, 3R

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
- `narrative` — summary for user briefing
