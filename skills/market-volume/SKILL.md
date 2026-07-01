---
name: market-volume
description: "Computes volume analysis: volume ratio vs SMA(20), OBV trend, volume regime classification (CLIMAX / HIGH / NORMAL / LOW). Use for volume confirmation of price moves, divergence detection, and regime filtering. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, volume, obv]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-volume

Volume analysis skill: measures current volume relative to its 20-period average, tracks On-Balance Volume (OBV) trend, and classifies the volume regime.

## Quick Start

```bash
uv run skills/market-volume/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars), enough for SMA(20)-volume baseline and OBV slope. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- **volume_ratio** — current volume divided by SMA(20) of volume
- **obv_trend** — whether OBV is rising or falling relative to its SMA
- **obv_divergence** — bullish/bearish divergence between price and OBV
- **regime** — CLIMAX / HIGH / NORMAL / LOW volume classification

## Volume Regime Interpretation

| Regime | Ratio Range | Meaning |
|--------|-------------|---------|
| CLIMAX | >= 2.5x avg | Potential exhaustion — trend may be ending |
| HIGH   | 1.5–2.5x avg | Strong participation — confirms the move |
| NORMAL | 0.5–1.5x avg | Typical activity — neutral |
| LOW    | < 0.5x avg | Low interest — breakouts may fail |

## Edge Cases

- OBV divergence requires 56+ candles of data to compute reliably.
- Volume ratio over 5.0x can indicate news events or data anomalies — still reported but flagged as extreme.
- Context skill: no directional score or signal — volume confirms or rejects price moves.
- Best used with `market-trend` for trend-aware volume analysis.
