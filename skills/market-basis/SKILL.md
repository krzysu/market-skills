---
name: market-basis
description: "Perpetual swap market structure: funding rate, spot-perp basis, and squeeze/RSI on both sides. Use to gauge positioning cost, directional bias in perp markets, and spot-perp divergence. Requires --source ccxt or ccxt:exchange."
compatibility: "Requires Python 3.12+ and uv"
---

# market-basis

Analyzes perpetual swap market structure for any ticker on supported CCXT exchanges.
Reports funding rate (current and historical average), spot-perp basis, and compares
squeeze momentum and RSI between spot and perpetual markets.

## Quick Start

```bash
uv run skills/market-basis/scripts/run.py BTC/USDT --source ccxt:binance --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | `BTC/USDT` | Perp ticker (`BTC/USDT`, `ETH/USDT`, etc.). Without a `/`, treated as spot. |
| `--source=PROVIDER` | `ccxt:binance` | CCXT provider and exchange. Other CCXT exchanges: `ccxt:bybit`, `ccxt:okx`, `ccxt:bitfinex`, etc. |
| `--interval=INTERVAL` | `1d` | `1m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `6mo` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |
| `--json` | human | Emit JSON to stdout. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Period defaults to `6mo` (vs `1y` elsewhere) because basis/funding analysis is most meaningful on the recent half-year.

## What it returns

- **Funding**: current rate, 30-period average, annualized APR
- **Basis**: perp vs spot price difference in absolute and percentage terms
- **Spot analysis**: squeeze signal, RSI, trend (EMA21/50)
- **Perp analysis**: squeeze signal, RSI, trend (EMA21/50)
- **Divergence**: flags when spot and perp disagree on squeeze direction or RSI zone

## Signal Interpretation

| Signal | Meaning |
|--------|---------|
| Funding > +0.01% / 8h | Longs paying shorts — bullish positioning, potential crowded trade |
| Funding < -0.01% / 8h | Shorts paying longs — bearish positioning, potential short squeeze |
| Positive basis (contango) | Perp above spot — bullish demand for leveraged exposure |
| Negative basis (backwardation) | Perp below spot — bearish or hedging pressure |
| Squeeze divergence | Spot and perp show different squeeze signals — structural disagreement |

## Edge Cases

- Funding rates only available on perp-capable exchanges (binance, bybit, okx, etc.)
- Some exchanges update funding every 8h, others every 1h or 4h
- Lightly traded perps may have stale or erratic funding data
- Best paired with `market-trend-quality` and `market-squeeze` for directional context

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
