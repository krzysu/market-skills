---
name: backtest-pipeline
description: "Nightly backtest pipeline — runs every L3 strategy against every active watchlist ticker on 1d + 4h intervals, compares against a rolling 7-night Sharpe baseline, detects strategy decay, and produces six cross-boundary output files consumed by downstream skills (DTP conviction floor, ESD conviction modulation, Position Watchdog regime, Swing Scan skip list, hold-regime opportunities, Morning Brief)."
version: 0.1.0
metadata:
  hermes:
    tags: [backtest, pipeline, cron, nightly, sharpe, fitness, decay, regime]
    category: backtest
  compatibility: "Requires Python 3.12+ and uv"
---

# backtest-pipeline

Nightly backtest pipeline — the sole producer of six cross-boundary analysis files consumed by downstream skills. Runs every L3 strategy against every active watchlist ticker, compares against a rolling 7-night Sharpe baseline, and detects strategy decay or improvement.

## When to use

- Scheduled cron at 02:00 CEST — after the feedback absorber (01:00), before the morning brief (05:00).
- Manual re-run after a new strategy or ticker is added to the registry / watchlist.
- Diagnostics: "what does the backtest say about this ticker/strategy right now?"

## When NOT to use

- As a real-time signal — the pipeline uses 1y of daily bars; it's a regime trend, not an entry trigger.
- For ad-hoc single-ticker backtests — use `backtest-engine` directly.
- To generate a heatmap on custom tickers/intervals — the heatmap was merged into this pipeline's `fitness_matrix.json` output.

## Quick Start

```bash
# The only required env var
export MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR=/path/to/data/backtest-nightly

# Optional: needed only for watchdog regime output
export MARKET_SKILLS_BACKTEST_PIPELINE_OPEN_POSITIONS_PATH=/path/to/open-positions.json

# Run all baskets
uv run skills/backtest-pipeline/scripts/run.py

# Run specific baskets
uv run skills/backtest-pipeline/scripts/run.py --baskets crypto_majors crypto_alts
```

## Output files

All files are written to `$MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR`:

| File | Consumer | Path resolution |
|------|----------|-----------------|
| `conviction_thresholds_private.json` | DTP via `analysis/conviction_thresholds.py` | `MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH` or `<OUT_DIR>/conviction_thresholds_private.json` |
| `fitness_matrix.json` | ESD (emerging-setup-detector) | `<OUT_DIR>/fitness_matrix.json` |
| `watchdog_regime_state.json` | Position Watchdog | `MARKET_SKILLS_REGIME_STATE_PATH` or `<OUT_DIR>/watchdog_regime_state.json` |
| `swing_scan_skip_list.json` | Swing Scan | `<OUT_DIR>/swing_scan_skip_list.json` |
| `hold_regime.json` | LLM / morning brief opportunity section | `<OUT_DIR>/hold_regime.json` |
| `regime_health_brief.md` | Morning Brief | `<OUT_DIR>/regime_health_brief.md` |

`swing_scan_skip_list.json` splits tickers three ways: `skip_tickers` (all strategies
negative Sharpe), `no_trade_tickers` (zero-signal / blind pairs — no trade signals on
any strategy/interval), and `keep_tickers`. Blind pairs are surfaced, not silently
excluded. The `reason` names each bucket it covers.

## Hold-regime opportunity signal

`hold_regime.json` surfaces a positive **opportunity** signal the ⚖️ footnotes only
hinted at: a `(ticker, interval)` pair where the edge is *exposure, not timing* —
buy-and-hold beats every measured strategy by a wide margin. A pair flags when ALL
of:

- `benchmark_sharpe > 0`
- `benchmark_total_return > 0`
- at least `HOLD_REGIME_MIN_STRATEGIES = 3` strategies carry a non-`insufficient_data` record
- every measured strategy satisfies
  `benchmark_sharpe - strategy_sharpe >= HOLD_REGIME_MIN_GAP = 1.0` (inclusive at exactly 1.0)

Entries carry `min_gap` / `max_gap` (the smallest and largest per-strategy gap,
two decimals) so the reader can judge strength, and are sorted by `max_gap`
descending. A non-`insufficient_data` record with a null `strategy_sharpe` is a
destroyed curve (`bankrupted`) and is skipped: it neither counts toward
`strategies_measured` nor blocks the flag. An empty list is the legitimate steady
state and is rewritten every night — stale flags never survive a quiet run.

The regime health brief renders a `### 🎯 Hold-regime assets (edge is exposure,
not timing)` section when the list is non-empty and omits the section entirely
when it is empty. **This signal is additive**: it does NOT change conviction
thresholds — a hold-regime ticker's strategies have negative Sharpe and stay
floor-99-suppressed; no combo is un-suppressed.

## Measured strategy set

The pipeline measures the **whole L3 registry minus a declared
`UNMEASURABLE_STRATEGIES` map** (`scripts/run.py`) — never a positional
slice. Each excluded entry carries its reason; the run record logs it under
`excluded_strategies`. Today only `strategy-funding-carry` is excluded: the
engine feeds spot price bars and there is no perp funding data, so every
funding-carry pair errors. `strategy-liquidity-sweep` **is** measured — it
gets fitness data and a conviction floor like every other strategy.
See [ADR 0005](../../docs/adr/0005-measure-the-whole-l3-registry.md).

## Engine child isolation

