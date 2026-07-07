---
name: market-movers
description: "CoinGecko movers + categories brief with optional Kraken tradability cross-reference. Gainers, losers, trending (24h), and category rotation (CoinGecko /coins/categories) feed the morning brief. Each CoinGecko panel retries with exponential backoff (1s/2s/4s); final 429 on a panel degrades that panel with an explicit marker in `note`. Kraken tradability is sourced from `kraken pairs -o json` with an in-process TTL cache; CLI absence degrades the per-entry `tradable_on` field to `null` with an explicit marker."
version: 0.2.0
metadata:
  hermes:
    tags: [movers, gainers, losers, trending, categories, coingecko, kraken, brief, discovery]
    category: briefing
compatibility: "Requires Python 3.12+, uv, network egress to api.coingecko.com, and the `kraken` CLI on PATH for the tradability cross-reference (graceful degrade when absent)."
---

# market-movers

Cross-asset movers brief source. Pulls four panels for the morning
brief, with one optional cross-reference against the user's execution
venue:

- **Gainers** — top N by 24h % change (`/coins/markets?order=percent_change_24h_desc`).
- **Losers** — same payload, ascending.
- **Trending** — top N from `/search/trending` (separate quota tier).
- **Categories** — top N from `/coins/categories` ordered by 24h market-cap change (the "where is the rotation" signal next to per-coin movers).
- **`tradable_on`** — per-entry, optional. Cross-references each gainers/losers/trending entry against `kraken pairs -o json` so the brief is *actionable* (you can actually execute it on Kraken), not just informative.

## Quick start

```bash
# JSON for LLM tool-use (preferred for batch + morning brief)
uv run skills/market-movers/scripts/run.py --json

# Human-readable
uv run skills/market-movers/scripts/run.py

# Wider panels / more retries
uv run skills/market-movers/scripts/run.py --top-n=10 --retries=5 --json

# CI / log-only run without the `kraken` CLI on PATH
uv run skills/market-movers/scripts/run.py --no-tradable-filter --json
```

## Panels + endpoint matrix

| Panel | Endpoint | Quota | Degrade when 429 |
|-------|----------|-------|------------------|
| Gainers (24h top N) | `/coins/markets?order=percent_change_24h_desc` | shared ~30 req/min | drop + `[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]` |
| Losers (24h top N) | same payload, sort ascending | same | same |
| Trending | `/search/trending` | higher tier | drop silently (rare) |
| Categories (24h rotation) | `/coins/categories?order=market_cap_change_24h_desc` | same bucket as `/coins/markets` | drop + `[MOVERS API RATE-LIMITED — categories unavailable this run]` |
| Tradable cross-ref | `kraken pairs -o json` (in-process TTL cache) | local CLI | empty fields + `[MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]` |

Each CoinGecko panel retries with exponential backoff (`1s, 2s, 4s, ...`)
up to `--retries` (default 3 → two backoff sleeps of `1s` and `2s`).
Trending is preserved when gainers/losers rate-limit; categories is a
separate panel so it can degrade independently.

The tradability cross-reference is opt-in via `--no-tradable-filter`.
When enabled, `kraken pairs -o json` is invoked once per process and
cached for `--kraken-pairs-ttl-s` seconds (default 600s). The CLI is
invoked directly via `subprocess.run` — no Python adapter layer.

## CLI flags

| Flag | Default | Notes |
|------|---------|-------|
| `--top-n` | 7 | Entries per coin panel (gainers/losers/trending). Brief uses 7; widen for a deeper dive. |
| `--categories-top-n` | 10 | Categories panel size. 0 to skip the panel entirely. |
| `--retries` | 3 | Per-panel retry count. Final 429 after N attempts → rate-limit escalation for that panel. |
| `--no-tradable-filter` | off | Skip the `kraken pairs` lookup. Use in CI without the `kraken` CLI on PATH, or when brief size matters and the cross-ref isn't needed. |
| `--kraken-pairs-ttl-s` | 600 | In-process cache TTL for the AssetPairs response. A daily brief makes at most one subprocess call per day at this default. |
| `--json` | off | Emit JSON to stdout (LLM tool-use). |

Exit code is 0 on success and degrade alike — a rate-limited or
CLI-missing run is a *known-degraded* outcome, not an error. Scheduled
runs can disable the rate-limit log line via
`MARKET_SKILLS_NO_RATE_LIMIT_LOG=1` if needed.

## Output shape (JSON)

```json
{
  "fetched_at": "2026-06-29T05:00:00+00:00",
  "gainers": [
    {
      "id": "bitcoin", "symbol": "BTC", "name": "Bitcoin",
      "pct_24h": 1.42, "price_usd": 62150.0,
      "market_cap_rank": 1,
      "tradable_on": {"kraken": true, "altname": "XBTUSD",
                      "base": "XXBT", "quote": "ZUSD"}
    }
  ],
  "losers":   [/* same shape, negative pct */],
  "trending": [{"id": "...", "symbol": "...", "name": "...",
                "market_cap_rank": 7,
                "tradable_on": {"kraken": false}}],
  "categories": [
    {"id": "ai", "name": "Artificial Intelligence",
     "market_cap_usd": 12300000000, "pct_24h": 12.34,
     "top_3_coins_id": ["render-token", "fetch-ai", "the-graph"]}
  ],
  "rate_limited": false,
  "attempts": {"gainers_losers": 1, "trending": 1, "categories": 1},
  "note": "",
  "kraken_cli_available": true,
  "tradable_filter": true
}
```

