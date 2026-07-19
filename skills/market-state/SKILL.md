name: market-state
description: "Cross-skill session-start dashboard that composes cached state from market-macro, market-valuation, market-movers, run-watchlist, l3-conviction-scan, and market-notes. One-call read of the world; no I/O at runtime."
version: 0.1.0
metadata:
  hermes:
    tags: [market, dashboard, session-start, meta]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-state

Session-start cross-skill dashboard. Reads the per-skill state caches written by phase 3 home views and composes a single AXI envelope. Designed to be the LLM's first call at session start.

## When NOT to use

- As a live analysis tool — it only reads cached state from other skills; if any source shows "no cache"/stale, run that source skill (`market-macro`, `run-watchlist`, `l3-conviction-scan`, etc.) before relying on the dashboard.
- For trade decisions — it is a dashboard/overview, not a signal. Drill into the underlying skill for the actionable read.
- As a fresh-data source — it does no I/O at runtime; use `--refresh` only for the macro TTL, other sources still come from disk.

## Quick Start

```bash
uv run skills/market-state/scripts/run.py --json
```

The first call typically shows several "no cache" freshness entries; populate them by running the source skills (any of `market-macro`, `market-valuation`, `market-movers`, `run-watchlist <basket>`, `l3-conviction-scan <basket>`, `market-notes list`) and re-run `market-state --json`.

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--json` | human | Emit the AXI envelope to stdout. |
| `--refresh` | off | Bypass the macro TTL cache and re-fetch the regime before composing the dashboard. Other sources still read from their on-disk cache; this flag is the explicit "I know the macro is stale" override. The home-view stdout / JSON `help[]` both list which sources were refreshed in this run. |
| `--fields=<csv>` | `summary,freshness,sources_cached,sources_total` | Project the dashboard. |
| `--full` | — | Full payload including slim source views. |

## Sources

The dashboard composes 6 cached sources. Each source contributes a slim view (a `summary` one-liner plus a handful of headline fields) and a `cached_at` ISO timestamp.

| Source | Skill | Headline fields |
|--------|-------|-----------------|
| regime | `market-macro` | `risk_appetite` / `liquidity` / `sentiment` / `missing_inputs[]` |
| valuation | `market-valuation` | `regime` (CAPE z-score) |
| movers | `market-movers` | `gainers_count` / `losers_count` / `trending_count` |
| watchlist | `run-watchlist` | `summary` / `fired_skills_total` / `ideas_count` |
| conviction | `l3-conviction-scan` | `total` / `baskets` / `top_ideas` (top 5) |
| notes | `market-notes` | `pair_count` / `summary` |

When the macro regime fetch is degraded (any source fails), `regime.missing_inputs` lists the structured input names that failed — `fng`, `vix`, `dxy`, `us10y`, `btc_dominance`, `total_mcap_usd`. LLM agents can branch on `missing_inputs` instead of parsing `regime_note`.

The top-level `freshness` map shows each source's age (`3h ago`, `2d ago`, `no cache`). When `sources_cached` is below `sources_total`, refresh stale sources by running them with `--json` before relying on the dashboard.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. `count` is `sources_cached` (how many of the 6 sources have a fresh cache). Default schema is the dashboard headline; pass `--fields=<csv>` to project or `--full` for the full payload (includes every source's slim view). `errors[]` lists missing-cache source labels so the LLM can decide which to refresh first. `help[]` lists the next-step commands. Lib.py return shape is the dashboard dict; the envelope wraps it at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached dashboard from `$XDG_DATA_HOME/market-skills/market-state_last.json`) instead of a usage error. `render_home_view()` is the underlying helper; `cache_run_result(__file__, result)` writes the cache after every successful `--json` run. Errors (`"error"` key in the result) are NOT cached — the home view always reflects the last healthy run.
