---
name: market-exhaustion
description: "Detects whether a price move is about to end by composing L1 indicators: volume climax, RSI extremes, narrowing range, momentum divergence, and sentiment extremes. Classifications: CAPITULATION_BOTTOM, BLOWOFF_TOP, IMPULSE_EXHAUSTION, PULLBACK_EXHAUSTED."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, exhaustion, momentum]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-exhaustion

L2 pattern detection skill that composes L1 indicators to determine whether a price move is approaching exhaustion.

## Quick Start

```bash
uv run skills/market-exhaustion/scripts/run.py SPY
uv run skills/market-exhaustion/scripts/run.py SPY --json
```

## Sub-Signals

| Sub-signal | Weight | Source L1 |
|---|---|---|
| Volume climax (volume_ratio >= 2.5 or regime == CLIMAX) | 0.30 | market-volume |
| RSI extreme (rsi < 30 or rsi > 70) | 0.25 | computed directly via lib.indicators |
| Narrowing range (volatility regime == LOW) | 0.20 | market-volatility |
| Momentum divergence (histogram_flip exists) | 0.15 | market-macd |
| Sentiment extreme (fear_greed < 25 or > 75) | 0.10 | market-fear-greed (optional) |

## Classifications

- **CAPITULATION_BOTTOM**: RSI oversold + volume climax
- **BLOWOFF_TOP**: RSI overbought + volume climax
- **IMPULSE_EXHAUSTION**: momentum divergence detected
- **PULLBACK_EXHAUSTED**: general exhaustion pattern present

## Output

Returns pattern presence, confidence (1-5), classification, sub-signal states, input scores from each L1, and a one-sentence narrative.
