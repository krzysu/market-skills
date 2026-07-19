---
name: strategy-liquidity-sweep
description: "L3 sweep-following strategy. Enters after liquidity sweeps with accumulation confirmation. Composes liquidity-sweep, accumulation, and volume."
version: 0.1.0
metadata:
  hermes:
    tags: [strategy, liquidity, sweep, crypto, l3]
    category: strategy
compatibility: "Requires Python 3.12+ and uv"
---

# strategy-liquidity-sweep

L3 strategy that enters after liquidity sweeps when accumulation confirms smart money is positioning the opposite direction. Works especially well on crypto where stop hunts are frequent.

## When NOT to use

- Without a mandatory `risk-engine` vet before execution — this skill emits ideas, not orders. Always vet the Intent and get explicit user approval.
- On a trending tape — a sweep can be a continuation retest, not a reversal; require accumulation confirmation (`market-accumulation`) and volume reversal (`market-volume`).
- For a single-indicator read — it composes `market-liquidity-sweep` + `market-accumulation` + `market-volume`; call those directly if you only want the pattern.

## Quick Start

```bash
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## Composes

| L2 Skill | Purpose |
|----------|---------|
| market-liquidity-sweep | Detect support/resistance sweeps and double tests |
| market-accumulation | Confirm accumulation after the sweep |
| market-volume | Volume confirmation of reversal |

## Entry Logic

- **Long**: support sweep detected + accumulation pattern + volume confirms reversal
- **Short**: resistance sweep detected + distribution signals + volume confirms rejection
- **Stop**: below sweep low (long) / above sweep high (short)
- **Targets**: 2R, 3R

## Conviction calibration

The conviction number each idea carries comes from
`conviction_from_confidences(sweep_conf, accum_conf, *, mode)` in `lib.py`
(see `analyze`, which calls it with `mode="current"`). The shipped `current`
formula is `min(5, sweep + accum // 2)`. Before changing this constant, a grid
search scaffold lives at `scripts/conviction_grid.py`.

```bash
# Offline smoke test (synthetic candles, no network):
uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py --demo

# Real calibration: tally the conviction each formula would emit per fired bar:
uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py \
    --tickers BTCUSD,ETHUSD,SOLUSD,<TICKER3>USD,<TICKER4>USD,<TICKER5>USD \
    --interval 1d --period 1y --warmup 200

# Out-of-sample: tally only the last 30% of each series (leading 70% = warmup
# context) so the formula is selected WITHOUT peeking at the deploy sample:
uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py \
    --tickers BTCUSD,ETHUSD --interval 1d --period 1y --warmup 200 --holdout

# Validate candidate bands against the operator journal. The path comes from
# LIQ_SWEEP_JOURNAL_PATH; the script raises if unset (no host-path default):
export LIQ_SWEEP_JOURNAL_PATH=<path-to-journal>/picks.json
uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py \
    --tickers BTCUSD,ETHUSD --holdout --validate-journal
```

Modes: `current` (`min(5, sweep + accum // 2)`), `add` (`min(5, sweep + accum)`),
`add_minus_one` (`min(5, sweep + accum - 1)`), `max_plus_one` (`min(5, max(sweep, accum) + 1)`).

The grid only **reports** the per-mode conviction distribution (and, with
`--validate-journal`, the journal's per-band hit rate) — it never edits the
shipped formula. Change the constant in `lib.py` only after the grid shows a
healthy number of `conv >= 3` fires per scan **without** inflating the
`conv = 2` band (the negative-EV band per journal evidence), and after
confirming the out-of-sample distribution matches the in-sample one.

### Conviction gate (entry-side filter)

After the conviction formula returns, this strategy applies an entry-side
floor: any idea whose final conviction is below the configured threshold is
dropped before `analyze()` returns. The threshold is per-(`strategy`,
`ticker`, `interval`) and lives in
[`analysis/conviction_thresholds.py`](../../analysis/conviction_thresholds.py)
— read via `lookup_min_conviction("strategy-liquidity-sweep", ticker, interval)`.

`strategy-liquidity-sweep`'s bucket ships **empty** — the per-band backtest
evidence (597-fill `conv=2` Sharpe `-0.32` is negative-EV, while `conv=5`
is split ticker-dependent) is ambiguous without journal accumulation,
so conservative no-ship wins for the open-source default. The grid's
`--validate-journal` workflow above is the path to per-ticker entries:
confirm a stable threshold per ticker before populating the central
table (set `MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH` to your private
JSON to ship overrides without touching this file).

## Output

- `ideas[]` — trade ideas with direction, conviction, entry/stop/target, reasoning
  - Each idea carries `version: "v1".."v5"` derived from `conviction` via `analysis.contracts.conviction_version()`
  - Each idea carries `take_profit_ideal` (unrounded construction) and `rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]` (precomputed R:R to each TP via `analysis.contracts.compute_rr_to_tp()`) so consumers can read a canonical R:R without reimplementing the direction-asymmetric formula
  - Each idea is validated against `validate_l3_tp_ladder()` (TP3 ≥ entry × 1.05 long, or ≤ entry × 0.95 short)
- `narrative` — summary for user briefing

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
