---
name: l3-conviction-scan
description: "Conviction-ranked L3 cross-ticker view. Flattens every L3 idea across one or more watchlist baskets, sorts by conviction, and surfaces the top N. Use for morning briefs, conviction triage, or any LLM scan that needs 'show me the strongest setups first' across the whole watchlist."
version: 0.1.0
metadata:
  hermes:
    tags: [runner, batch, l3, conviction, scan, ranking]
    category: utility
compatibility: "Requires Python 3.12+ and uv"
---

# l3-conviction-scan

Ranks L3 trade ideas across watchlist baskets by conviction. Fetches candles **once per ticker per (basket, interval, period)**, runs all 6 L3 strategies in-process, then flattens every `ideas[]` into a single conviction-sorted table.

## When to use

- Morning briefs and swing scans: "show me the strongest setups across `tier_1` and `tier_2` first."
- After `run-all-l3` to re-shape its per-ticker output into a single ranking.
- Any LLM-driven triage that needs a flat "best N trades right now" view, not the
  per-ticker breakdown that `run-all-l3` / `run-watchlist` print.

## When NOT to use

- For per-ticker L2 + L3 + notes detail — use `run-all-l3` (L3) or
  `run-watchlist` (L2 + L3 + notes). This skill is a *ranking* view on top
  of L3, not a replacement.
- For diagnostic / anomaly detection on the L2 envelope — use `bug-scan`.
- For execution — never pipe conviction-scan output to `execution-kraken-*`
  without re-running the L3 strategy's full narrative + risk vet.

## Quick Start

```bash
# All ideas across tier_1 + tier_2, default 1d/1y
uv run skills/l3-conviction-scan/scripts/run.py tier_1 tier_2

# Top 10, intraday TF, machine-readable
uv run skills/l3-conviction-scan/scripts/run.py tier_1 --interval 4h --period 3mo --top 10 --json

# Include top-5 strategy narratives after the table
uv run skills/l3-conviction-scan/scripts/run.py tier_1 tier_2 --narrative
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `BASKET...` (positional, repeatable) | — | One or more watchlist basket names. |
| `--interval` | `1d` | `1m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |
| `--source` | auto | Data provider override (`yfinance`, `kraken`, `hl`, `ccxt:binance`). |
| `--watchlist` | skill default | Path to watchlist.json (env: `MARKET_SKILLS_WATCHLIST_PATH`). |
| `--top N` | unset | Cap output rows to the top N by conviction. |
| `--narrative` | off | After the table, print the top-5 strategy narratives. |
| `--json` | off | Emit machine-readable JSON envelope to stdout. |

Both `--interval` and `--period` are validated via `analysis.intervals.validate_timeframe` — bad values exit 2 with a friendly error.

## Output

### Text (default)

```text
TF   BASKET    TICKER     STRATEGY                  DIR   CONV ENTRY      STOP      TP1       TP2       RRT2   VETO
---- ---------- ---------- -------------------------- ----- ---- --------- --------- --------- --------- ------ -----
1d   tier_1    BTCUSD     strategy-trend-follow      long  5    67500.00  66200.00  70500.00  73000.00  2.44
1d   tier_1    AAPL       strategy-breakout-confirm  long  4     187.20    182.50    194.00    199.50  2.31
4h   tier_2    HYPEUSD    strategy-trend-follow      long  4      24.10     22.80     26.40     28.10  2.29
1d   tier_1    ETHUSD     strategy-mean-reversion    long  3    3450.00   3380.00   3550.00   3620.00  1.78   mature-move
```

`RRT2` is the deployment-ready R:R to TP2 (the runner TP1 here is just the
scaling-out target; see `run-all-l3` output schema). The `VETO` column
shows soft veto tags emitted by L3 strategies (`mature-move`,
`asset-class-scaled`, etc.) — these are correct L3 behaviour, not bugs.
Read them, don't strip them.

### JSON

```json
{
  "interval": "1d",
  "period": "1y",
  "baskets": ["tier_1", "tier_2"],
  "count": 42,
  "ideas": [
    {
      "ticker": "BTCUSD",
      "label": "Bitcoin",
      "tier": 2,
      "asset_class": "majors",
      "strategy": "strategy-trend-follow",
      "direction": "long",
      "conviction": 5,
      "version": "v5",
      "entry": 67500.0,
      "stop": 66200.0,
      "tp1": 70500.0,
      "tp2": 73000.0,
      "tp3": 76000.0,
      "rr_tp1": 1.22,
      "rr_tp2": 2.44,
      "rr_tp3": 3.94,
      "mover_pct": 18.4,
      "veto": [],
      "narrative": "Healthy uptrend with higher-low structure..."
    }
  ]
}
```

## How it works

1. Resolve each positional basket via `analysis.watchlist.by_category` (the
   same `market-watchlist` resolver the other batch runners use).
2. For each (basket, ticker), `fetch_ohlc` once, then call
   `skills.run-all-l3.lib.analyze` in-process with the watchlist's
   `asset_class` so scaled-maturity strategies (perp_dex, low_float,
   ai_infra) score correctly. No subprocess, no host-specific paths.
3. Walk the envelope (`tickers[t].strategies[s].ideas[]`) and yield one
   flat row per idea. Tolerates both the canonical `rr_to_tp: list[float]`
   (preferred) and the legacy `rr_to_tp2` / `rr` scalar fallback.
4. Sort by `conviction desc` (ties broken by ticker), optionally cap to
   `--top N`, render.

## Layer rules

- **L3 only.** Does not run L2 patterns or load notes — those are
  `run-watchlist`'s job. Conviction scan is the cheap, fast "what's the
  top N?" view.
- **Advisory only.** Like every other batch runner, conviction-scan
  produces a view; it does not gate trades. The risk engine + execution
  prompt remain the safety layer.
- **In-process.** Imports `skills.run-all-l3.lib.analyze` via
  `analysis.skill_loader.load_skill` — never shells out, never reads
  host-specific config.
- Asset-class-scaled conviction (perp_dex floor 3) means `conv < 3` is
  not a defect, just a veto signal in the swing-scan playbook. This skill
  surfaces everything; downstream agents decide what to act on.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count (findings for `bug-scan`, ranked ideas for `l3-conviction-scan`, total journal entries for `daily-trade-pick`), `help[]` is contextual next-step command templates.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
