---
name: position-watchdog
description: "Unified position monitor — entry/stop/TP ladders, multi-zone entry tracking, and market-skills L3 signal evaluation. Replaces the four legacy hermes watchdog scripts (hype/lit/zec/reversal)."
version: 0.1.0
metadata:
  hermes:
    tags: [watchdog, position, monitor, signals]
    category: monitoring
compatibility: "Requires Python 3.12+ and uv"
---

# position-watchdog

One skill, one config (`watches.json`), one cron — watches any number of assets against three logical rule types expressed in two config arrays:

- `levels` — price-driven rules covering **position monitoring** (stop, TP ladder, drop warnings, recovery) and **entry-zone tracking** (price-band zones, invalidation floor)
- `signals` — market-skills L3 strategy evaluation (trend-follow, mean-reversion, etc.) with conviction threshold and cooldown

Per-watch state is persisted across cron ticks. Alerts fire only on state changes (silent on normal ticks). Manual confirmation language preserved — script NEVER executes orders.

## Quick Start

```bash
# Run once with the default config (skills/position-watchdog/watches.json)
uv run skills/position-watchdog/scripts/run.py

# Custom config path
uv run skills/position-watchdog/scripts/run.py --config /path/to/watches.json

# Custom state directory (per-watch state files go here)
uv run skills/position-watchdog/scripts/run.py --state-dir /path/to/state

# Inspect without firing alerts (dry run, prints what would alert)
uv run skills/position-watchdog/scripts/run.py --dry-run
```

## Config schema

