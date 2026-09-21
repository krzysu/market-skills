---
name: portfolio-mgmt
description: "Portfolio management — track holdings, cost basis, and P&L across multiple portfolios. SQLite-backed, FIFO cost basis."
version: 0.1.0
metadata:
  hermes:
    tags: [portfolio, tracking, pnl, cost-basis]
    category: portfolio
compatibility: "Requires Python 3.12+ and uv"
---

# portfolio-mgmt

Track trades, holdings, and P&L across any number of user-defined portfolios. SQLite-backed with FIFO cost basis.

> **LLM agent brain**: for the partial-fill recording workflow (when an `execution-kraken-*` `FillConfirmation` returns `status="partial"` and you need to write a row manually because auto-wiring was skipped), see [`LLM-ORCHESTRATION.md`](../../LLM-ORCHESTRATION.md) §3.

## When NOT to use

- For analysis or trade signals — this is a record-keeping system (cost basis, P&L, positions), not an indicator. Use market-* / strategy-* skills for the read.
- For live execution — placing orders is `execution-kraken-spot` / `execution-kraken-perps`; this skill only logs fills after the fact.
- To skip the AUTO-LOG: every confirmed trade MUST be written here via `add` so FIFO cost basis stays correct; do not treat it as optional bookkeeping.

## Quick Start

```bash
uv run skills/portfolio-mgmt/scripts/run.py init
uv run skills/portfolio-mgmt/scripts/run.py portfolio create --name spot
uv run skills/portfolio-mgmt/scripts/run.py add --portfolio spot --asset=kraken:BTCUSD --side buy --qty 0.01 --price 45000
uv run skills/portfolio-mgmt/scripts/run.py add --portfolio spot --asset=kraken:HYPEEUR --side buy --qty 10 --price 33
uv run skills/portfolio-mgmt/scripts/run.py prices refresh    # Fetch live prices for held assets
uv run skills/portfolio-mgmt/scripts/run.py positions
```

## AUTO-LOG directive

**Every deliberate trade — manual or scripted — MUST be logged to portfolio-mgmt.** This is the system of record; on-chain CEX history matters more than mark-to-market.

### Scope: book INVESTMENTS, never mirror a venue balance

This ledger records **decisions**, not the venue's balance sheet. Logging is triggered by *a trade you chose to make* — never by *something appearing in a `kraken balance` output*.

**Never do this:**

- Run a balance→ledger "sync" that adds a row for every non-zero key. No such job exists and none should — drift is diagnosed with `reconcile`, never repaired by bulk-inserting rows.
- Book **dust and residue** (leftover fractions from a partial sell, sub-cent remainders). These are artifacts, not positions.
- Book a balance **that arrived unbidden** — staking or reward drips that simply showed up. A position with no cost basis is not an investment.
- Mirror venue **cash** (`ZEUR`/`ZUSD`) into the ledger. Sell proceeds are not a new position; the cash side of a trade is intentionally absent. The **single** sanctioned exception is the fixed working-capital bucket below — everything else cash-shaped stays out.

**The one sanctioned exception — a deliberate, fixed working-capital bucket.** Book it **once** as a stated allocation and leave it alone. Live example: `BUY kraken:EUR 1000 @ 1.0` (transactions.id=78) is a **notional** EUR 1,000 working-capital bucket, **not a venue mirror** — Kraken's actual cash at the time was EUR 1,167.62, and that difference is expected; the ledger records **decisions** while the venue records **reality**, and the two must **never** be reconciled to each other. Never tune the bucket to match `kraken balance`, never top it up, and never insert a cash row because a key appeared in a balance output.

**The asset key must be `kraken:EUR`, not `ZEUR`.** `risk-engine` populates `cash_available` by comparing `_strip_prefix(asset).upper()` against the portfolio's `base_ccy`, so only the prefixed form matches; a bare `ZEUR` key leaves `cash_available` at 0 and the bucket is silently ignored.

