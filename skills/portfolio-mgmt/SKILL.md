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

## Quick Start

```bash
uv run skills/portfolio-mgmt/scripts/run.py init
uv run skills/portfolio-mgmt/scripts/run.py portfolio create --name spot
uv run skills/portfolio-mgmt/scripts/run.py add --portfolio spot --asset=kraken:BTCUSD --side buy --qty 0.01 --price 45000
uv run skills/portfolio-mgmt/scripts/run.py add --portfolio spot --asset=kraken:HYPEEUR --side buy --qty 10 --price 33
uv run skills/portfolio-mgmt/scripts/run.py prices refresh    # Fetch live prices for held assets
uv run skills/portfolio-mgmt/scripts/run.py positions
```

## CLI

```
init                                          # Create database (~/.market-skills/portfolio.db)
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

prices refresh                                # Fetch live prices via lib/data.py for held assets
allocation [--portfolio X] [--no-refresh]       # % breakdown by asset
performance [--portfolio X] [--no-refresh]      # Realized PnL, profit factor
replay [--portfolio X] [--json]                # FIFO audit trail — per-lot creation & consumption
reconcile --portfolio X --balance-file FILE     # Compare DB positions vs external JSON snapshot
export [--portfolio X] [--format csv|json] [--output FILE]
```

All commands accept `--json` for machine output and `--db PATH` to override the default database location.
`init` creates the parent directory if it doesn't exist. Default DB is `~/.market-skills/portfolio.db`.
`view`, `positions`, `pnl`, `allocation`, `performance` automatically use cached prices from `prices refresh`. Override with `--price-override`.

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

The `provider:` prefix routes through `lib/data.py` for `prices refresh`. Assets without a `:` prefix are skipped (manual price only).

## Currency

Each portfolio has its own `base_ccy` (EUR, USD, USDC, etc.). All numbers are rendered in that portfolio's native currency. No cross-currency aggregation — each `by_portfolio` entry is self-contained.

**All transactions in a given portfolio must use the same base currency.** If you trade in both EUR and USD, create two portfolios (e.g., `spot-eur` and `spot-usd`).

`view`, `positions`, `pnl`, `allocation`, `performance` auto-refresh prices from `lib/data.py` on every call. Add `--no-refresh` to skip network calls and use stale cache. `--price-override` always takes precedence.

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

## Reconcile — external balance check

`reconcile` compares your computed positions against an external wallet snapshot (JSON file). Shows deltas between your manual tracking and actual holdings.

Snapshot format: `{"provider:ticker": qty, ...}` — only asset quantities, no cash balances.

```bash
# snapshot.json: {"kraken:BTCUSD": 0.15, "kraken:HYPEEUR": 10}
reconcile --portfolio 1 --balance-file snapshot.json
#   =   kraken:BTCUSD    0.15000000    0.15000000  +0.0       match
#   !=  kraken:HYPEEUR   10.00000000   9.50000000   +0.5       diff
#   -   hl:LIT            5.00000000   0.00000000   +5.0       missing_external
```

Status legend: `=` exact match, `!=` quantity mismatch, `+` asset in snapshot but not in DB, `-` asset in DB but not in snapshot.