Each per-pair backtest runs the engine as a child process with a sanitised environment
(`_engine_child_env()`): both `MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH` and
`MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR` are removed, and
`MARKET_SKILLS_CONVICTION_GATE=off` is set. This prevents the engine from loading the
`conviction_thresholds_private.json` file the pipeline itself writes — a floor of 99
inherited from last night would drop every idea, record zero trades, and re-lock the
floor at 99. The gate module (`analysis/signals/conviction_thresholds.py`) treats
`MARKET_SKILLS_CONVICTION_GATE` off/0/false/no as a kill switch: no overrides are
loaded and lookups return the shipped default floor 1.

Consumer-side env vars are **overrides** — unset them to use the default `<OUT_DIR>/<filename>`. The typical cron config sets only ``OUT_DIR`` and the two existing consumer-side overrides for backward compat.

## Bankruptcy handling in `conviction_thresholds_private.json`

The engine reports `sharpe: null` for a combo whose equity curve went
non-positive (`bankrupted: true` — a destroyed base makes the signed ratio
metrics meaningless). `_write_conviction_thresholds()` does not skip such a
combo: an absent key would fall through to `GLOBAL_MIN_CONVICTION_TO_EMIT = 1`
("trade this"), grading a destroyed curve as tradeable. Instead it writes an
explicit **non-tradeable floor of 99** for the flagged combo — the same floor
a numeric `Sharpe <= 0` maps to, since a destroyed curve is strictly worse.

`insufficient_data` combos (too few forward bars) are still skipped and get
no entry: an absent key keeps meaning "no opinion" for the
genuinely-unmeasured case. The consumer side
(`analysis/signals/conviction_thresholds.py::lookup_min_conviction`) resolves
a floor of 99 to never-emit, so downstream DTP runs drop every idea for that
combo. The lookup is notation-robust on the read side: keys are written in
`provider:ticker` notation, but a floor binds even when the emit path hands
the strategy a bare symbol (`PENDLEUSD`) or a separator form
(`PENDLE-USD`) — the query's canonical symbol is matched against the stored
keys, with provider-prefix preference and no guessing when same-symbol
candidates disagree.

## Minimum-trades guard in `conviction_thresholds_private.json`

A combo's Sharpe is only meaningful on a statistically meaningful sample. A
handful of trades can score a large Sharpe and get a permissive floor
(`1 = emit at any conviction`), surfacing live as a trade idea on noise —
the same failure class as the bankrupted-curve case above, reached with
real metrics instead of fake ones. `_write_conviction_thresholds()` and
`_write_regime_health_brief()` both derive "withheld" from a single helper
(`_withheld_low_trades()`), so the writer and the brief can never disagree.
A combo whose `trades` count is below the minimum-trades threshold gets the
bankruptcy remedy — an explicit **non-tradeable floor of 99**, never a
skip — and is excluded from the regime brief's Top-5 / Bottom-5 rankings
with a one-line count explaining the absence. A missing `trades` key is
treated as 0 (withheld). Every withheld combo is recorded in the run
record under `withheld_low_trades` (combo, strategy, ticker, trades,
`min_trades`, reason), shaped like `excluded_strategies`. The threshold is
`MARKET_SKILLS_BACKTEST_PIPELINE_MIN_TRADES` (default 10, `0` disables);
see [ADR 0006](../../docs/adr/0006-minimum-trades-guard.md). The
"Strategy Health (avg Sharpe across all tickers)" aggregate table and the
swing-scan partition are deliberately not combo-level top-N rankings and
are left untouched.

## Contracts

All output file contracts are defined in `lib.py` as TypedDicts with validation functions:

- `FitnessMatrix` / `validate_fitness_matrix()` — Sharpe pivot table (intervals → tickers × strategies)
- `WatchdogRegimeState` / `validate_watchdog_regime()` — per-position per-strategy regime status
- `SwingScanSkipList` / `validate_swing_scan_skip()` — ticker triage list
- `HoldRegime` / `validate_hold_regime()` — hold-regime opportunity flags per (ticker, interval)
- `validate_regime_brief()` — Markdown structural check
- `conviction_thresholds_private.json` contract is owned by `analysis/conviction_thresholds.py`

## Configuration

| Env var | Required | Purpose |
|---------|----------|---------|
| `MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR` | **Yes** | Base directory for all 6 files + rolling state |
| `MARKET_SKILLS_BACKTEST_PIPELINE_OPEN_POSITIONS_PATH` | No | Source for watchdog regime output |
| `MARKET_SKILLS_WATCHLIST_PATH` | No | Watchlist JSON (falls back to repo default) |
| `MARKET_SKILLS_BACKTEST_PIPELINE_MIN_TRADES` | No | Minimum trade count for a combo's Sharpe to be trusted (default 10; `0` disables the guard) |

## Architecture

- **Ticker discovery**: `analysis.watchlist.categories()` — iterates all baskets in the watchlist. Use `--baskets` to target specific ones.
- **Strategy discovery**: `analysis.registry.l3_strategies()` filtered through `measured_strategies()` — the whole registry minus declared `UNMEASURABLE_STRATEGIES` ([ADR 0005](../../docs/adr/0005-measure-the-whole-l3-registry.md))
- **Per-pair execution**: shells out to `uv run skills/backtest-engine/scripts/run.py` with `--fill-sim --metrics --json`
- **Baseline**: rolling 7-night average Sharpe per `{interval}×{strategy}×{ticker}`
- **Decay detection**: Sharpe zero-crossing (improvement or decay), ≥0.5 absolute delta, benchmark vs strategy comparison
- **Runtime**: 6 strategies × tickers × 2 intervals, 120s timeout per pair