Why the bucket exists at all: without a cash key the ledger has no liquidity figure, and any `insufficient_funds` check has nothing to read. With `kraken:EUR` present the policy is live — verified: a EUR 399.82 intent resolves `APPROVED`, while a EUR 2,023.20 intent resolves `REJECT — insufficient cash: need 2023.20 EUR, have 1000.00`.

> **Caveat — market orders are costed, not skipped.** `analysis/risk/spot.py::insufficient_funds_policy` no longer requires `limit_price`: a market buy is costed from a resolved reference price (the intent's `extras` hints, the held position's `current_price`, or the price risk-engine injects into `ctx.reference_prices` from `--reference-price`, the price cache, or a live spot fetch), and when no price resolves the price-dependent policies emit a CONCERN naming the missing price rather than silently approving. An `APPROVED` on a market order means a reference price resolved and the funds check actually ran.

**Test before adding a row:** did the user decide to acquire this asset at a known price? If no — it arrived unbidden, the price is unknown, or it is too small to trade — it does not belong here. Leave it out rather than inserting a row priced at `0`.

A row priced at `0` is a red flag, not a neutral placeholder: it claims the asset was free and inflates unrealized P&L by its full market value. Omit it instead.

> **Superseded — staking-reward `@ 0` convention retired (2026-09-18).** The old rule ("log `BUY @ price 0` with `notes.reason='staking_reward'` so an over-sell balances under FIFO", precedent PENDLE 2026-07-21, VVV 2026-09-09) is **superseded**. It only existed to let an over-sell balance — and over-sells no longer occur because we **sell exactly the amounts we bought**, not what the venue shows. Micro staking rewards and residue are therefore simply **ignored** — never logged, never given a zero-price row.

The rule now: **sell the amounts we bought, not what the venue shows.** There is no FIFO-balancing exception left, and no "legitimate exception" for rewards accruing on a position the user already owns — the old convention is retired, not endorsed.

When in doubt, ask — do not insert rows the user did not ask for.

After any order confirms (returns fill confirmation), run `add` before confirming completion. Two failure cases if you skip the log: (1) the trade shows up in balance but not in positions, breaking FIFO cost-basis from that point on; (2) the FIFO chain silently corrupts downstream — every later position that consumes the missing lot produces wrong realized P&L.

```bash
add --portfolio <NAME> \
    --asset=<provider>:<PAIR> \
    --side <buy|sell> \
    --qty <X> \
    --price <total_incl_fees / qty> \
    --notes '{"order_id":"…","stop_loss":…,"entry":…,"stop":…,"tp1":…,"tp2":…,"tp3":…,"thesis":"…"}'
```

The `--notes` JSON should at minimum include `order_id` plus the entry/stop/TP ladder for every trade with a level plan — this makes `pnl` queries answer "did this TP fire?" in one read.

**`--portfolio` accepts name OR integer ID on every subcommand.** `add`, `positions`, `pnl`, `view`, `lots`, `allocation`, `performance`, `replay`, `reconcile`, `export`, and `list` all resolve the argument via `portfolio get_portfolio(id_or_name)` — numeric IDs first, then by name. Use whichever is handier:

```bash
add --portfolio defi --asset=hl:LIT --side buy --qty 100 --price 0.085      # by name
positions --portfolio 2                                                    # by integer id (same effect)
```

If neither matches, the command exits non-zero with a friendly `No portfolio matching '<arg>'` message instead of crashing on the previous `invalid int value` shape.

## CLI

