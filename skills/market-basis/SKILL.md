---
name: market-basis
description: "Perpetual swap market structure: funding rate, spot-perp basis, and squeeze/RSI on both sides. Use to gauge positioning cost, directional bias in perp markets, and spot-perp divergence. Requires --source ccxt or ccxt:exchange."
compatibility: "Requires Python 3.12+ and uv"
---

# market-basis

Analyzes perpetual swap market structure for any ticker on supported CCXT exchanges.
Reports funding rate (current and historical average), spot-perp basis, and compares
squeeze momentum and RSI between spot and perpetual markets.

## Quick Start

```bash
uv run skills/market-basis/scripts/run.py BTC/USDT --source ccxt:binance --json
```

## What it returns

- **Funding**: current rate, 30-period average, annualized APR
- **Basis**: perp vs spot price difference in absolute and percentage terms
- **Spot analysis**: squeeze signal, RSI, trend (EMA21/50)
- **Perp analysis**: squeeze signal, RSI, trend (EMA21/50)
- **Divergence**: flags when spot and perp disagree on squeeze direction or RSI zone

## Signal Interpretation

| Signal | Meaning |
|--------|---------|
| Funding > +0.01% / 8h | Longs paying shorts — bullish positioning, potential crowded trade |
| Funding < -0.01% / 8h | Shorts paying longs — bearish positioning, potential short squeeze |
| Positive basis (contango) | Perp above spot — bullish demand for leveraged exposure |
| Negative basis (backwardation) | Perp below spot — bearish or hedging pressure |
| Squeeze divergence | Spot and perp show different squeeze signals — structural disagreement |

## Edge Cases

- Funding rates only available on perp-capable exchanges (binance, bybit, okx, etc.)
- Some exchanges update funding every 8h, others every 1h or 4h
- Lightly traded perps may have stale or erratic funding data
- Best paired with `market-trend-analysis` and `market-squeeze` for directional context
