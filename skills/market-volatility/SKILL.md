---
name: market-volatility
description: "Computes realized volatility (7d/30d annualized), percentile rank, regime classification (LOW / NORMAL / HIGH / EXTREME), and volatility trend (spiking / compressing / stable). Use for position sizing, stop placement, and regime-aware analysis. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, volatility, risk]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-volatility

Volatility analysis skill: measures annualized realized volatility over 7-day and 30-day windows, ranks it historically, and classifies the current regime.

## Quick Start

```bash
uv run skills/market-volatility/scripts/run.py AAPL --json
```

## What it returns

- **realized_vol_7d** — 7-day annualized volatility (%)
- **realized_vol_30d** — 30-day annualized volatility (%)
- **percentile_rank_30d** — where current 30d vol sits in the 1y history (0–100)
- **regime** — LOW / NORMAL / HIGH / EXTREME
- **trend** — spiking / compressing / stable

## Volatility Regime Interpretation

| Regime | Percentile | Meaning |
|--------|------------|---------|
| EXTREME | >= 90th | Crisis or euphoria — wide stops, reduce size |
| HIGH   | 75–90th | Elevated — widen stops |
| NORMAL | 25–75th | Typical — standard parameters |
| LOW    | < 25th | Quiet — tighten stops, expect breakout |

## Edge Cases

- Requires at least 30 daily candles for realized_vol_30d; falls back to shorter windows.
- Percentile rank computed over the full available history (up to 1y).
- Context skill: no directional score or signal.
- Combine with `market-squeeze` for volatility compression / expansion cycle.