```
init                                          # Create database ($MARKET_SKILLS_PORTFOLIO_DB)
portfolio create --name X [--base-ccy EUR]    # Create a portfolio
portfolio list                                # List all portfolios
portfolio show <id|name>                      # Show one portfolio
portfolio rename <id> <new-name>              # Rename
portfolio delete <id> [--yes]                   # Permanently delete (prompts unless --yes)

add --portfolio X --asset=kraken:BTCUSD --side buy --qty 0.01 --price 45000
add --notes '{"fng": 12, "rsi": 38}'           # Free text or JSON, @file for reading from path

list [--portfolio X] [--asset Y] [--side buy] [--since=DATE] [--limit N]
view [--portfolio X] [--price-override ASSET=PRICE ...]
positions [--portfolio X] [--asset Y] [--price-override ASSET=PRICE ...]
pnl [--portfolio X] [--asset Y] [--price-override ASSET=PRICE ...]
lots [--portfolio X] [--asset Y]

edit <id> --field notes --value "updated"     # Only notes and ref are editable
remove <id> [--yes]                           # Delete a transaction

prices refresh                                # Fetch live prices via analysis/data.py for held assets
allocation [--portfolio X] [--no-refresh]       # % breakdown by asset
performance [--portfolio X] [--no-refresh]      # Realized PnL, profit factor
replay [--portfolio X] [--json]                # FIFO audit trail — per-lot creation & consumption
reconcile --portfolio X --balance-file FILE     # Compare DB positions vs external JSON snapshot
export [--portfolio X] [--format csv|json] [--output FILE]
sync-open-positions [--config PATH] [--portfolio X] [--dry-run]   # Derive the open-positions held file from the ledger
                   [--price-override ASSET=PRICE ...]
```

All commands accept `--json` for machine output and `--db PATH` to override the default database location.
`init` creates the parent directory if it doesn't exist. The default DB path comes from `$MARKET_SKILLS_PORTFOLIO_DB`; the CLI raises if it is unset (no host-specific fallback).
`view`, `positions`, `pnl`, `allocation`, `performance` automatically use cached prices from `prices refresh`. Override with `--price-override`.

**Price freshness on `prices refresh`:** when a held asset doesn't expose a live spot endpoint, the cache falls back to the most recent daily candle close and the asset is flagged `ohlc:close` in the `price_cache.source` column. Stale-fallback assets are listed to stderr so operators can see when unrealized P&L is mark-to-stale rather than mark-to-market. All four providers (`kraken`, `hl`, `yf`, `ccxt:*`) now expose `fetch_spot_price`, so the fallback path is rare in practice.

## Asset format

Pass via `--asset`. All transactions in a portfolio must use the same base currency.

| `--asset` | Provider | Base | Use for |
|---|---|---|---|
| `kraken:BTCUSD` | Kraken spot | USD | Crypto on Kraken |
| `kraken:HYPEEUR` | Kraken spot | EUR | Crypto on Kraken |
| `kraken:XLExUSD` | Kraken xStock | USD | Tokenized stocks/ETFs |
| `yf:AAPL` | YFinance | USD | Any YFinance ticker |
| `yf:IBCJ.DE` | YFinance | EUR | XETRA ETFs (Sparplan) |
| `hl:LIT` | Hyperliquid | USDC | HL spot tokens |
| `BTC` | none | — | Watch-only (manual price) |

The `provider:` prefix routes through `analysis/data.py` for `prices refresh`. Assets without a `:` prefix are skipped (manual price only).

## Currency

Each portfolio has its own `base_ccy` (EUR, USD, USDC, etc.). All numbers are rendered in that portfolio's native currency. No cross-currency aggregation — each `by_portfolio` entry is self-contained.

**All transactions in a given portfolio must use the same base currency.** If you trade in both EUR and USD, create two portfolios (e.g., `spot-eur` and `spot-usd`).

### Reporting rule — never total across portfolios

The no-aggregation rule above governs the **tool**; this governs the **agent narrating to the user**. Violating it produces a real and confusing error: a single "book total" is reported by summing a EUR-based portfolio with a USDC-based one, which requires a silent currency conversion. The user then reasonably concludes a position is held in the wrong portfolio and asks for it to be deleted — chasing a problem the reporting invented.

