---
name: market-volatility
description: "Computes realized volatility (7d/30d annualized), percentile rank, regime classification (LOW / NORMAL / HIGH / EXTREME), and volatility trend (spiking / compressing / stable). Use for position sizing, stop placement, and regime-aware analysis. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, volatility, risk]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-volatility

Volatility analysis skill: measures annualized realized volatility over 7-day and 30-day windows, ranks it historically, and classifies the current regime.

## When NOT to use

- This is a context/sizing input, not a directional signal — it returns a regime (LOW/HIGH/EXTREME), never a buy/sell call. Pair with a directional read before acting.
- Use it to size stops and position (wide stops in EXTREME, tight in LOW), not to pick direction.
- For volatility compression/expansion cycle timing, pair with `market-squeeze`; this skill only ranks the current regime.

## Quick Start

```bash
uv run skills/market-volatility/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars) for the 30d percentile rank. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- **realized_vol_7d** — 7-day annualized volatility (%)
- **realized_vol_30d** — 30-day annualized volatility (%)
- **percentile_rank_30d** — where current 30d vol sits in the 1y history (0–100)
- **regime** — LOW / NORMAL / HIGH / EXTREME
- **trend** — spiking / compressing / stable

## Volatility Regime Interpretation

| Regime | Percentile | Meaning |
|--------|------------|---------|
| EXTREME | >= 90th | Crisis or euphoria — wide stops, reduce size |
| HIGH   | 75–90th | Elevated — widen stops |
| NORMAL | 25–75th | Typical — standard parameters |
| LOW    | < 25th | Quiet — tighten stops, expect breakout |

## Edge Cases

- Requires at least 30 daily candles for realized_vol_30d; falls back to shorter windows.
- Percentile rank computed over the full available history (up to 1y).
- Context skill: no directional score or signal.
- Combine with `market-squeeze` for volatility compression / expansion cycle.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
