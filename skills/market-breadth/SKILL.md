---
name: market-breadth
description: "Cross-sectional breadth: what share of a watchlist basket outperformed the benchmark (default BTC) over a rolling N-day window. Ticker-agnostic rotation read — returns pct_beating, median member return, leaders/laggards, and an alt_rotation/mixed/btc_led regime label with uncalibrated bands. Use to decide whether the multi-day swing book leans into alts or into BTC this week."
version: 0.1.0
metadata:
  hermes:
    tags: [market, breadth, rotation, regime, crypto, alts]
    category: market
compatibility: "Requires Python 3.12+ and uv; network access to the providers named in market-watchlist (kraken by default)"
---

# market-breadth

Cross-sectional breadth read. One call → one breadth payload: the percentage
of a `market-watchlist` basket whose N-day return is strictly greater than the
benchmark's. **Ticker-agnostic** — there is no positional ticker; the universe
comes from a watchlist basket and the benchmark resolves from the same
registry (alias `btc` → canonical ticker). Logic lives skill-local in
`lib.py` (`compute_breadth` pure math, `analyze` for resolution + fetch);
the skill is deliberately NOT registered in `analysis/registry.py`
([ADR-0010](../../docs/adr/0010-cross-sectional-breadth-is-not-an-l2.md)).

## When NOT to use

- As an entry-timing signal — breadth is a rotation/regime read for the
  multi-day swing book, not a per-ticker trigger. Use market-* / strategy-*
  skills for entries.
- As an L2 input — it is not a pattern detector: no single ticker, no
  `{pattern, signals, input_scores, narrative}` shape, not consumable by L3
  composition code (ADR-0010).
- For conviction or sizing — not wired into L3 conviction or risk sizing yet;
  narrate-only.
- When members lack history — each series needs >= 2 usable daily closes;
  members with less are excluded and named in `errors[]`.

## Quick Start

```bash
uv run skills/market-breadth/scripts/run.py --json
```

Defaults: `--basket=crypto_alts`, `--window-days=7`, `--benchmark=btc`.

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--json` | human | Emit the AXI envelope to stdout. |
| `--basket=NAME` | `crypto_alts` | Watchlist basket to measure. Missing/empty basket → AXI empty state, never raises. |
| `--window-days=N` | `7` | Requested lookback in days. `<= 0` → usage error, exit 2. Longer than the shortest available history → truncated to the effective window, disclosed in `errors[]`. |
| `--benchmark=ALIAS` | `btc` | Benchmark alias resolved through the watchlist; the resolved ticker is excluded from the member set. |
| `--full` | off | Full payload instead of the default field projection. |
| `--fields=<csv>` | default schema | Project specific fields. |
| `--toon` | off | Opt-in TOON encoding. |

## What it returns

```json
{
  "data": {
    "window_days": 7,
    "effective_window_days": 7,
    "benchmark": "BTCUSD",
    "basket": "crypto_alts",
    "members": 19,
    "pct_beating": 63.2,
    "btc_return_pct": 10.53,
    "median_alt_return_pct": 16.6,
    "regime": "alt_rotation",
    "leaders": [{"ticker": "<TICKER>", "return_pct": 91.36}],
    "laggards": [{"ticker": "<TICKER>", "return_pct": 2.88}],
    "narrative": "Breadth 7d: 63.2% of 19 members beating BTCUSD (BTCUSD +10.53%, median +16.60%) -> alt_rotation. Regime bands (>=60 / <=40) are uncalibrated first guesses - trust pct_beating, not the label.",
    "errors": []
  },
  "count": null,
  "errors": [],
  "help": ["..."]
}
```

- `members` is the `pct_beating` denominator: members actually measured.
  Members excluded for unusable history are named in `errors[]`.
- Ties do not count as beating — a member whose return equals the
  benchmark's is not beating. The strict comparison runs on unrounded
  returns, so a member strictly above the benchmark on raw values still
  counts even when both round to the same 2dp value; reported values
  are rounded to 2dp.
- `benchmark` is the resolved canonical watchlist ticker, not a display string.
- `leaders` / `laggards` are the top/bottom 3 measured members, deterministic
  on ties (sorted by return, then ticker).

### Regime bands

| `pct_beating` | `regime` | Reading |
|---|---|---|
| `>= 60.0` | `alt_rotation` | Broad participation — alt swing setups more likely to follow through |
| `<= 40.0` | `btc_led` | Narrow breadth — alt breakouts more likely to fail |
| between | `mixed` | No rotation edge |

**These bands are uncalibrated first guesses**, picked by hand rather than
fitted against historical data. The raw `pct_beating` is always in the
payload and the narrative repeats the caveat — trust the number, not the
label. Calibration is a follow-up once there is history to calibrate
against.

## Edge cases

- **Missing / empty basket** → AXI empty state (`data: null`, `count: 0`),
  `errors[]` names the basket, `help[]` lists the available basket names.
  Never raises at the CLI.
- **Unresolvable or ambiguous benchmark alias** → empty state naming the alias.
- **Window truncation** → one shared effective window:
  `min(window_days, shortest available history across every measured series
  and the benchmark)` — never compare a 7d alt return against a 3d benchmark
  return. The payload carries `effective_window_days` and `errors[]` carries
  `[BREADTH WINDOW TRUNCATED — requested Nd, used Md (shortest available history)]`.
  No exception.
- **Member fetch failure or short history** → excluded from the measurement
  and named in `errors[]` (`[BREADTH <TICKER> FETCH FAILED ...]` /
  `[BREADTH <TICKER> SKIPPED ...]`); the rest of the basket still returns.
- **Benchmark in the basket** → dropped silently from the member set
  (comparing the benchmark to itself is meaningless).
- **Benchmark with < 2 usable closes** → empty state with an actionable error.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md)
(ADR-0004) — `{data, count, errors, help[]}`. Default schema is the minimal
field list (`basket, window_days, members, pct_beating, regime, narrative`);
`--fields=<csv>` projects, `--full` returns everything. `count` is `null`
(one cross-sectional reading). Errors are never cached state.

## Home view (no-arg mode)

No-arg mode prints the home view from the last cached successful run
(`$XDG_DATA_HOME/market-skills/market-breadth_last.json`), or the
"no cached state" fallback on a fresh install.