- **Report per portfolio, in that portfolio's base currency.** Two books are two numbers, never one.
- **A cross-portfolio total is never valid**, even when the currencies happen to match — they are separate books with separate theses and separate risk budgets.
- **If you convert between base currencies at all, label the conversion explicitly** (`<X> USDC ≈ <Y> EUR at <rate>`) instead of folding it into a total.
- **Never derive positions from the position-watchdog held file.** `open-positions.json` is a **cross-portfolio monitoring list**, not a book: it has no portfolio field and unions every venue. Reading it as a portfolio is the exact mechanism that produces the error above. Positions come from `positions --portfolio <name>` (the ledger), always.

| Wrong | Right |
|---|---|
| `Book: EUR <sum> — <ASSET_A> <n>, <ASSET_B> <n>, <ASSET_C> <n>` | `<portfolio-a> (<ccy>): <total>` and `<portfolio-b> (<ccy>): <total>` as two separate lines |

`view`, `positions`, `pnl`, `allocation`, `performance` auto-refresh prices from `analysis/data.py` on every call. Add `--no-refresh` to skip network calls and use stale cache. `--price-override` always takes precedence.

## Price overrides

Override auto-fetched prices per-asset:

```bash
positions --price-override kraken:BTCUSD=45000 --price-override kraken:HYPEEUR=33
```

## Notes (`--notes`)

Free text or JSON stored with a transaction. Use `@path` to read from a file:

```bash
add --notes '{"fng": 12, "rsi": 38, "thesis": "DCA entry"}'
add --notes "bought the dip"
add --notes @path/to/signal.json
```

Shows in `list` output and `export`.

### `decision_context` — structured decision trace

The **decision_context** sub-object captures the *state of the world at the moment you decided to take the trade* — what signals fired, what the risk verdict said, whether you overrode anything. It's the difference between "I bought <TICKER> at $60.15" (current data, already captured) and "I bought <TICKER> at $60.15 because L3 trend-follow LONG conv 4 fired under fear-recovery regime, macro regime was supportive, and I overrode the L3 stop from $49.71 to $50.50 because ZEC was already in the same direction" (decision trace, queryable later).

**System of record:** the `decisions` table in the portfolio SQLite DB (see `portfolio.db.schema`). Each row is keyed by `intent_id` (unique). For backward compat with tools that read the `transactions.notes` JSON, a copy is also embedded in `notes.decision_context` — but the `decisions` table is the authoritative source.

The canonical schema is the `DecisionContext` TypedDict in `analysis/decision.py` — that module is the single source of truth for field types and validation. The example below is illustrative; always refer to the TypedDict for the exact shape.

**Example** (illustrative — see `analysis/decision.py::DecisionContext` for the canonical schema):

