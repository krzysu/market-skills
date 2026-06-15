---
name: market-liquidity-sweep
description: "Detects whether a fakeout occurred: price wicks through S/R without close beyond, immediate reclaim, old high/low taken then reversed, above-avg volume on rejection candle. Classifications: SUPPORT_SWEEP, RESISTANCE_SWEEP, DOUBLE_TEST."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, liquidity, sweep, fakeout]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-liquidity-sweep

L2 pattern detection skill that composes L1 indicators to detect liquidity sweeps and fakeouts.

## Quick Start

```bash
uv run skills/market-liquidity-sweep/scripts/run.py SPY
uv run skills/market-liquidity-sweep/scripts/run.py SPY --json
```

## Sub-Signals

| Sub-signal | Weight | Source L1 | Logic |
|---|---|---|---|
| Wick through S/R without close beyond | 0.35 | market-s-r | sits_on_level is False; low of last 3 bars below nearest_support or high above nearest_resistance; close back within range |
| Immediate reclaim | 0.30 | market-s-r | After wick-through, latest close is back above support or below resistance |
| Old high/low taken then reversed | 0.20 | market-trend | Last bar high exceeded swing_high but close below it, or low broke swing_low but close above it |
| Above-avg volume on rejection candle | 0.15 | market-volume | Highest volume in last 5 bars > 1.5× SMA(20) of volume |

## Classifications

| Classification | Meaning |
|---|---|
| SUPPORT_SWEEP | Price wicked below support then reclaimed |
| RESISTANCE_SWEEP | Price wicked above resistance then reclaimed |
| DOUBLE_TEST | Old high/low taken and reversed without clear S/R direction |

## Output

- `pattern.present` (bool)
- `pattern.confidence` (1–5)
- `pattern.classification` (SUPPORT_SWEEP / RESISTANCE_SWEEP / DOUBLE_TEST)
- `pattern.type` always `"SWEEP"`
- `signals` — per-signal `{"present": bool, "weight": float}`
- `input_scores` — raw L1 outputs
- `narrative` — one-sentence explanation
