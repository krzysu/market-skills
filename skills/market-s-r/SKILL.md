---
name: market-s-r
description: "Identifies nearest support and resistance levels from swing highs/lows with distance %, touch counts, and clustered price levels. Use for entry/exit placement, stop positioning, and level quality assessment. Supports any yfinance ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [market, technical-analysis, support, resistance, levels]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-s-r

Support and Resistance analysis from swing point clustering. Identifies key price levels where the market has previously reversed.

## Quick Start

```bash
uv run skills/market-s-r/scripts/run.py AAPL --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. Defaults give daily candles over 1y (~250 bars) for swing-point clustering. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## What it returns

- **nearest_support**, **nearest_resistance** — closest levels below/above price
- **support_distance_pct**, **resistance_distance_pct** — distance from current price
- **support_touches**, **resistance_touches** — how many swing points cluster at that level
- **clustered_levels** — all detected levels with price and touch counts
- **support_count**, **resistance_count** — total levels found in each direction

## Interpretation

| Metric | Meaning |
|--------|---------|
| Distance% | How close price is to a level — tighter = more reactive |
| Touches | More touches = stronger level (3+ is significant) |
| Clustered levels | Nearby swing points that merge into a zone |

## Edge Cases

- Context skill: no directional score or signal.
- Swing point window=3 for broad detection; cluster tolerance=1.5%.
- Levels closer than 0.1% to price are reported as current_price_sits_on_level.
- Requires 20+ candles minimum.

## Extracting multiple structural levels (not just nearest)

The text output only prints the nearest support/resistance (single row). For goal-price placement (TP ladders, stop clusters, position-watchdog level lists), you need **all** clustered levels above/below price with their touch counts — which tells you level strength.

The `--json` output exposes this in `indicators.clustered_levels` as `[{price: float, touches: int}, ...]`. Workflow:

```bash
# Get all resistance levels above current price, sorted by price, with touch counts
uv run skills/market-s-r/scripts/run.py ETHEUR --json \
  | jq '.indicators.clustered_levels | map(select(.price > .[].price)) | sort_by(.price)'
```

Or via `read_file` after saving JSON to disk (per Kraken CLI pitfall — never pipe market-skills JSON to `python3 -c` for parsing):

```bash
uv run skills/market-s-r/scripts/run.py ETHEUR --json > /tmp/sr.json 2>/tmp/sr_err.txt
```

Then `read_file /tmp/sr.json` and grep `clustered_levels`.

**Touch-count thresholds for structural strength:**
- `touches >= 2` — real level, worth watching
- `touches >= 3` — significant, strong S/R
- `touches >= 5` — historical structural level (cycle top/bottom, multi-year consolidation)

**Use cases:**
- Building TP ladders for position-watchdog: pick TPs from levels with `touches >= 3` above current price
- Position stops: pick stops from levels with `touches >= 3` below current price
- Zone identification: consecutive levels within 2% of each other form a zone

See `references/swing-levels-extraction.md` for the full pattern + worked examples.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