### `tradable_on` field

Three states per entry, depending on the tradability state at fetch
time:

| Value | Meaning |
|-------|---------|
| `null` | The Kraken AssetPairs cross-ref was attempted but the `kraken` CLI is missing (`FileNotFoundError`) or returned an unparseable payload. Read top-level `kraken_cli_available` to distinguish. The LLM agent brain should NOT treat this as "CoinGecko says this is hot AND I can trade it on Kraken" — the cross-ref signal is silently unavailable. |
| `{"kraken": false}` | AssetPairs response parsed cleanly, no Kraken pair matched the entry. Often: low-cap coins not yet listed on Kraken, or symbol-collision edge cases. |
| `{"kraken": true, "altname", "base", "quote"}` | The entry is tradable on Kraken. `altname` is the Kraken instrument name (e.g. `XBTUSD`) suitable for `execution-kraken-spot submit`. Quote preference is stablequote (`ZUSD`/`USDT`/`USDC`) > ZEUR/ZGBP > XBT > XETH. |

Symbol-collision caveat: a CoinGecko entry whose `id` is not in the
built-in `_COINGECKO_ID_TO_KRAKEN_BASE` map (top-50 by market cap)
falls back to symbol-only lookup, which can match a Kraken pair for a
different coin sharing the symbol (e.g. a low-cap chain that publishes
a token literally named `BTC`). The built-in map prevents the most
likely collisions; consumers that need a precise answer for an unmapped
coin should cross-reference the CoinGecko `id` against Kraken's own
listing pages.

### `note` markers

Multiple degrade markers can stack. Examples:

- Gainers/losers rate-limited, Kraken CLI OK:
  `"[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]"`
- Categories rate-limited only: `"[MOVERS API RATE-LIMITED — categories unavailable this run]"`
- Kraken CLI missing: `"[MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]"`
- Gainers/losers rate-limited AND Kraken CLI missing:
  `"[MOVERS API RATE-LIMITED — gainers/losers unavailable this run] [MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]"`

The morning-brief prompt reads `note` and surfaces it verbatim so the
user knows the brief was partial.

## Rate-limit log

Path: `$XDG_DATA_HOME/market-skills/coingecko-rate-limit.log` (one JSON
line per CoinGecko 429 incident — gainers/losers, trending, and
categories each log independently). The Kraken CLI lookup has no
rate-limit log; its absence degrades to `tradable_on: null` and is
captured in `note`. When `XDG_DATA_HOME` is unset, the CoinGecko log is
silently skipped (no console fallback). One record per panel —
`endpoint` is the full URL, `attempts` is the actual retry count,
`final_status` is the HTTP code that broke the retry chain.

Disable for clean test runs: `export MARKET_SKILLS_NO_RATE_LIMIT_LOG=1`.

## Failure modes

- CoinGecko 429 × retries on `/coins/markets` → degrade that panel with the explicit `note` marker; trending stays populated.
- CoinGecko 429 × retries on `/coins/categories` → degrade that panel with the explicit `note` marker; movers unaffected.
- `kraken` CLI absent → every entry's `tradable_on` is `null`; `kraken_cli_available: false`; `note` carries the explicit `KRAKEN CLI UNAVAILABLE` marker.
- `kraken pairs -o json` timeout (default 30s) → same shape as CLI absent (caller can't distinguish).
- `kraken pairs -o json` returns unparseable JSON → same shape as CLI absent.
- `kraken pairs -o json` returns empty dict → empty index; entries get `tradable_on: {"kraken": false}` (legitimate "Kraken lists no online pairs" answer).
- Network error × retries on CoinGecko → empty payload, `rate_limited: false` (we don't conflate network errors with throttling).
- Schema change (CoinGecko renames a field) → field is `None` in the output; no crash, no alert. Consumers that need a specific field should treat `None` as "unavailable".
- Empty response → empty panel, no note.

## Data flow

`lib.fetch_movers(*, top_n, retries, tradable_filter, categories_top_n,
kraken_pairs_ttl_s, sleeper, kraken_runner, now_s)` is a pure-function-
shaped call. `sleeper` defaults to `time.sleep` and tests pass a no-op;
`kraken_runner` defaults to `subprocess.run` and tests pass a stub;
`now_s` defaults to `time.time` and tests pass a fixed-time callback.

The CLI parses flags, calls the lib, prints either JSON or a formatted
table. No DB, no portfolio context, no env-mutating side effects beyond
the CoinGecko rate-limit log.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.
