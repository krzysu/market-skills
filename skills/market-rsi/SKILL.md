---
name: market-rsi
description: "Computes RSI(14) momentum oscillator for a ticker. Use for oversold/overbought identification, DCA entry timing, or momentum confirmation. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, rsi]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-rsi

Computes the Relative Strength Index (RSI) using Wilder smoothing on daily OHLC from Yahoo Finance.

## Quick Start

```bash
uv run skills/market-rsi/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars). For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- RSI(14) value
- Classification into oversold/neutral/overbought zones
- 7-day RSI delta and trend direction (rising/falling/stable)
- Score: +2 (oversold, buy) to -2 (overbought, skip)

## Signal Interpretation

| RSI Range | Zone | Signal | Score |
|-----------|------|--------|-------|
| < 30 | Oversold | Strong buy / accumulation | +2 |
| 30-40 | Approaching Oversold | Buy signal | +1 |
| 40-60 | Neutral | No signal | 0 |
| 60-70 | Approaching Overbought | Caution / reduce | -1 |
| > 70 | Overbought | Skip or trim | -2 |

## Edge Cases

- RSI can remain overbought in strong uptrends — combine with `market-ema` for context.
- RSI can remain oversold in strong downtrends — don't buy blindly.
- Requires 30+ days of data minimum.
- RSI is a single oscillator — use `market-trend-quality` for a full picture.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.
