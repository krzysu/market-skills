---
name: strategy-funding-carry
description: "L3 non-directional strategy. Harvests perpetual swap funding rates by entering the side that receives funding. Composes market-basis funding data."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, funding, carry, perps, non-directional, l3]
    category: strategy
    compatibility: "Requires Python 3.12+ and uv"
---

# strategy-funding-carry

L3 non-directional strategy that harvests perpetual swap funding rates for income. Enters the side receiving funding (long when funding is negative, short when funding is positive) and exits when funding normalizes.

## When NOT to use

- Without a mandatory `risk-engine` vet before execution — this skill emits ideas, not orders. Always vet the Intent and get explicit user approval.
- On spot markets — funding rates are a perpetual-swap construct. The strategy needs `fetch_funding_rate` to return a live rate, which requires a perp ticker (e.g. `BTC/USDT` on a CCXT exchange).
- For a directional view — this is a carry trade, not a price-direction call. The entry direction is determined by which side receives funding, not by trend/momentum.
- Without confirming the venue's funding mechanism — funding intervals, settlement, and sign conventions vary by exchange. The strategy reads the raw per-8h rate from `market-basis`/`fetch_funding_rate`.

## Quick Start

```bash
uv run skills/strategy-funding-carry/scripts/run.py BTC/USDT --source ccxt:binance
uv run skills/strategy-funding-carry/scripts/run.py BTC/USDT --source ccxt:binance --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). Use a perp ticker for funding data. |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). Funding rates require `ccxt` or `ccxt:exchange`. |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-basis | Funding rate data via `fetch_funding_rate` (raw per-8h rate, percentile mapping) |
| market-volatility | ATR(14) for stop placement (L1, composed by L3 directly) |

## Entry Logic

- **Direction**: negative perp funding (shorts pay longs) → **LONG**; positive perp funding (longs pay shorts) → **SHORT**. The carry trade enters the side that *receives* funding.
- **Funding extremity → conviction** (absolute per-8h rate thresholds):
  - `|rate| >= 0.001` (0.1% per 8h) → conviction 4
  - `|rate| >= 0.0005` (0.05% per 8h) → conviction 3
  - `|rate| >= 0.0001` (0.01% per 8h) → conviction 2
  - `|rate| < 0.0001` → neutral, no idea
- **Entry**: current price (last close)
- **Stop**: 2 × ATR(14) from entry (long: entry − 2×ATR; short: entry + 2×ATR)
- **Targets**: 3-TP ladder at 1.5R / 2.5R / 4R, with TP3 clamped at the 5% dead-zone boundary (`entry × 1.05` long, `entry × 0.95` short) so low-vol assets still emit an idea

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
  - Each idea carries `version: "v1".."v5"` derived from `conviction` via `analysis.contracts.conviction_version()`
  - Each idea carries `take_profit_ideal` (unrounded construction) and `rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]` (precomputed R:R to each TP via `analysis.contracts.compute_rr_to_tp()`) so consumers can read a canonical R:R without reimplementing the direction-asymmetric formula
  - Each idea is validated against `validate_l3_tp_ladder()` (TP3 ≥ entry × 1.05 long, or ≤ entry × 0.95 short). If validation fails after the clamp, the strategy surfaces the validator's error message as `narrative` so the silent-failure fingerprint doesn't reappear.
- `narrative` — summary for user briefing (funding regime, direction, conviction)

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
