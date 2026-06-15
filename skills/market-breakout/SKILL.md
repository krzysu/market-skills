---
name: market-breakout
description: "Detects whether a breakout is real by composing L1 indicators: structure break, volume confirmation, OBV confirmation, squeeze release, and retest holding. Classifications: FRESH, CONFIRMED, STALE, FAILED."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, breakout, momentum]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-breakout

L2 pattern detection skill that composes L1 indicators to determine whether a breakout is genuine or likely to fail.

## Classifications

| Classification | Meaning |
|---|---|
| FRESH | Breakout just initiated (confidence >= 3, no retest yet) |
| CONFIRMED | Breakout confirmed by successful retest of the breakout level |
| STALE | Breakout has been present >10 bars without continuation |
| FAILED | Structure break reversed, price returned to consolidation |

## Sub-signals

| Sub-signal | Weight | Source L1 | Logic |
|---|---|---|---|
| Structure break | 0.35 | market-trend | alignment is FULL_BULL or FULL_BEAR, or signal is STRONG_UPTREND/STRONG_DOWNTREND |
| Volume confirmation | 0.25 | market-volume | volume_ratio > 1.5 |
| OBV confirmation | 0.15 | market-volume | obv_trend == "rising" with bullish, or "falling" with bearish |
| Squeeze release | 0.15 | market-squeeze | squeeze_on is False (released) and direction == "increasing" with bullish momentum |
| Retest holding | 0.10 | market-s-r | sits_on_level is True after break (lagging) |

## Output

- `pattern.present` (bool)
- `pattern.confidence` (1–5)
- `pattern.classification` (FRESH / CONFIRMED / STALE / FAILED)
- `pattern.type` always `"BREAKOUT"`
- `signals` — per-signal `{"present": bool, "weight": float}`
- `input_scores` — raw L1 outputs
- `narrative` — one-sentence explanation
