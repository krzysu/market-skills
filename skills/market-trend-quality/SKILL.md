---
name: market-trend-quality
description: "Assesses trend health by composing L1 indicators: EMA alignment, HH/HL integrity, pullback depth, impulse vs retrace ratio, and volume confirmation on impulse bars. Classifications: HEALTHY_UPTREND, HEALTHY_DOWNTREND, WEAKENING, DEGRADING, TANGLED."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, trend, quality, health]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-trend-quality

L2 pattern detection skill that composes L1 indicators to assess the health and quality of a trend.

## Quick Start

```bash
uv run skills/market-trend-quality/scripts/run.py SPY
uv run skills/market-trend-quality/scripts/run.py SPY --json
```

## Sub-signals

| Sub-signal | Weight | Source L1 | Logic |
|---|---|---|---|
| EMA alignment | 0.25 | market-trend | alignment field: FULL_BULL, FULL_BEAR, etc. |
| HH/HL integrity | 0.25 | market-trend | higher_high and higher_low both intact for uptrend, both broken for downtrend |
| Pullback depth (shallow vs deep) | 0.20 | market-trend + market-fibonacci | Compare price distance to fib support levels to gauge retracement depth |
| Impulse vs retrace ratio | 0.15 | market-trend | Signal direction strength and score magnitude |
| Volume confirmation on impulse bars | 0.15 | market-volume | volume_ratio > 1.0 and obv_trend confirms direction |

## Classifications

| Classification | Meaning |
|---|---|
| HEALTHY_UPTREND | Strong uptrend with score >= 3, intact HH/HL, and bullish EMA alignment |
| HEALTHY_DOWNTREND | Strong downtrend with score <= -3, broken HH/HL, and bearish EMA alignment |
| WEAKENING | Trend score 1-2 or -1 to -2 with conflicting sub-signals |
| DEGRADING | HH/HL structure breaking down and EMA alignment becoming tangled |
| TANGLED | No clear alignment or directional conviction |

## Output

- `pattern.present` (bool)
- `pattern.confidence` (1–5)
- `pattern.classification` (HEALTHY_UPTREND / HEALTHY_DOWNTREND / WEAKENING / DEGRADING / TANGLED)
- `pattern.type` always `"TREND_QUALITY"`
- `signals` — per-signal `{"present": bool, "weight": float}`
- `input_scores` — raw L1 outputs
- `narrative` — one-sentence explanation
