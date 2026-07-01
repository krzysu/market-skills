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

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## Sub-Signals

| Sub-signal | Weight | Source L1 |
|---|---|---|
| Volume climax (volume_ratio >= 2.5 or regime == CLIMAX) | 0.30 | market-volume |
| RSI extreme (rsi < 30 or rsi > 70) | 0.25 | computed directly via analysis.indicators |
| Narrowing range (volatility regime == LOW) | 0.20 | market-volatility |
| Momentum divergence (histogram_flip exists) | 0.15 | market-macd |
| Sentiment extreme (fear_greed < 25 or > 75) | 0.10 | market-fear-greed (optional) |

## Classifications

- **CAPITULATION_BOTTOM**: RSI oversold + volume climax
- **BLOWOFF_TOP**: RSI overbought + volume climax
- **IMPULSE_EXHAUSTION**: momentum divergence detected
- **PULLBACK_EXHAUSTED**: general exhaustion pattern present

## Trigger

`pattern.present` is True when at least 2 sub-signals are present AND their
combined weight exceeds 0.30. The previous `weighted_sum / total_weight >= 0.5`
threshold silently dropped 2-sub combinations at `weighted_sum` in
(0.30, 0.50) — e.g. `rsi_extreme + momentum_divergence` (0.4445) and
`narrowing_range + momentum_divergence` (0.3889) — into `present=False`
while the sub-signals were populated.

`confidence` is `round(weighted_sum * 5)`, clamped to `[1, 5]`.

## Output

Returns pattern presence, confidence (1-5), classification, sub-signal states, input scores from each L1, and a one-sentence narrative.
