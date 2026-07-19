---
name: market-trend
description: "Computes comprehensive trend analysis: EMA alignment (FULL_BULL to FULL_BEAR), HH/HL swing structure detection, slope metrics, and price position. Use for trend direction, strength assessment, and structure integrity. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, trend, structure]
    category: market
    layer: L1
    lens: raw EMA alignment + swing structure (closest to the candles)
compatibility: "Requires Python 3.12+ and uv"
---

# market-trend

Comprehensive trend structure analysis combining EMA alignment with swing point pattern recognition.

## When NOT to use

- This is a single indicator (L1), not a trade idea — pair it with `market-trend-quality` (the L2 health lens) or an L3 strategy before acting.
- Swing structure lags price by nature — use it for confirmation, not timing. Do not enter on a single EMA cross.
- Needs 220+ daily candles for EMA(200); with shorter history the long-term read is incomplete (falls back with a warning).

## Layering

Two trend skills do overlapping but distinct work. Use them in this order:

1. **`market-trend`** (this skill) — *raw EMA alignment + HH/HL structure*. Score range -4/+4. Closest to the candles; use as ground truth for direction.
2. **`market-trend-quality`** — *structural health (HEALTHY/WEAKENING/DEGRADING)*. Classifies with sub-signals; the L2 layer that L3 strategies key off. Composes this skill plus market-fibonacci and market-volume internally.

`market-trend-analysis` (composite trend + RSI + squeeze + volume) was deprecated in favour of `market-trend-quality` to eliminate the cross-skill conflict that previously fired Pattern U every tick.

## Quick Start

```bash
uv run skills/market-trend/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars), enough for EMA(200) and HH/HL swing structure. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- **ema_21**, **ema_50**, **ema_100**, **ema_200** — current EMA values
- **alignment** — FULL_BULL / PARTIAL_BULL / TANGLED / PARTIAL_BEAR / FULL_BEAR
- **higher_high**, **higher_low** — swing structure booleans (multi-swing majority, see below)
- **slope_21_pct**, **slope_50_pct** — EMA slope as % change over 5 days
- **crossover** — golden_cross / death_cross / null
- **price_above_emas** — count of EMAs price is above (0–4)
- **score** — -4 (strong downtrend) to +4 (strong uptrend)
- **zone** — bullish / bearish / neutral
- **signal** — STRONG_UPTREND / UPTREND / SIDEWAYS / DOWNTREND / STRONG_DOWNTREND

## Swing structure (HH/HL) detection

`higher_high` and `higher_low` are determined by **majority vote over all detected swing points** in the lookback, not by a single prior-swing compare. For a market to register `higher_high = True`, the most recent swing high must exceed ≥60% of the prior swing highs. Same shape for `higher_low`. If fewer than 2 swing points are detected on either axis, that field is `null`.

The swing-detection window scales with the candle interval to avoid spurious swing detection on noisy intraday series:

| Interval | Swing window |
|----------|--------------|
| `1m`–`30m` | 20 |
| `1h` | 12 |
| `4h` | 8 |
| `1d` | 5 |
| `1wk` | 4 |
| (other) | 5 (daily default) |

## Signal Interpretation

| Score | Signal | Structure |
|-------|--------|-----------|
| +4 | STRONG_UPTREND | FULL_BULL + HH + HL — maximum conviction |
| +3 | UPTREND | FULL_BULL + HH or HL |
| +2 | UPTREND | FULL_BULL or PARTIAL_BULL |
| +1 | UPTREND | PARTIAL_BULL with improving structure |
| 0 | SIDEWAYS | TANGLED or mixed signals |
| -1 to -4 | DOWNTREND | Corresponding bearish structure |

Score composition (no slope-agreement bonus — EMA alignment already encodes slope direction):
- EMA alignment: ±2 (FULL_BULL/FULL_BEAR) or ±1 (PARTIAL_*)
- HH true: +1, HH false: −0.5
- HL true: +1, HL false: −0.5
- Clamped to [-4, +4]

## Edge Cases

- Requires 220+ daily candles for EMA(200); falls back to available EMAs with a warning.
- Swing structure lags price by nature — best for confirmation, not timing.
- Golden/death cross events boost/discount score by 1 level.
- Combine with `market-rsi` for oversold entries in uptrends, `market-volume` for confirmation.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
