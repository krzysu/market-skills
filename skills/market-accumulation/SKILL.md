---
name: market-accumulation
description: "Detects whether smart money is positioning by composing L1 indicators: spring/shakeout, absorption, sign of strength, reaccumulation, and low volatility after distribution. Classifications: SPRING, REACCUMULATION, DISTRIBUTION, UTAD."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, accumulation, smart-money]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-accumulation

L2 pattern detection skill that composes L1 indicators to determine whether smart money is accumulating a position.

## Quick Start

```bash
uv run skills/market-accumulation/scripts/run.py SPY
uv run skills/market-accumulation/scripts/run.py SPY --json
```

## Sub-Signals

| Sub-signal | Weight | Source L1 |
|---|---|---|
| Spring/shakeout (dip below support + reclaim) | 0.30 | market-s-r + market-volume |
| Absorption (high volume, flat price) | 0.20 | market-volume + market-volatility |
| Sign of strength (high vol up after basing) | 0.20 | market-volume + market-trend |
| Reaccumulation (after initial markup) | 0.15 | market-trend |
| Low volatility after distribution | 0.15 | market-volatility |

## Classifications

- **SPRING**: Spring/shakeout + absorption — price faked below support then reclaimed with volume
- **REACCUMULATION**: Reaccumulation + sign of strength — pullback in uptrend with institutional buying
- **DISTRIBUTION**: Sign of strength + absorption with bearish trend — smart money distributing
- **UTAD**: Low volatility after prior high volatility — upthrust after distribution

## Output

Returns pattern presence, confidence (1-5), classification, sub-signal states, input scores from each L1, and a one-sentence narrative.
