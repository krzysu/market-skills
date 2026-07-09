---
name: market-fibonacci
description: "Computes Fibonacci retracement levels (0.236/0.382/0.5/0.618/0.786) and extensions (1.272/1.618) from the most recent swing high/low. Use for identifying potential support/resistance zones and price targets. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, fibonacci, levels]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-fibonacci

Fibonacci retracement and extension analysis based on the most recent significant swing high and swing low.

## Quick Start

```bash
uv run skills/market-fibonacci/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars), enough for a recent swing high/low pair. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- **fib_levels** — dict of all retracement and extension levels
- **swing_high**, **swing_low** — the identified swing points
- **current_position** — above_swing_high / below_swing_low / inside_swing
- **nearest_fib_support** — closest fib level below current price
- **nearest_fib_resistance** — closest fib level above current price
- **nearest_fib_distance_pct** — distance to nearest fib level

## Interpretation

| Key Level | Significance |
|-----------|-------------|
| 0.382 | Shallow retracement — trend may continue |
| 0.5 | Psychological midpoint — watch for reversal |
| 0.618 | Golden ratio — strongest retracement level |
| 0.786 | Deep retracement — trend may be ending |
| 1.272 / 1.618 | Extension targets if price breaks beyond swing |

## Edge Cases

- Context skill: no directional score or signal.
- Swing point detection uses a 5-bar window — less sensitive than parameterized versions.
- Requires 25+ candles minimum for reliable swing identification.
- Combine with `market-s-r` for multi-level confluence confirmation.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