```json
{
  "decision_context": {
    "intent_id": "trend-follow-<TICKER>USD-2026-06-22-001",
    "source_skill": "strategy-trend-follow",
    "l3_idea": {
      "direction": "long",
      "conviction": 4,
      "summary": "EMA21 reclaim + 4h thrust; bullish retest at ascending trendline",
      "entry_price": 60.15,
      "stop": 49.71,
      "tp1": 88.21, "tp2": 100.58, "tp3": 119.14,
      "rr_to_tp2": 3.3
    },
    "regime": {
      "label": "fear_recovery",
      "fng": 22,
      "btc_dominance": null,
      "divergence": "accumulation"
    },
    "macro_signals": ["fng_extreme_fear", "btc_fair_value_narrowing"],
    "risk_verdict": {
      "status": "APPROVED",
      "position_size_pct": 12.4,
      "concerns": []
    },
    "override": {
      "from_suggestion": false,
      "field": null,
      "reason": null
    },
    "captured_at": "2026-06-22T15:00:00Z"
  }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `intent_id` | string | recommended | Idempotency key from the execution Intent; matches `notes.intent_id` / `notes.ref` |
| `source_skill` | string | recommended | L3 strategy that produced the idea (`strategy-trend-follow`, `strategy-breakout-confirm`, etc.); or `"manual"` if hand-built |
| `l3_idea` | object | recommended | Compact idea summary: `direction`, `conviction` (1–5), `summary` (1-line), entry/stop/TP ladder, `rr_to_tp2` |
| `regime` | object | optional | Macro regime label + key metrics. Use `market-macro` skill labels when available: `fear_recovery`, `risk_on`, `risk_off`, `neutral`, etc. |
| `macro_signals` | list[string] | optional | Free-form list of macro/fundamental signals that supported the trade (e.g. `fng_extreme_fear`, `divergence_accumulation`, `btc_weekly_above_ema21`) |
| `risk_verdict` | object | recommended | `status` (APPROVED/CONCERN/SCALE/REJECT), `position_size_pct` of portfolio, `concerns` (list of policy fragment reasons) |
| `override` | object | recommended | `from_suggestion` (bool), `field` (what was overridden: stop/tp/volume/conviction), `reason` (1-line). Default `false/null/null` for clean system-driven trades |
| `captured_at` | ISO timestamp | recommended | When the decision was made (NOT the fill time) |

**When to populate:**
- **Auto:** Every Kraken trade via `execution-kraken-spot` / `execution-kraken-perps`. The LLM supplies the regime snapshot, risk verdict, and override flag via `Intent.decision_decoration` (or the matching CLI flag `--decision-decoration` on those skills); the lib merges those into the auto-built `DecisionContext` and writes to both the `decisions` table and `notes.decision_context`. Idempotent on `intent_id` — a retried submit with the same id is a no-op, preserving the original trace.
- **Manual inline:** `add --notes @/tmp/decision_ctx.json` for direct kraken trades or on-chain DEX swaps.
- **Backfill:** Use the spec at `references/decision-context-backfill.md` for older trades missing the field.

**Why this exists:** per the Foundation Capital "context graph" thesis (2026-07), the value in a decision-support system lives in *why a decision was made* (the trace), not just *what* (the data). This field makes every trade queryable by reasoning — "show me trades where I overrode the L3 stop under fear-recovery regime and what their R:R turned out to be."

## Editing transactions

Only `notes` and `ref` fields are editable. To correct a trade (qty, price, side, asset, timestamp), **remove and re-add it**. This avoids silently corrupting downstream FIFO chains.

## Moving assets between wallets

Use independent portfolios. When moving assets from portfolio A to portfolio B, record a BUY in B at the original cost basis:

```bash
# Buy 1 BTC on Kraken at 45k
add --portfolio kraken --asset=kraken:BTCUSD --side buy --qty 1 --price 45000

# Move 0.5 BTC to DeFi wallet: remove from A at cost basis (zero realized P&L)
add --portfolio kraken --asset=kraken:BTCUSD --side sell --qty 0.5 --price 45000

