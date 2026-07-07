---
name: strategy-liquidity-sweep
description: "L3 sweep-following strategy. Enters after liquidity sweeps with accumulation confirmation. Composes liquidity-sweep, accumulation, and volume."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, liquidity, sweep, crypto, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-liquidity-sweep

L3 strategy that enters after liquidity sweeps when accumulation confirms smart money is positioning the opposite direction. Works especially well on crypto where stop hunts are frequent.

## Quick Start

```bash
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD --json
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

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-liquidity-sweep | Detect support/resistance sweeps and double tests |
| market-accumulation | Confirm accumulation after the sweep |
| market-volume | Volume confirmation of reversal |

## Entry Logic

- **Long**: support sweep detected + accumulation pattern + volume confirms reversal
- **Short**: resistance sweep detected + distribution signals + volume confirms rejection
- **Stop**: below sweep low (long) / above sweep high (short)
- **Targets**: 2R, 3R

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
  - Each idea carries `version: "v1".."v5"` derived from `conviction` via `analysis.contracts.conviction_version()`
  - Each idea carries `take_profit_ideal` (unrounded construction) and `rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]` (precomputed R:R to each TP via `analysis.contracts.compute_rr_to_tp()`) so consumers can read a canonical R:R without reimplementing the direction-asymmetric formula
  - Each idea is validated against `validate_l3_tp_ladder()` (TP3 ≥ entry × 1.05 long, or ≤ entry × 0.95 short)
- `narrative` — summary for user briefing

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.
