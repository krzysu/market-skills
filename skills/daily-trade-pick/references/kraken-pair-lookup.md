# Kraken Pair Format Lookup

Canonical mapping from analysis-ticker format (`BTC-USD`, `ETH-USD`, `SOL-USD`, etc.) to the **actual Kraken pair key** returned by `kraken ticker <PAIR>USD -o json`.

## Why this matters

Kraken prepends `X` and `Z` to some canonical pairs for the ticker API and doesn't for others. Newer pairs (post-2024 listings) typically skip the prefix. Hardcoding the wrong pair makes `kraken ticker` return a 404 or empty payload, which breaks the outcome-check loop in the daily-trade-pick cron.

**Pitfall (from SKILL.md):** `kraken ticker ETHUSD -o json` returns `{"XETHZUSD": {...}}`. Extract the first key from the response rather than assuming the key matches what you asked for. But for batch-processing many tickers in Python, a precomputed lookup is faster and avoids the per-call `next(iter(data))` dance.

## Canonical mapping (verified 2026-06-29)

The operator's specific mapping lives in their private watchlist. The
table below covers the **public** tier 2 set that's safe to commit:

| Analysis ticker | Kraken pair key | Note |
|---|---|---|
| `BTC-USD` | `XXBTZUSD` | X+Z prefix |
| `ETH-USD` | `XETHZUSD` | X+Z prefix |
| `SOL-USD` | `XSOLZUSD` | X+Z prefix |
| `XMR-USD` | `XXMRZUSD` | X+Z prefix |
| `PAXG-USD` | `PAXGUSD` | newer pair, no prefix |

HL-native tickers have no Kraken spot pair — they return `None` from this
lookup. Skip outcome-check price fetches for HL-native tickers or source
the price from `hl ticker` instead.

## Lookup function (drop-in for cron Python)

```python
def kraken_price(ticker_str, tickers_dict):
    """Map analysis ticker to Kraken pair key and return last price from a
    pre-fetched tickers dict (output of `kraken ticker ... -o json`).

    Operator-specific pairs (tier 1, ai_infra, etc.) live in the private
    watchlist — extend ``pair_map`` from your ``MARKET_SKILLS_WATCHLIST_PATH``
    before deploying."""
    pair_map = {
        "BTC-USD": "XXBTZUSD",
        "ETH-USD": "XETHZUSD",
        "SOL-USD": "XSOLZUSD",
        "XMR-USD": "XXMRZUSD",
        "PAXG-USD": "PAXGUSD",
    }
    kraken_pair = pair_map.get(ticker_str)
    if kraken_pair and kraken_pair in tickers_dict:
        return float(tickers_dict[kraken_pair]["c"][0])
    return None
```

For HL-native tickers, the function returns `None` — callers should
either skip outcome-check price fetches or substitute the HL mid-price
from `hl ticker <COIN>`.

## When to re-verify

Re-test this table whenever:
- A new ticker is promoted to tier 1 or tier 2 in `market-watchlist/data/watchlist.json` (the pair key is asset-specific, not inferable from the symbol alone)
- A pair is delisted from Kraken or migrated to a new key (rare but happens during pair renames)
- The cron logs a `KeyError` on a `kraken_pair` lookup

Quick verification one-liner (operator-specific tickers; extend as needed):

```bash
for pair in BTCUSD ETHUSD SOLUSD XMRUSD PAXGUSD; do
  key=$(kraken ticker "$pair" -o json 2>/dev/null | python3 -c "import json,sys; print(next(iter(json.load(sys.stdin))))")
  echo "$pair -> $key"
done
```
