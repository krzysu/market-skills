---
name: market-macd
description: "Computes MACD(12,26,9): MACD line, signal line, histogram, histogram flip direction, and signal classification (BULLISH / BEARISH / crossovers). Use for momentum confirmation, divergence detection, and trend direction changes. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, macd, momentum]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-macd

MACD (Moving Average Convergence Divergence) momentum indicator using standard parameters 12/26/9.

## Quick Start

```bash
uv run skills/market-macd/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars), enough for MACD(12,26,9) warm-up. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- **macd_line** — fast EMA(12) minus slow EMA(26)
- **signal_line** — EMA(9) of the MACD line
- **histogram** — MACD line minus signal line
- **histogram_direction** — rising or falling vs previous bar
- **histogram_flip** — positive→negative (bearish) or negative→positive (bullish)
- **signal** — BULLISH, BEARISH, BULLISH_CROSS, BEARISH_CROSS, or NEUTRAL
- **score** — +2 (strong bullish) to -2 (strong bearish)
- **zone** — bullish / bearish / neutral

## Signal Interpretation

| Condition | Signal | Score |
|-----------|--------|-------|
| Histogram > 0, MACD > Signal, rising | BULLISH | +2 |
| Histogram > 0, MACD > Signal | BULLISH | +1 |
| MACD crossed above Signal (bullish cross) | BULLISH_CROSS | +1 |
| Histogram < 0, MACD < Signal | BEARISH | -1 |
| Histogram < 0, MACD < Signal, falling | BEARISH | -2 |
| MACD crossed below Signal (bearish cross) | BEARISH_CROSS | -1 |

## Edge Cases

- Requires 35+ daily candles for reliable MACD calculation.
- Histogram flips signal potential trend changes before price confirms.
- Best used with `market-trend` for trend context and `market-volume` for confirmation.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
