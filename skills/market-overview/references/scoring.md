# Unified Scoring Model — market-overview

## How the 0-100 Score is Computed

Each ticker gets a raw score from four weighted components, then normalized to a 0-100 range:

### Raw Component Scoring

| Component | Max Positive | Max Negative | Weight |
|-----------|-------------|-------------|--------|
| Trend (EMA 21/50) | +2 | -2 | 35% |
| RSI (14) | +2 | -2 | 25% |
| Squeeze Momentum | +2 | -2 | 25% |
| Volume (OBV) | +1 | -1 | 15% |

Total possible range: -185 to +185

### Normalization

```
unified_score = ((raw - (-185)) / (185 - (-185))) * 100
             = ((raw + 185) / 370) * 100
```

This maps the worst possible (-185) to 0 and the best possible (+185) to 100.

### Action Thresholds

| Score Range | Action |
|------------|--------|
| 75 - 100 | STRONG_BUY |
| 55 - 74 | BUY |
| 35 - 54 | WATCH |
| 0 - 34 | AVOID |

### Limitations

- This is a lightweight scoring model for screening, not a trading signal generator.
- Only uses 4 components (EMA, RSI, squeeze, volume) — no macro, funding, or orderbook data.
- All components equally weighted within their bands; no volatility adjustment.
- The model doesn't know about your portfolio, position sizes, or risk tolerance.
