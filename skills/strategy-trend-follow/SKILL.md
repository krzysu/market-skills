---
name: strategy-trend-follow
description: "L3 trend-following strategy. Enters long in healthy uptrends, short in healthy downtrends. Composes market-trend-quality and market-breakout for entry timing."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, trend, follow, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-trend-follow

L3 strategy that enters with the dominant trend at pullbacks or breakouts. Composes L2 verdicts from trend-quality and breakout detection.

## Quick Start

```bash
uv run skills/strategy-trend-follow/scripts/run.py SPY
uv run skills/strategy-trend-follow/scripts/run.py SPY --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--asset-class` | auto | Asset class for maturity threshold scaling (resolved from watchlist metadata; see Pattern S). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-trend-quality | Assess trend health (HEALTHY_UPTREND / HEALTHY_DOWNTREND / HEALTHY_PULLBACK_UPTREND / WEAKENING) |
| market-breakout | Detect fresh breakouts for entry timing |

## Entry Logic

- **Long — healthy trend**: trend-quality is HEALTHY_UPTREND (or HEALTHY_PULLBACK_UPTREND) + breakout at resistance → limit at EMA pullback or market at breakout. Pullback setups get conviction −1 (less confirmation).
- **Long — weakening with intact structure**: trend-quality is WEAKENING but the underlying HH/HL + EMA alignment from market-trend is still bullish → partial bull re-entry at current price. Conviction is confidence −1 (capped at 5, floor at 1).
- **Short**: trend-quality is HEALTHY_DOWNTREND + breakdown at support → limit at EMA bounce or market at breakdown
- **Stop**: below recent swing low (long) / above recent swing high (short) — approximated via ATR
- **Targets**: 1.5R, 2.5R, 4R where R = entry - stop

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning, source_skills
  - Each idea carries `version: "v1".."v5"` derived from `conviction` via `analysis.contracts.conviction_version()`
  - Each idea carries `move_maturity_pct` and `entry_window_validity_pct` — observational fields used by Pattern S (see below)
  - Each idea carries `take_profit_ideal` (unrounded construction) and `rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]` (precomputed R:R to each TP via `analysis.contracts.compute_rr_to_tp()`) so consumers can read a canonical R:R without reimplementing the direction-asymmetric formula
  - Each idea is validated against `validate_l3_tp_ladder()` (TP3 ≥ entry × 1.05 long, or ≤ entry × 0.95 short, plus strict monotonicity + distinctness for sub-$1 ladders)
- `narrative` — summary for user briefing

## Pattern S — soft veto

Inlines the swing-scan cron's down-or-info rules at L3 emit time so consumers
running `strategy-trend-follow` standalone see the same protective downgrades.
Catches cases like an 80%-extended LONG emitted as `conv=4` with no maturity
signal — late chase-risk that downstream prompts would have to manually veto.

When any Pattern S condition fires the idea gets `veto_reasons: list[str]` and
conviction is downgraded:

| Tag | Trigger | Conviction |
|---|---|---|
| `late-move` | `move_maturity_pct > 50 × mult` | −2 |
| `mature-move` | `move_maturity_pct > 30 × mult` | −1 |
| `chase-risk` | `entry_window_validity_pct > 2` | −1 |
| `entry-edge` | `entry_window_validity_pct > 0.5` | (info only) |
| `pullback-not-yet` | long: close < entry × 0.95 / short: close > entry × 1.05 | −1 |
| `asset-class-scaled` | multiplier > 1.0 (info only) | — |

The maturity thresholds scale by **asset class**. When the caller passes
`asset_class` (typically resolved from the watchlist metadata), the default
30%/50% floors are multiplied:

| `asset_class` | Multiplier | `mature-move` | `late-move` |
|---|---|---|---|
| (unset / blue-chip) | 1× | 30% | 50% |
| `perp_dex` | 6× | 180% | 300% |
| `low_float` | 6× | 180% | 300% |
| `ai_infra` | 2× | 60% | 100% |

When `multiplier > 1.0` the tag `asset-class-scaled` is added (zero conviction
delta) so downstream consumers can detect the scaling was active.

Conviction is clamped to `[1, 5]`. Ideas never silently drop — the cron prompt
can demote to `[INFO]` based on the `veto_reasons` tags. The reasoning field is
appended with `Pattern S: <tags>.` so the rationale is visible in human-readable
output.

## Conviction gate (entry-side filter)

After the Pattern S downward-conviction adjustments above, this strategy
applies an entry-side floor: any idea whose final conviction is below the
configured threshold is dropped before `analyze()` returns. The threshold
is per-(`strategy`, `ticker`, `interval`) and lives in
[`analysis/conviction_thresholds.py`](../../analysis/conviction_thresholds.py) —
read via `lookup_min_conviction("strategy-trend-follow", ticker, interval)`.
The lookup fall-through is `GLOBAL_MIN_CONVICTION_TO_EMIT = 1` (no-op; preserves
legacy emit-all behaviour for unknown combinations).

| Value | Effect |
|-------|--------|
| `0`   | Opt-out — every analyzed idea is emitted regardless of conviction. |
| `1`   | No-op — formula floor is `>= 1`, so all surviving ideas pass. |
| `>= 2`| Drops ideas with conviction strictly below the floor. |

Per-(ticker, interval) overrides live outside the open-source repo in the
JSON file pointed to by `$MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`. A/B
backtest evidence (per-tuner; specific tickers/interval values are
private) lives there. The shipped source ships with an empty override
table; without the env var, every combination resolves to the global
default (1, no-op). To add a per-(ticker, interval) entry, write it into
the external JSON rather than editing this lib file.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