```json
{
  "watches": [
    {
      "name": "HYPE",
      "enabled": true,
      "provider": "kraken:HYPEEUR",
      "entry_price": 60.15,
      "position_size": 1.66,
      "levels": [
        {"type": "stop", "price": 49.71},
        {"type": "tp",   "price": 88.21,  "exit_pct": 33},
        {"type": "tp",   "price": 100.58, "exit_pct": 33},
        {"type": "tp",   "price": 119.14, "exit_pct": 34},
        {"type": "drop", "pct": -5},
        {"type": "drop", "pct": -10},
        {"type": "recovery"}
      ],
      "signals": [
        {"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}
      ]
    }
  ]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | Unique identifier; used in alert prefix and state filename |
| `enabled` | yes | When false, watch is skipped silently |
| `provider` | yes | `provider:ticker` notation — `kraken:HYPEEUR`, `hl:LIT`, `yf:AAPL` |
| `entry_price` | for `drop`/`recovery` | Reference price for percentage drops and recovery detection |
| `position_size` | for TP `exit_pct` math | Position size (in base asset) used to compute `size × exit_pct / 100` for TP alerts |
| `levels` | one of `levels` or `signals` required | Price-driven alert rules (see below) |
| `signals` | one of `levels` or `signals` required | L3 strategy evaluation rules (see below) |

### `levels` array — position monitor + entry zones

Each entry has a `type` discriminator. Drop percentages **must be negative** (the lib uses `pct_from_entry <= pct` to fire; a positive value would fire on upward moves, which is a bug — see `tests/test_position_watchdog.py::test_drop_positive_pct_does_not_fire_on_up_moves`).

| `type` | Required fields | Fires when | Alert format |
|--------|-----------------|------------|--------------|
| `stop` | `price` | price ≤ price | `🔴 STOP BREACHED at €X (stop €Y). Verify fill manually.` |
| `tp` | `price`, optional `exit_pct` | price ≥ price | `✅ TP hit (€Y). RECOMMEND: sell {qty} (~{pct}%). Manual confirm required.` |
| `drop` | `pct` (negative) | pct-from-entry ≤ pct | `🟡/🔶 {pct} from entry. Current €X, entry €Y.` (`🔶` when `pct ≤ −10`, else `🟡`) |
| `recovery` | (uses `entry_price`) | 2 consecutive ticks above entry after any `drop` has fired | `🟢 recovered above entry. Current €X.` |
| `zone` | `low`, `high`, `label`, optional `emoji` | price enters `[low, high]` band | `<emoji> {label} — {NAME} @ €X.` |
| `invalidation` | `below` | price < below (sticky — does not re-alert on recovery) | `🔴 INVALIDATION — Thesis dead. {NAME} @ €X. Stop loss triggered below €Y. Do not average down.` |

Full HYPE example:

```json
{
  "name": "HYPE",
  "enabled": true,
  "provider": "kraken:HYPEEUR",
  "entry_price": 60.15,
  "position_size": 1.66,
  "levels": [
    {"type": "stop", "price": 49.71},
    {"type": "tp",   "price": 88.21,  "exit_pct": 33},
    {"type": "tp",   "price": 100.58, "exit_pct": 33},
    {"type": "tp",   "price": 119.14, "exit_pct": 34},
    {"type": "drop", "pct": -5},
    {"type": "drop", "pct": -10},
    {"type": "recovery"}
  ]
}
```

Full ZEC example (zones + invalidation, no entry_price):

```json
{
  "name": "ZEC",
  "enabled": false,
  "provider": "kraken:ZECEUR",
  "levels": [
    {"type": "zone",         "low": 500, "high": 510,   "label": "T2 limit zone",   "emoji": "🟢"},
    {"type": "zone",         "low": 558, "high": 588,   "label": "T3 reclaim",      "emoji": "🟡"},
    {"type": "zone",         "low": 588, "high": 99999, "label": "T4 continuation", "emoji": "🟠"},
    {"type": "invalidation", "below": 486}
  ]
}
```

### `signals` array — L3 strategy evaluation

Each entry is a strategy block. The watchdog fetches 1d/1y candles for the watch's `provider:ticker` and runs the listed L3 strategies. Alerts fire when an idea meets `min_conviction` and the cooldown window for that strategy+direction has elapsed.

```json
{
  "name": "ZEC",
  "enabled": false,
  "provider": "kraken:ZECEUR",
  "signals": [
    {"strategies": ["mean-reversion", "breakout-confirm"], "min_conviction": 4, "cooldown_hours": 4}
  ]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `strategies` | yes | L3 strategy names: `trend-follow`, `mean-reversion`, `breakout-confirm`, `accumulation-swing`, `exhaustion-fade`, `liquidity-sweep` |
| `min_conviction` | no, default 3 | Minimum L3 conviction (1–5) to fire |
| `cooldown_hours` | no, default 0 | Same strategy + same direction will not re-alert within this window |
| `direction` | no | Restrict alerts to ideas matching this direction: `"long"` or `"short"`. Case-insensitive; mismatched ideas are silently dropped. Cooldown still keyed on actual idea direction. |

Alert format:
- `🎯 trend-follow LONG conv=4. Entry €X, stop €Y.`
- `🎯 mean-reversion SHORT conv=3. Entry €X, stop €Y.`

## State files

Per-watch JSON state lives in the state directory (default `skills/position-watchdog/data/`, override with `--state-dir`). Filenames are sanitized — `:` and `/` become `_` (e.g., `hl:LIT` → `hl_LIT_state.json`).

State fields per watch:
- `alerted_levels` — set of level IDs that have already fired (dedup; recovery and invalidation are sticky)
- `above_entry_streak` — consecutive ticks above `entry_price` (used by `recovery`)
- `prev_price` — last seen price (used by `zone` for transition detection)
- `last_signal_alert_at` — per `(strategy, direction)` last alert timestamp (cooldown)

Stale state (>24h old) is treated as fresh on the first tick — no alerts fire, state is rewritten.

## Workflows

**Add a new position:**
1. Open position on Kraken (manual, exchange UI)
2. Edit `watches.json`: copy the HYPE template, set `enabled: true`, fill `entry_price`/`position_size`/levels
3. Next `:08`/`:38` tick picks it up

**Close a position:**
1. Sell on Kraken
2. Edit `watches.json`: set `enabled: false`
3. Config preserved for future re-adds

**Re-enter a closed position:**
1. Buy on Kraken
2. Edit `watches.json`: flip `enabled: true`, update fills if needed
3. Done

## Cron integration

Run as a `no_agent=true` script-style cron job. Suggested schedule: `8,38 * * * *` (twice per hour, offset from `:00`/`:30` to avoid minute-boundary congestion with other crons).

The wrapper `scripts/run.sh` handles `cd market-skills && uv run python` invocation so the cron can reference it directly. Pass `--config` and `--state-dir` to decouple config and state from the market-skills checkout:

```bash
bash skills/position-watchdog/scripts/run.sh \
  --config /path/to/watches.json \
  --state-dir /path/to/state
```

Exit codes:
- `0` — normal tick (silent or alerts printed)
- `1` — fatal: bad config, schema error, or all-watches fetch failed
- `2` — partial: some watches had fetch failures but at least one succeeded