# Record in B at the same cost basis
add --portfolio defi --asset=kraken:BTCUSD --side buy --qty 0.5 --price 45000
```

Selling at the exact buy price produces zero realized P&L — the cost basis transfers cleanly. Do not sell at current market price for an internal transfer.

## Replay — FIFO audit trail

`replay` walks every transaction chronologically, showing when each BUY lot is created and how SELLs consume them. Every sell lists which lots were matched, the cost basis consumed, and per-lot P&L.

```bash
replay --portfolio 1
# #1  2024-01-15  BUY   1.0    kraken:BTCUSD  @ 45000  -> 1.0 remaining
# #3  2024-02-10  SELL  0.3    kraken:BTCUSD  @ 46000
#       <- lot #1: consumed 0.3  cost_basis 13500  P&L +300.00
#       total realized: +300.00
```

Pass `--json` for machine-readable replay events.

## On-chain DEX swap logging (defi portfolio)

When the user shares a Base / Arbitrum / StarkNet / Ethereum tx link for a DEX swap that lands in the defi portfolio, the auto-log shape is the same as Kraken:

1. **Capture tx data** — try `web_extract` first. If the page is JS-rendered (empty placeholders), ask the user to paste the Transaction Action line + token-transfer amounts.
2. **Pick asset key by data feed** — a Base-chain `<AI_TOKEN>` buy is logged as `hl:<AI_TOKEN>` (HL is the price feed). Chain info goes in notes only.
3. **Cost basis** = total input ÷ qty received. Include gas if material.
4. **Build notes JSON** with chain-specific fields:
   ```json
   {
     "tx_hash": "0x...",
     "chain": "base",
     "chain_id": 8453,
     "exchange": "KyberSwap",
     "block": 47667147,
     "filled_at": "2026-06-22T12:06:00Z",
     "input_token": "USDC",
     "input_amount": 5000,
     "output_token": "<AI_TOKEN>",
     "output_qty": 317.86,
     "route_summary": "USDC->WETH->DIEM-><AI_TOKEN> multi-hop",
     "implied_price_per_unit_usd": 15.73,
     "thesis": "Multi-TF spring setup",
     "asset_class": "alt-l3",
     "position_role": "starter",
     "logged_by": "operator"
   }
   ```
5. Use `--date ISO8601` (not `--ts`).
6. Verify after add with `positions --portfolio 2 --no-refresh`.

**No auto-log for on-chain trades yet.** Unlike Kraken, the user has not directed auto-log for on-chain swaps. Each log is an explicit instruction.

**Pitfall — note-key auto-attach requires asset-key match.** Logging `hl:<AI_TOKEN>` when existing notes use `hl:<AI_TOKEN>` means the note auto-attaches. Logging `base:<AI_TOKEN>` orphans the note.

## Reconcile — external balance check

`reconcile` compares your computed positions against an external wallet snapshot (JSON file). Shows deltas between your manual tracking and actual holdings.

Snapshot format: `{"provider:ticker": qty, ...}` — only asset quantities, no cash balances.

```bash
# snapshot.json: {"kraken:BTCUSD": 0.15, "kraken:<TICKER>EUR": 10}
reconcile --portfolio 1 --balance-file snapshot.json
#   =   kraken:BTCUSD    0.15000000    0.15000000  +0.0       match
#   !=  kraken:<TICKER>EUR   10.00000000   9.50000000   +0.5       diff
#   -   hl:<PERP>            5.00000000   0.00000000   +5.0       missing_external
```

Status legend: `=` exact match, `!=` quantity mismatch, `+` asset in snapshot but not in DB, `-` asset in DB but not in snapshot.

## Ledger -> open-positions sync

The `position-watchdog` held file (its `open-positions.json` config) is derived from this
ledger. **The source of truth is the LEDGER, never the venue** — the ledger records decisions,
the venue records reality, and they are expected to diverge. **No venue API call happens
anywhere in this path**: reference prices come from `--price-override` or the ledger-local
`price_cache` table only.

```bash
uv run skills/portfolio-mgmt/scripts/run.py sync-open-positions \
    --config /path/to/open-positions.json \
    [--portfolio X] [--dry-run] [--json] [--price-override ASSET=PRICE ...]
