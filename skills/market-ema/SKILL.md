---
name: market-ema
description: "Computes moving averages (EMA 21/50/100/200) for a ticker. Detects trend alignment, golden/death crosses, and EMA slope. Use for trend direction, support/resistance proxy, or DCA timing. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, ema]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-ema

Computes exponential moving averages and trend structure from daily OHLC data via Yahoo Finance.

## Quick Start

```bash
uv run skills/market-ema/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars), enough for EMA(200). For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d (script warns to stderr when a combo is likely to truncate).

## What it returns

- Current price vs EMA(21), EMA(50), EMA(100), EMA(200)
- Alignment: FULL_BULL, PARTIAL_BULL, TANGLED, PARTIAL_BEAR, FULL_BEAR
- Price position: how many EMAs is price above (0-4)
- Slope of EMA(21) and EMA(50) as % change over 5 days
- Crossover: golden_cross or death_cross if 21/50 crossed within last 5 days

## Signal Interpretation

| Alignment | Price vs EMAs | Signal |
|-----------|--------------|--------|
| FULL_BULL + above all 4 | STRONG UPTREND — DCA with conviction |
| PARTIAL_BULL + above 3+ | UPTREND — mostly bullish structure |
| FULL_BEAR + above 0-1 | DOWNTREND — bearish, DCA into weakness |
| TANGLED / mixed | TRANSITION — wait for clarity |

Golden cross (21 crossing above 50) is bullish reversal. Death cross is bearish.

## Edge Cases

- Needs 220+ daily candles for EMA(200). Tickers with less history get an error.
- EMA alignment lags price — best used for structure, not timing.
- Combine with `market-rsi` for oversold entries aligned with trend.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
