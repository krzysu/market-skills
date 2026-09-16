# 0005. Measure the whole L3 registry (minus declared unmeasurable strategies)

- **Status**: accepted
- **Date**: 2026-09-16

## Context

The nightly `backtest-pipeline` selected the strategies it would measure with
a positional slice: `analysis.registry.l3_strategies()` — top 3 are
"primary" (full coverage), remainder limited to 3 "secondary". With seven
strategies in the registry, that cap silently dropped
`strategy-liquidity-sweep` — last in registry order — so it got no fitness
data in `fitness_matrix.json`, no conviction floor in
`conviction_thresholds_private.json`, and no coverage in the regime health
brief. Meanwhile `strategy-funding-carry` burned one of those scarce
secondary slots: the backtest engine feeds spot price bars and
`fetch_funding_rate` has no perp funding data, so every funding-carry pair
errored and consumed a slot while producing no measurement at all.

## Decision

The measured set is the **whole L3 registry minus an explicitly declared
`UNMEASURABLE_STRATEGIES` map** — a dict of strategy name → reason, so
every exclusion carries its justification in code. Positional slicing
(`[:3]`) is rejected: it makes membership depend on registry ordering, so
appending a new strategy displaces an existing one, and the displacement is
silent. `measured_strategies()` returns exactly
`[s for s in l3_strategies() if s not in UNMEASURABLE_STRATEGIES]`, and a
test asserts the measured set equals the registry minus the declared
exclusions.

- **`strategy-liquidity-sweep`: YES, it is measured.** It gets fitness data
  in `fitness_matrix.json` and a conviction floor in
  `conviction_thresholds_private.json` like every other measurable
  strategy. Sweep-and-reclaim is the user's preferred entry pattern, and an
  unmeasured strategy can never be gated — with no floor on record, DTP
  falls through to the global minimum and emits its ideas unvetted.
- **`strategy-funding-carry`: excluded**, with the reason recorded in the
  map: the backtest engine feeds spot price bars and `fetch_funding_rate`
  has no perp funding data,   so every funding-carry pair errored and consumed
  a slot while producing no measurement.

## Consequences

- (+) No silent displacement when the registry grows: a new entry joins the
  measured set; nobody loses coverage.
- (+) Liquidity-sweep gets a conviction floor and fitness data — it is now
  gated like every other strategy instead of emitting unvetted.
- (-) The pipeline still iterates the same six strategy grids as before:
  funding-carry's slot is reallocated to `strategy-liquidity-sweep`, so the
  per-ticker per-interval pair count is unchanged. The only runtime delta is
  that the previously all-erroring funding-carry pairs no longer run — the
  nightly run is at worst unchanged, not longer.
- (-) A strategy that becomes measurable again (e.g. the engine gains perp
  funding bars) must be removed from the exclusion map; the map is not
  self-updating, and a stale entry would hide a measurable strategy from
  the nightly backtest.
