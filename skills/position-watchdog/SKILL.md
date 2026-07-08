---
name: position-watchdog
description: "Unified position monitor — entry/stop/TP ladders, multi-zone entry tracking, and market-skills L3 signal evaluation. Single-currency library; renders alerts in the monitor provider's quote."
version: 0.4.0
metadata:
  hermes:
    tags: [watchdog, position, monitor, signals]
    category: monitoring
compatibility: "Requires Python 3.12+ and uv"
---

# position-watchdog

Two kinds of watchdogs: **position protection** (downside alerts for open positions) and **entry zone** (buy-zone monitoring). Both run as `no_agent` ticks — zero LLM tokens per evaluation, only print on state changes.

### Position protection watchdog

Monitors open positions with silent hourly checks, fires only on threshold crossings:

- Stop-loss breach → 🔴 CRITICAL
- % drop from avg cost → 🟡 WARNING (-5%), 🟠 MAJOR (-10%)
- Rapid hourly decline → ⚡ >3%
- Recovery → 🟢 resets alert state

### Entry zone watchdog

Notifies when a target asset enters a defined buy zone. Tracks previous state via `.json` state file so it fires only on state changes.

One skill, one config (`watches.json`), one scheduled evaluation — watches any number of assets against three logical rule types expressed in two config arrays:

> **LLM agent brain**: this skill is the hand-off target when an `execution-kraken-*` `FillConfirmation` returns `status="submitted"` (market accepted, no fill in `--wait-timeout`) or `status="open"` (limit on the book). Do not keep retrying the execution skill — the watchdog detects fills on its own tick. See [`LLM-ORCHESTRATION.md`](../../LLM-ORCHESTRATION.md) §3.

- `levels` — price-driven rules covering **position monitoring** (stop, TP ladder, drop warnings, recovery) and **entry-zone tracking** (price-band zones, invalidation floor)
- `signals` — market-skills L3 strategy evaluation (trend-follow, mean-reversion, etc.) with conviction threshold and cooldown

Per-watch state is persisted across evaluation ticks. Alerts fire only on state changes (silent on normal ticks). Manual confirmation language preserved — script NEVER executes orders.

### Cost-basis gate (MUST-PASS rule)

Every `tp` level **must** be strictly above `entry_price` for longs (below for shorts). A TP that fires underwater locks in a realized loss — the watchdog must never suggest that.

**Why:** the watchdog does not cross-check `tp` against `entry_price` at the schema level. All agents authoring watch configs must enforce this rule manually until the schema gate lands.

**Action checklist:**

1. Every `tp` MUST be strictly greater than `entry_price` for longs. Audit all enabled watches.
2. If a legacy TP is below cost, raise to the first profit level above cost or remove it entirely.
3. When asked "shall we trim X?", show cost basis + current bid + per-slice P&L first. If the math shows a realized loss, reconfigure instead.
4. Watchdog TP labels say "TP HIT" regardless of profitability — always verify before acting.

## Quick Start

```bash
# First time: copy the example and edit
cp skills/position-watchdog/examples/watches.example.json \
   skills/position-watchdog/data/watches.json

# Run once with the default config (skills/position-watchdog/data/watches.json)
uv run skills/position-watchdog/scripts/run.py

# Custom config path
uv run skills/position-watchdog/scripts/run.py --config /path/to/watches.json

# Custom state directory (per-watch state files go here)
uv run skills/position-watchdog/scripts/run.py --state-dir /path/to/state

# Or via env vars (CLI flags still win):
export MARKET_SKILLS_WATCHDOG_PATH=/path/to/watches.json
export MARKET_SKILLS_WATCHDOG_STATE_DIR=/path/to/state

# Inspect without firing alerts (dry run, prints what would alert)
uv run skills/position-watchdog/scripts/run.py --dry-run
```

## Single-currency alert rendering (library default)

This library renders alerts in a single currency — the monitor provider's
quote. All level prices (`stop`, `tp`, `entry_price`, `invalidation.below`,
`zone.low/high`) are in the **monitor's** quote. If you set
`monitor_provider: "kraken:HYPEUSD"`, write your stop in USD.

The library uses only `monitor_provider`. The historical `execution_provider`
field is rejected at schema-validation time — a clean break to keep the
library minimal. If you want a separate view on a different pair, configure
a second watch with its own `monitor_provider`.

