---
name: run-watchlist
description: "Bulk-run L2 + L3 skills + notes across every ticker in a watchlist basket. Fetches candles once per ticker, runs all patterns + strategies in-process, returns aggregated JSON. Use for morning briefs, batch scans, or basket triage."
version: 0.1.0
metadata:
  hermes:
    tags: [runner, batch, watchlist, l2, l3, scan]
    category: utility
compatibility: "Requires Python 3.12+ and uv"
---

# run-watchlist

The bulk-analysis runner. Driven by [`market-watchlist`](../market-watchlist/) — pass a basket name, a `--tickers` list, or omit for everything.

## When NOT to use

- As a trade signal — it aggregates L2 + L3 + notes into a view; the agent brain still decides and must vet via `risk-engine` + `execution-kraken-*` before acting.
- For a single-skill deep dive — call the individual market-* / strategy-* skill directly for focused output and flags.
- To execute — run-watchlist is analytics-only, never places orders.

Fetches candles **once per ticker**, then runs:

- All 5 L2 pattern skills (`market-accumulation`, `market-breakout`, `market-exhaustion`, `market-liquidity-sweep`, `market-trend-quality`)
- All 7 L3 strategy skills (`strategy-trend-follow`, `strategy-mean-reversion`, `strategy-breakout-confirm`, `strategy-accumulation-swing`, `strategy-exhaustion-fade`, `strategy-funding-carry`, `strategy-liquidity-sweep`)
- Active notes from [`market-notes`](../market-notes/) — auto-included

Notes load by default. This is the "morning brief" use case — you almost always want thesis context alongside verdicts.

## Quick Start

```bash
# All baskets (uses market-watchlist)
uv run skills/run-watchlist/scripts/run.py --json

# One basket
uv run skills/run-watchlist/scripts/run.py crypto_majors

# Ad-hoc ticker list (bare aliases supported)
uv run skills/run-watchlist/scripts/run.py --tickers btc eth xle --json

# L2 only (skip L3)
uv run skills/run-watchlist/scripts/run.py crypto_majors --l2-only

# Skip notes
uv run skills/run-watchlist/scripts/run.py crypto_majors --no-notes

# Custom watchlist file
uv run skills/run-watchlist/scripts/run.py --watchlist /path/to/watchlist.json crypto_majors

# Data source override
uv run skills/run-watchlist/scripts/run.py --tickers BTCUSD ETHUSD --source=yfinance
```

## What it returns

```json
{
  "scope": "basket: crypto_majors",
  "interval": "1d",
  "period": "1y",
  "tickers": {
    "BTCUSD": {
      "metadata": {"tier": 2, "source": "kraken"},
      "l2": {
        "market-accumulation":   {"pattern": {"present": false, ...}, ...},
        "market-breakout":       {"pattern": {...}, ...},
        ...
      },
      "l3": {
        "strategy-trend-follow": {"ideas": [...], "narrative": "..."},
        ...
      },
      "notes": [{"note": "cycle bottom thesis...", "expires": "..."}]
    },
    "ETHUSD": { ... }
  }
}
```

Same shape as running `run-all-l2` and `run-all-l3` separately per ticker, merged into one envelope.

## Why notes are on by default

A "morning brief" is meaningless without the agent's prior context. L3 says LONG conv 4 on `<TICKER>`, but there's an active "wait for $502 breakout" note — that should appear together so the agent brain can reconcile.

If you're running this from a hot loop where notes are noise (e.g. backtest data prep), pass `--no-notes`.

## Tracking-only tickers

Watchlist entries with `"tracking_only": true` are still fetched and surfaced in the output, but the agent brain should skip strategy evaluation for them. The metadata surfaces this flag explicitly:

```bash
uv run skills/run-watchlist/scripts/run.py macro_refs
# SPYUSD
#   meta: tracking-only
#   L2 market-accumulation   no  (n/a, 0/5)
#   ...
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `basket` (positional) | — | Basket name from the watchlist |
| `--tickers` | — | Ad-hoc list (bare aliases supported) |
| `--watchlist PATH` | skill default | Override the watchlist file |
| `--l2-only` / `--l3-only` | both on | Mutually exclusive |
| `--no-notes` | notes on | Skip notes load |
| `--interval=INTERVAL` | `1d` | `1m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. Passed to every fetched candle. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |
| `--source` | auto | Override data provider |
| `--json` | human | Emit JSON envelope to stdout |

Both `--interval` and `--period` are validated against the supported sets in `analysis/intervals.py` — bad values exit 2 with a one-liner. JSON output includes top-level `interval`/`period` so the consumed timeframe is always visible to downstream agents.

Precedence: `--tickers` > `basket` positional > all baskets.

## Scheduled integration

The skill is designed to be invoked on a recurring schedule (e.g. via the host's task scheduler) — `scripts/run.sh` handles the `cd market-skills && uv run python` invocation so any scheduler can reference it directly:

```bash
# Morning brief at 7am
0 7 * * *  bash /path/to/market-skills/skills/run-watchlist/scripts/run.sh crypto_majors --json > /tmp/brief-$(date +\%F).json

# Full watchlist every 4 hours
0 */4 * * *  bash /path/to/market-skills/skills/run-watchlist/scripts/run.sh --json > /tmp/watchlist.json
```

## Library use

```python
from skills.run_watchlist.lib import analyze_ticker
from analysis.notes import load_active

result = analyze_ticker(
    "BTCUSD",
    candles,
    metadata={"tier": 2, "source": "kraken"},
    include_l2=True,
    include_l3=True,
    include_notes=True,
    notes_loader=load_active,
)
```

## Candle cache (opt-in)

This runner bulk-scans a watchlist basket (L2 + L3 + notes). Each invocation
re-fetches candles from the venue unless the OHLC cache is enabled. For cron /
repeated scans, set a TTL so identical `provider:ticker:interval:period`
requests are served from disk:

```bash
MARKET_SKILLS_OHLC_CACHE_TTL=3600 uv run skills/run-watchlist/scripts/run.py crypto_majors
```

`0` (the default) means always fetch live. See the README "Candle cache"
section for the full contract (store path, entry cap, staleness guidance).

## Exit codes

- `0` — success (per-ticker fetch failures appear in output, not as exit errors)
- `1` — fatal: no tickers resolved, bad watchlist path, mutually-exclusive flags
- `2` — invalid usage (missing args)