---
name: strategy-accumulation-swing
description: "L3 swing strategy. Enters on Wyckoff accumulation patterns within healthy trends. Composes accumulation and trend-quality."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, accumulation, swing, wyckoff, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-accumulation-swing

L3 swing strategy that identifies Wyckoff accumulation patterns (spring, reaccumulation) within healthy trends and enters for multi-swing moves.

## Quick Start

```bash
uv run skills/strategy-accumulation-swing/scripts/run.py SPY
uv run skills/strategy-accumulation-swing/scripts/run.py SPY --json
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
| market-accumulation | Detect accumulation pattern (spring, reaccumulation, UTAD) |
| market-trend-quality | Confirm trend health to avoid catching falling knives |

## Entry Logic

- **Long**: accumulation detected (spring or reaccumulation) + trend quality is HEALTHY_UPTREND or WEAKENING (improving)
- **Stop**: below spring low
- **Targets**: 2R, 3R where R = entry - stop

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
  - Each idea carries `version: "v1".."v5"` derived from `conviction` via `analysis.contracts.conviction_version()`
  - Each idea carries `take_profit_ideal` (unrounded construction) and `rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]` (precomputed R:R to each TP via `analysis.contracts.compute_rr_to_tp()`) so consumers can read a canonical R:R without reimplementing the direction-asymmetric formula
  - Each idea is validated against `validate_l3_tp_ladder()` (TP3 ≥ entry × 1.05 long, or ≤ entry × 0.95 short)
- `narrative` — summary for user briefing

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
