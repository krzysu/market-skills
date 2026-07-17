---
name: market-watchlist
description: "Asset registry — named baskets of tickers with metadata (source, tier, tracking_only, hl_proxy). Drives bulk analysis for run-watchlist, run-all-l2, run-all-l3. JSON config keyed by basket name."
version: 0.1.0
metadata:
  hermes:
    tags: [watchlist, baskets, registry, tickers]
    category: utility
compatibility: "Requires Python 3.12+ and uv"
---

# market-watchlist

The asset registry. Named **baskets** of tickers, each entry carrying metadata: provider, tier, tracking-only flag, HL proxy, label, free-form comment. Drives bulk analysis for `run-watchlist`, `run-all-l2`, `run-all-l3`. Replaces the asset-list part of the old `config.json` (execution-side knobs stay where they are).

Data file is **gitignored** at `skills/market-watchlist/data/watchlist.json`. Shipped example: `skills/market-watchlist/examples/watchlist.example.json`.

## Quick Start

```bash
# First time: copy the example and edit
cp skills/market-watchlist/examples/watchlist.example.json \
   skills/market-watchlist/data/watchlist.json

# Inspect
uv run skills/market-watchlist/scripts/run.py list
uv run skills/market-watchlist/scripts/run.py show crypto_majors
uv run skills/market-watchlist/scripts/run.py tickers
uv run skills/market-watchlist/scripts/run.py tickers crypto_majors
uv run skills/market-watchlist/scripts/run.py resolve btc       # → BTCUSD
uv run skills/market-watchlist/scripts/run.py resolve xle       # → XLExUSD
uv run skills/market-watchlist/scripts/run.py validate

# Machine output
uv run skills/market-watchlist/scripts/run.py --json list
uv run skills/market-watchlist/scripts/run.py --json show defi

# Custom file location (--watchlist is an alias of --config, used by
# run-watchlist and position-watchdog for cross-tool consistency)
uv run skills/market-watchlist/scripts/run.py --config /path/to/watchlist.json list
uv run skills/market-watchlist/scripts/run.py --watchlist /path/to/watchlist.json list
```

## Data file

Default: `skills/market-watchlist/data/watchlist.json`. Override via:

- `--config PATH` on every subcommand
- `MARKET_SKILLS_WATCHLIST_PATH` env var (absolute path)

## Schema

```json
{
  "baskets": {
    "crypto_majors": {
      "BTCUSD": {"tier": 2, "source": "kraken", "label": "BTC"},
      "ETHUSD": {"tier": 2, "source": "kraken", "label": "ETH"}
    },
    "crypto_alts": {
      "<TICKER1>USD": {"tier": 1, "source": "kraken", "label": "<label>"},
      "hl:<PERP>":  {"tier": 1, "source": "hyperliquid", "label": "<label>"}
    },
    "macro_refs": {
      "SPYUSD": {"source": "yfinance", "yfinance_ticker": "SPY", "tracking_only": true, "sector": "stocks"},
      "IWMUSD": {"source": "yfinance", "yfinance_ticker": "IWM", "tracking_only": true, "sector": "stocks",
                 "hl_proxy": "km:SMALL2000", "hl_proxy_weight": 1.0, "hl_proxy_note": "Russell 2000 perp"}
    }
  }
}
```

### Per-ticker metadata

| Field | Required | Notes |
|-------|----------|-------|
| `tier` | no | Priority hint (1=highest). Used by the agent brain for triage, not by analysis skills. |
| `source` | no | One of `kraken`, `yfinance`, `hyperliquid`, `ccxt`. Used by `provider_for()`. |
| `label` | no | Human-friendly name for display. |
| `yfinance_ticker` | no | Override the yfinance symbol when it differs from the watchlist key (e.g. `XLExUSD` → `XLE`). |
| `hl_coin` | no | Hyperliquid coin name when it differs from the watchlist key. |
| `hl_proxy` / `hl_proxy_weight` / `hl_proxy_note` | no | For commodities/ETFs: route sentiment through a related HL perp. |
| `tracking_only` | no | If true, skip strategy evaluation — only indicator reads. Used for macro/benchmark tickers. |
| `sector` | no | Free-form tag (`stocks`, `commodities`, etc.) for grouping in reports. |
| `asset_class` | no | L3 strategy maturity threshold class. Valid values: `perp_dex`, `low_float` (6× maturity floor), `ai_infra` (2×). See `strategy-trend-follow` Pattern S. |
| `comment` | no | Free-form note for the human maintainer. Not parsed by tools. |

