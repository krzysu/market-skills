---
name: market-squeeze
description: "Detects Bollinger Band / Keltner Channel squeeze with momentum direction. Use for pre-breakout compression signals and breakout confirmation. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, squeeze, breakout]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-squeeze

Squeeze momentum indicator: when Bollinger Bands are inside Keltner Channels, volatility is compressing — a breakout is likely. The momentum histogram shows which direction.

Based on John Carter's TTM Squeeze (LazyBear variant).

## Quick Start

```bash
uv run skills/market-squeeze/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars), enough for BB/KC squeeze stability. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- `squeeze_on`: whether BB bands are inside KC bands (compression)
- `momentum`: oscillator value — positive = bullish, negative = bearish
- `direction`: increasing or decreasing momentum
- `signal`: BULLISH, BEARISH, BULLISH FADING, BEARISH FADING, FLAT

## Signal Interpretation

| Squeeze | Momentum | Signal | Meaning |
|---------|----------|--------|---------|
| ON | rising | Squeeze building | Coiling for upside breakout |
| ON | falling | Squeeze building | Coiling for downside breakout |
| OFF | positive | BULLISH | Breakout in progress (up) |
| OFF | negative | BEARISH | Breakout in progress (down) |
| OFF | fading | Respective FADING | Momentum losing steam |

## Edge Cases

- "FADING" signals after a squeeze release may indicate the breakout is exhausting.
- Squeeze fires are strongest when confirmed by volume — use with OBV analysis.
- Requires 50+ days of data for reliable BB/KC calculation.
- Best used as a timing overlay on trend direction from `market-ema` or `market-trend-quality`.
