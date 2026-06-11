---
name: market-trend-analysis
description: "Composite trend verdict combining EMA alignment, RSI momentum, squeeze, and volume trend. Use for a single, weighted trend direction with conviction level. More comprehensive than individual indicators. Supports any yfinance ticker."
compatibility: "Requires Python 3.12+ and uv"
---

# market-trend-analysis

Runs EMA structure, RSI, squeeze momentum, and volume trend (OBV) simultaneously and synthesizes a weighted trend verdict. This is the "trend analysis, not just moving average" layer — it reconciles conflicting signals from multiple indicators.

## Quick Start

```bash
uv run skills/market-trend-analysis/scripts/run.py AAPL --json
```

## What it returns

- Individual component results: EMA, RSI, squeeze, volume
- Unified verdict: direction (BULLISH / BEARISH / NEUTRAL) with conviction (HIGH / MEDIUM / LOW)
- Conflicting signals flagged explicitly
- Score: -10 to +10 composite

## How the Verdict Works

Each component votes with a weight:

| Component | Weight | What it measures |
|-----------|--------|------------------|
| EMA structure | 35% | Long-term trend alignment (21/50/100/200) |
| RSI momentum | 25% | Overbought/oversold state + direction |
| Squeeze momentum | 25% | Near-term breakout direction |
| Volume (OBV) | 15% | Confirmation via volume flow |

Conflicts (e.g., EMA bearish but RSI oversold) reduce conviction. See [references/interpretation.md](references/interpretation.md) for how to read mixed signals.

## Edge Cases

- HIGH conviction + conflicting component = investigate the outlier before acting.
- LOW conviction across the board = stay flat, wait for clarity.
- Combine with `market-overview` for cross-asset context.