### Provider resolution

`provider_for(ticker)` resolves the provider in this order:

1. Explicit `provider:` prefix on the ticker (e.g. `hl:LIT` → `hyperliquid`)
2. `source` field in metadata
3. `None` if ambiguous

The bare-alias resolver (`resolve("btc")`) strips common quote suffixes (`USD`, `xUSD`, `EUR`) so users can type `btc`, `eth`, `xle`, `lit`. Raises on ambiguous aliases.

## Library use (from other skills / scripts)

```python
from analysis.watchlist import all_tickers, by_category, basket, categories
from analysis.watchlist import metadata_for, provider_for, resolve, expand_tickers

# Flat list across all baskets
tickers = all_tickers()                                  # -> ["BTCUSD", "ETHUSD", "<TICKER1>USD", ...]

# One basket
majors = by_category("crypto_majors")                    # -> ["BTCUSD", "ETHUSD"]

# Alias resolution
ticker = resolve("btc")                                  # -> "BTCUSD"

# Accept user input with bare symbols + full tickers mixed
tickers = expand_tickers(["btc", "ETHUSD", "xle"])       # -> ["BTCUSD", "ETHUSD", "XLExUSD"]

# Provider routing
prov = provider_for("BTCUSD")                             # -> "kraken"
```

The skill's `lib.py` re-exports the same surface for compatibility with `analysis.skill_loader.load_skill("market-watchlist")`.

## Integration with other skills

| Skill | How it consumes the watchlist |
|-------|------------------------------|
| `run-watchlist` | Primary driver. Pass `--basket <name>` or `--tickers …` (accepts bare aliases). |
| `run-all-l2` / `run-all-l3` | Use `--tickers $(...)` to pipe a basket's tickers in. No automatic integration; opt-in. |

The watchdog (`position-watchdog`) reads its own per-position config (`skills/position-watchdog/data/watches.json`) — different semantics (entry_price/position_size, stop/TP ladders). Keep them separate. `position-watchdog --watchlist` does a soft cross-ref against this registry and warns on stale tickers.

## Cron integration

`scripts/run.sh` is a thin wrapper that activates uv and calls `scripts/run.py`. Cron jobs can reference it directly:

```bash
# Daily brief — run all L2+L3 on a basket, with notes auto-included
0 7 * * *  bash /path/to/market-skills/skills/run-watchlist/scripts/run.sh crypto_majors --json > /tmp/brief.json
```

Most edits to the watchlist itself are manual (add a new asset, change tier). Cron mostly consumes it via `run-watchlist`.

## Workflows

**Add a new asset to a basket:**
```bash
# Edit skills/market-watchlist/data/watchlist.json
#   "defi": {
#     ...,
#     "MORPHOUSD": {"tier": 3, "source": "kraken", "label": "Morpho"}
#   }
uv run skills/market-watchlist/scripts/run.py validate
```

**Bulk-scan a basket every morning:**
```bash
uv run skills/run-watchlist/scripts/run.py crypto_majors --json
```

**Triage by tier:**
```bash
# Custom one-liner: list all tier-1 tickers across baskets
uv run skills/market-watchlist/scripts/run.py --json tickers | \
  jq -R 'split("\n")[] | select(length > 0)' | ...
# (or use the JSON list output + a small Python filter)
```

## Validation

`validate` walks the file and reports schema errors. Use after manual edits:

```bash
uv run skills/market-watchlist/scripts/run.py validate
# OK — 8 basket(s), 41 ticker(s)
```

## Exit codes

- `0` — success
- `1` — fatal (bad `--config`, file I/O error, schema error)
- `2` — invalid usage (missing args, unknown subcommand)