## Alert format styles

`run.py` accepts three rendering styles via the `format_style` watch field
or the `--formatter` CLI flag. The CLI flag sets the default for any
watch that doesn't pin its own `format_style`.

| Style | Shape | Use case |
|-------|-------|----------|
| `compact` | One-liner, legacy output | Existing pipelines / minimal log noise |
| `default` | Richer multi-line. Signal events show R-multiples, R:R, entry type + risk% | Open-positions, human-readable alerts |
| `verbose` | `default` + reasoning + source_skills lines for signal events | Audit / debug / on-call handoff |

Defaults are filename-driven: `open-positions.json` → `default`, every
other config → `compact`. To force a watch onto a style, set
`"format_style": "compact"` (or `default` / `verbose`) on the watch.

The data flow is `lib.evaluate_*` (pure, returns structured event dicts)
→ `formatter.format_alerts(events, ctx)` (pure, returns strings). The
event dict shapes are stable and can be inspected in tests via the
`lib` import.

```bash
# Override the default style across the whole run
uv run skills/position-watchdog/scripts/run.py --formatter verbose

# Pin a specific watch to a non-default style
# { ..., "format_style": "verbose", ... }
```

## Config schema

```json
{
  "watches": [
    {
      "name": "HYPE",
      "enabled": true,
      "monitor_provider": "kraken:HYPEUSD",
      "interval": "4h",
      "period": "6mo",
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
| `monitor_provider` | yes | `provider:ticker` notation — `kraken:HYPEUSD`, `hl:LIT`, `yf:AAPL`. Drives the live tick, candles, L3 evaluation, and the alert prices. All level prices in this watch are assumed to be in this provider's quote. |
| `execution_provider` | removed | Schema-rejected in this release. Use a second watch if you want a different pair's view. |
| `interval` | optional, default `"4h"` | Candle interval for both live-price tick and L3 strategy evaluation. Validated against `analysis/intervals.py`. Common values: `15m`, `1h`, `4h`, `1d`. |
| `period` | optional, default `"6mo"` | Candle lookback for both jobs. Validated against `analysis/intervals.py`. Common values: `1mo`, `3mo`, `6mo`, `1y`. |
| `format_style` | optional, default `"compact"` (watchlist) or `"default"` (open positions) | Alert rendering style. One of `"compact"` (one-liner), `"default"` (richer multi-line), or `"verbose"` (default + reasoning/sources on signal events). Overrides the filename default and the `--formatter` CLI flag. |
| `entry_price` | for `drop`/`recovery` | Reference price (in the monitor's quote) for percentage drops and recovery detection |
| `position_size` | for TP `exit_pct` math | Position size (in base asset) used to compute `size × exit_pct / 100` for TP alerts |
| `levels` | one of `levels` or `signals` required | Price-driven alert rules (see below) |
| `signals` | one of `levels` or `signals` required | L3 strategy evaluation rules (see below) |

## Timeframes

Default `4h` / `6mo`. To watch on a different timeframe, set `interval` and `period` per watch. Validation against `analysis/intervals.py`. The same interval governs both the live-price tick and L3 strategy evaluation — no split. Trade-off: alerts may lag by up to one full candle.

### `levels` array — position monitor + entry zones

Each entry has a `type` discriminator. Drop percentages **must be negative** (the lib uses `pct_from_entry <= pct` to fire; a positive value would fire on upward moves, which is a bug — see `tests/test_position_watchdog.py::test_drop_positive_pct_does_not_fire_on_up_moves`).

All level prices are in the watch's **monitor quote** (e.g. USD if `monitor_provider: "kraken:HYPEUSD"`). Library renders a single currency in alerts.

| `type` | Required fields | Fires when | Alert format (compact · default) |
|--------|-----------------|------------|--------------|
| `stop` | `price` | price ≤ price | compact: `🔴 STOP BREACHED at $X (stop $Z). Verify fill manually.` · default: `🔴 STOP BREACHED — {NAME}. Now $X. Stop at $Z.` |
| `tp` | `price`, optional `exit_pct` | price ≥ price | compact: `✅ TP hit ($Y). RECOMMEND: sell {qty} (~{pct}%). Manual confirm required.` · default: `✅ TP HIT — {NAME}. Now $X. TP at $Y. Exit {pct}% ({qty}).` |
| `drop` | `pct` (negative) | pct-from-entry ≤ pct | compact: `🟡/🔶 {pct} from entry. Current $X, entry $Z.` (`🔶` when `pct ≤ −10`, else `🟡`) · default: `🟡 DROP WARNING — {NAME}. Now $X (−{pct} from entry $Z).` or `🔶 DEEP DROP — {NAME}. …` |
| `recovery` | (uses `entry_price`) | 2 consecutive ticks above entry after any `drop` has fired | compact: `🟢 recovered above entry. Current $X.` · default: `🟢 RECOVERED — {NAME}. Now $X. Back above entry $Z.` |
| `zone` | `low`, `high`, `label`, optional `emoji` | price enters `[low, high]` band | compact: `<emoji> {label} — {NAME} @ $X.` · default: `<emoji> ZONE ENTRY — {label}. {NAME} now $X.` |
| `invalidation` | `below` | price < below (sticky — does not re-alert on recovery) | compact: `🔴 INVALIDATION — Thesis dead. {NAME} @ $X. Stop loss triggered below $Z. Do not average down.` · default: `🔴 INVALIDATED — {NAME}. Now $X. Below invalidation $Z. Thesis dead.` |

The live `$X` in the "Now" / "Current" field is the monitor's last close.
Static levels (`$Z`) render in the monitor's quote only — the skill never
synthesizes a converted price from a live ratio.

Full HYPE example (USD-monitored):

```json
{
  "name": "HYPE",
  "enabled": true,
  "monitor_provider": "kraken:HYPEUSD",
  "interval": "4h",
  "period": "6mo",
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

Full ZEC example (zones + invalidation, no entry_price, monitor-only):

```json
{
  "name": "ZEC",
  "enabled": false,
  "monitor_provider": "kraken:ZECUSD",
  "levels": [
    {"type": "zone",         "low": 500, "high": 510,   "label": "T2 limit zone",   "emoji": "🟢"},
    {"type": "zone",         "low": 558, "high": 588,   "label": "T3 reclaim",      "emoji": "🟡"},
    {"type": "zone",         "low": 588, "high": 99999, "label": "T4 continuation", "emoji": "🟠"},
    {"type": "invalidation", "below": 486}
  ]
}
```

### `signals` array — L3 strategy evaluation

Each entry is a strategy block. The watchdog fetches candles for the watch's `monitor_provider` on the watch's configured `interval` / `period` (defaults `4h` / `6mo` — see [Timeframes](#timeframes)) and runs the listed L3 strategies. Alerts fire when an idea meets `min_conviction` and the cooldown window for that strategy+direction has elapsed.

The same interval/period governs both the live-price tick and L3 strategy evaluation — there is no longer a split between the two. Use a higher-frequency interval (e.g. `15m`, `1h`) for tighter alerts at the cost of more candle data; use a lower-frequency interval (e.g. `1d`) for swing-style positions. The analysis-skill `--interval`/`--period` flags don't apply here.

```json
{
  "name": "ZEC",
  "enabled": false,
  "monitor_provider": "kraken:ZECUSD",
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
- compact: `🎯 trend-follow LONG conv=4. Entry $X, stop $Y.`
- default (multi-line, R-multiples + R:R + risk%):
  ```
  🎯 trend-follow LONG conv=4.
    Entry $61.19 (limit, current). Stop $57.12 (-6.7%).
    TP $67.50 (1.7R) · $72.50 (2.4R) · $80.00 (3.3R).
    R:R 2.50:1 mid.
  ```
- verbose: default + `Why: …` and `Sources: …` lines pulled from the idea's `reasoning` and `source_skills`.

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
1. Open position on the exchange (manual, exchange UI)
2. Edit `watches.json`: copy the HYPE template, set `enabled: true`, fill `monitor_provider` / `entry_price` / `position_size` / levels
3. Next evaluation tick picks it up

**Close a position:**
1. Sell on the exchange
2. Edit `watches.json`: set `enabled: false`
3. Config preserved for future re-adds

**Re-enter a closed position:**
1. Buy on the exchange
2. Edit `watches.json`: flip `enabled: true`, update fills if needed
3. Done

## Scheduled integration

The skill is designed to be invoked on a recurring schedule (e.g. twice per hour via the host's task scheduler) — `scripts/run.sh` handles the `cd market-skills && uv run python` invocation so any scheduler can reference it directly. Pass `--config` and `--state-dir` to decouple config and state from the market-skills checkout:

```bash
bash skills/position-watchdog/scripts/run.sh \
  --config /path/to/watches.json \
  --state-dir /path/to/state
```

## `--status` mode (read-only current-state snapshot)

Prints one line per enabled watch with current price, zone attribution,
next-zone hint, invalidation floor, most-recent fired drop thresholds,
% from entry, and any above-entry streak. Read-only — does not advance
state, fire alerts, or write the fetch-failures window. Useful when you
got one transition alert earlier and now want to see "where am I right
now?" for every position at a glance.

```bash
uv run skills/position-watchdog/scripts/run.py \
  --config /path/to/watches.json \
  --status
```

Output example:

```
[VVV] @ $10.39 | 🟡 T2 wait zone (no add) — above T1 add zone ($7.50–$9.00); invalid <$8.00; drop −20.0%, −10.0% fired | −33.9% from entry $15.73; above entry streak=3
[ETH] @ <fetch failed> | no active zone; invalid <$1500.00 | (no live price; using last known $1538.42)
[HYPE] @ $68.35 | no active zone | +13.6% from entry $60.15
```

Notes:
- `--watch` is ignored when `--status` is set; status mode always renders every enabled watch (pipe to `grep VVV` to filter).
- Per-watch fetch failure renders as `<fetch failed>` and falls back to the last `prev_price` from state for the `% from entry` clause.
- Stale state (>24h old) is treated as empty so streaks and `alerted_levels` reflect only the current tick + config.
- Exit codes: `0` clean (all live prices returned), `2` partial (one or more fetches failed but lines still print).
- No new state fields, no new thresholds, no behavioral change to the existing tick path.

## Cross-reference with market-watchlist

If you maintain a [`market-watchlist`](../market-watchlist/) registry, pass `--watchlist` to cross-check every watch's `monitor_provider` bare ticker against it. Any watch using a monitor ticker that isn't registered in any basket gets a stderr warning — useful for catching stale `watches.json` entries when you rebalance the watchlist.

```bash
bash skills/position-watchdog/scripts/run.sh \
  --config /path/to/watches.json \
  --watchlist /path/to/watchlist.json
```

Exit codes:
- `0` — normal tick (silent or alerts printed); also when every enabled watch had a single-tick fetch blip (all-fetches-failed but the rolling 5-tick window shows <3 failures per watch) — logged as `[WARN]`, no FATAL
- `1` — fatal: bad config, schema error, or sustained all-watches fetch failure (≥3 of last 5 ticks failing per watch)
- `2` — partial: some watches had fetch failures but at least one succeeded

## Migration from pre-0.3.0 configs

> **Breaking changes in 0.3.0.** Update your `watches.json` before deploying.

The schema was split in 0.2.0 and the legacy `provider` field was kept as a back-compat alias through 0.2.x. The 0.3.0 release removes the alias — `provider` is no longer recognized. If you still have the old single-field config, do a one-time edit:

Before (0.1.x):
```json
{
  "name": "HYPE",
  "provider": "kraken:HYPEEUR",
  "display_currency": "usd"
}
```

After (0.3.0+):
```json
{
  "name": "HYPE",
  "monitor_provider": "kraken:HYPEUSD"
}
```

What changed:
- **`provider` → `monitor_provider`** (rename; the `provider` field is now a hard schema error).
- **`display_currency` removed** — the skill no longer derives a secondary currency from the primary. Set `monitor_provider` to the quote you want alerts in.
- **Level prices are in the monitor's quote** — if you switched from `provider: "kraken:HYPEEUR"` to `monitor_provider: "kraken:HYPEUSD"`, divide your EUR prices by the EUR/USD rate (~1.08 as of writing) to get USD equivalents.
- **`execution_provider` removed** — the library is single-currency now. If you want to watch a different pair, configure a second watch with its own `monitor_provider`.

Quick sed one-liner for the rename (review before running):
```bash
sed -i 's/"provider":/"monitor_provider":/g' watches.json
```
You'll still need to manually: (1) set the new `monitor_provider` to the pair you want alerts in, (2) convert level prices to that quote, and (3) remove any `display_currency` field.
