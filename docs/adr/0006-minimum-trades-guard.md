# 0006. Minimum-trades guard for backtest conviction floors

- **Status**: accepted
- **Date**: 2026-09-17

## Context

The nightly `backtest-pipeline` wrote per-combo conviction floors with no
minimum-trades guard. In the 2026-09-17 00:04 UTC nightly, 55 of the
measured combos had fewer than 10 trades; e.g. 4h ×
strategy-accumulation-swing × PENDLEUSD scored Sharpe +3.03 on 5 trades and
received floor 1 (emit at any conviction), surfacing live as a trade idea on
a statistically meaningless sample — and the regime-health brief listed it
as a Top-5-by-Sharpe pick, presenting it as one of the best edges in the
book. This is the same failure class as market-skills-ww0 (a fake +1.12
Sharpe from a bankrupted curve) reached by a different route: ww0 removed
FAKE metrics; this admits REAL metrics computed on noise.

## Decision

Stage a minimum-trades guard in `_write_conviction_thresholds()` after
Sharpe/return metrics are computed and before floor values are written:

- **Configurable threshold**: `MARKET_SKILLS_BACKTEST_PIPELINE_MIN_TRADES`,
  default 10 (`DEFAULT_MIN_TRADES` in `lib.py`). An explicit `0` disables
  the guard; a malformed or negative value raises `ValueError` — never a
  silent fallback.
- **Explicit floor 99, never a skip**: a below-threshold combo gets the
  same non-tradeable floor as a bankrupted combo. An absent key would fall
  through to `GLOBAL_MIN_CONVICTION_TO_EMIT=1` ("trade this"), the exact
  downstream harm. No fourth floor value is invented. `insufficient_data`
  combos keep their current behaviour (skipped, no entry).
- **Single source of truth for "withheld"**: one helper
  (`_withheld_low_trades()`) returns the withheld combos with combo,
  strategy, ticker, trade count, threshold, and reason. Both
  `_write_conviction_thresholds()` and `_write_regime_health_brief()` use
  it, so the writer and the brief can never disagree. A missing `trades`
  key counts as 0 (withheld).
- **Run record**: `main()` logs `withheld_low_trades` — shaped in the spirit
  of the `excluded_strategies` reason list, with combo/ticker/trades/
  `min_trades` for auditability — and prints a one-line withheld count.
- **Ranking exclusion**: below-threshold combos are filtered out of the
  regime brief's Top-5 / Bottom-5 tables, with a one-line count explaining
  the absence. The "Strategy Health (avg Sharpe across all tickers)"
  aggregate table and the swing-scan partition are deliberately untouched:
  they are not combo-level top-N rankings and the acceptance criteria do
  not cover them.

## Consequences

- (+) Noise combos can no longer surface as live ideas: a high Sharpe on a
  meaningless sample writes floor 99, which the consumer resolves to
  never-emit.
- (+) The withheld reason is auditable in the run record and explained in
  the brief rather than silent.
- (-) Genuinely emerging strategies with few signals are gated (floor 99)
  until they accumulate `min_trades` trades.
- (-) The default 10 is a judgement call, tuned per deployment via the env
  var without a code change.
