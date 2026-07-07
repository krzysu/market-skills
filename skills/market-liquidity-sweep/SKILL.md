---
name: market-liquidity-sweep
description: "Detects whether a fakeout occurred: price wicks through S/R without close beyond, immediate reclaim, old high/low taken then reversed, above-avg volume on rejection candle. Classifications: SUPPORT_SWEEP, RESISTANCE_SWEEP, DOUBLE_TEST."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, liquidity, sweep, fakeout]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-liquidity-sweep

L2 pattern detection skill that composes L1 indicators to detect liquidity sweeps and fakeouts.

## Quick Start

```bash
uv run skills/market-liquidity-sweep/scripts/run.py SPY
uv run skills/market-liquidity-sweep/scripts/run.py SPY --json
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

| Sub-signal | Weight | Source L1 | Logic |
|---|---|---|---|
| Wick through S/R without close beyond | 0.35 | market-s-r | sits_on_level is False; low of last 3 bars below nearest_support or high above nearest_resistance; close back within range |
| Immediate reclaim | 0.30 | market-s-r | After wick-through, latest close is back above support or below resistance |
| Old high/low taken then reversed | 0.20 | market-trend | Last bar high exceeded swing_high but close below it, or low broke swing_low but close above it |
| Above-avg volume on rejection candle | 0.15 | market-volume | Highest volume in last 5 bars > 1.5× SMA(20) of volume |

## Classifications

| Classification | Meaning |
|---|---|
| SUPPORT_SWEEP | Price wicked below support then reclaimed |
| RESISTANCE_SWEEP | Price wicked above resistance then reclaimed |
| DOUBLE_TEST | Old high/low taken and reversed without clear S/R direction |

## Output

- `pattern.present` (bool)
- `pattern.confidence` (1–5)
- `pattern.classification` (SUPPORT_SWEEP / RESISTANCE_SWEEP / DOUBLE_TEST)
- `pattern.type` always `"SWEEP"`
- `signals` — per-signal `{"present": bool, "weight": float}`
- `input_scores` — raw L1 outputs
- `narrative` — one-sentence explanation

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