```

`--config` defaults to `$MARKET_SKILLS_OPEN_POSITIONS_PATH`; when the env var is unset and no
`--config` is passed, the CLI exits 2 naming the env var. `--dry-run` prints the diff and
writes nothing. `--json` emits the AXI envelope (`data` = the sync report, `count` = number of
watches in the config). Exit status is 0 even when drift is found; 2 only on a config/ledger/
write error.

Derivation rules — the sync derives **only membership, `position_size` and a missing
`entry_price`**; everything else in an entry (`levels`, zones, `_comment_*`, `format_style`,
`signals`, `monitor_provider`, `interval`, `period`) is hand-authored and preserved
byte-identical. A sync that regenerates zones would be worse than the drift it fixes:

- **Membership.** An enabled watch is matched against ledger assets by exact `monitor_provider`
  asset key first, else by normalized bare ticker (drop the `provider:` prefix and a trailing
  `USD`/`USDT`/`USDC`/`EUR` quote suffix, uppercase, strip `-`/`/`) — accepted when exactly one
  ledger asset normalizes to it, whose net is then classified exactly like an exact-key match
  (flat → removed watchlist candidate, negative → `negative_net`, never deleted; see below).
  A ledger net with absolute value
  `<= 1e-6` counts as flat: the watch is **removed** from the file and reported as a *watchlist
  candidate* carrying its verbatim hand-authored levels — re-ground the zones in the watchlist
  config, do not delete the watch outright (the ZEC failure mode). A **negative** ledger net is
  NOT flat (a perps short is recorded as SELL on the same `kraken:<PAIR>` key, or an over-sold
  residue): the watch is kept byte-identical and reported under `negative_net` — a short's size
  is not the ledger net of the key it shares with spot, so `position_size` is not rewritten.
  Unmatched/ambiguous watches (e.g. the same asset
  held in more than one portfolio) are kept untouched and reported, as are held ledger assets
  with no watch entry (`unwatched` — an entry cannot be invented; it needs hand-authored
  levels). Disabled watches (`"enabled": false`) are never pruned — the "close a position: set
  enabled false, config preserved for future re-adds" contract.
- **`position_size`** = the ledger FIFO net quantity rounded to 8 decimals (the file's
  quantity precision — the reference ETH case expects `0.03815984`, exactly
  `round(0.0381598383, 8)`). Disagreeing values are corrected and the correction recorded.
- **`entry_price`** is filled from the ledger FIFO average cost of the remaining lots ONLY when
  the entry has none (missing or `null`). A hand-authored `entry_price` is **never overwritten**
  — it anchors the watchdog's `drop`/`recovery` math and is a decision reference, not a ledger
  field (a staking reward priced at 0 drags the ledger avg cost below the hand-authored entry;
  only `position_size` is corrected).
- **Audit trail.** When (and only when) something about an entry changed, the entry gets
  (or extends) a single-line `_comment_ledger_sync` note, e.g.
  `position_size 3417.0 -> 385.59124734 (ledger-derived 2026-09-18T13:22:05Z)`, appended after
  ` | ` when earlier corrections exist. No other `_comment_*` key and not the top-level
  `_comment` are ever touched.
- **Stale levels are flagged, never rewritten.** If every `zone` level sits more than 25% below
  the reference price (the runner case), the entry is listed under `stale_levels` with its name
  and the distance — re-ground, never prune. When no price is known for a watch it is listed
  under `price_unavailable` instead of crashing.
- **Idempotent.** Serialization detects the file's indent width, ascii mode and trailing
  newline, so untouched entries and untouched regions round-trip byte-identically; corrected
  entries get only their changed value tokens spliced into their original raw bytes
  (`position_size`, an inserted `entry_price`, the `_comment_ledger_sync` string), so
  hand-authored `levels`/`signals` — including literals like `500.00` — stay byte-identical.
  When nothing changed the file is not written at all (mtime untouched). Writes are atomic
  (sibling temp file + `os.replace`).

**Post-fill trigger.** Drift is created by fills, so both `execution-kraken-spot` and
`execution-kraken-perps` run this sync automatically immediately after a successful
`write_fill_to_portfolio` (see
`analysis.providers.execution._cli_common.sync_open_positions_after_fill`). The hook prints
one stderr line (`open-positions: no drift` / `open-positions: LIT position_size 3417.0 ->
385.59124734`) and never changes the submit's exit code; when `$MARKET_SKILLS_OPEN_POSITIONS_PATH`
is unset it prints one warning and skips. No cron job is added anywhere — run the subcommand on
demand for everything else.

## Troubleshooting

**`OSError: MARKET_SKILLS_PORTFOLIO_DB is not set`** — the env var is missing from the current shell. Either export it to the path of your portfolio SQLite database:

    export MARKET_SKILLS_PORTFOLIO_DB=/absolute/path/to/portfolio.db

or source the shell-init file that defines it (e.g. your agent runtime's profile env file). Pass `--db=PATH` to override the env var for a single invocation. The contract is documented in `skills/portfolio-mgmt/lib.py::default_db_path()`.
