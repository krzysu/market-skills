# 0010. Cross-sectional breadth is not an L2

- **Status**: accepted
- **Date**: 2026-09-23

## Context

The "rally health" question — what share of a defined altcoin basket
outperformed BTC over a rolling N-day window — is **cross-sectional**: it has
no single ticker and cannot be run per-ticker. The L2 contract in this repo
is per-ticker by construction: an L2 skill receives one ticker's candles,
composes L1 indicators, and returns `{pattern, signals, input_scores,
narrative}` so L3 strategies can consume it per idea. Forcing a
cross-sectional read into that shape would mean inventing a synthetic
"ticker" and a per-ticker pattern payload that no L3 could consume honestly.

Two extraction temptations existed alongside the layering one:

- Register the skill in `analysis/registry.py`'s `l2_skills()` so the
  batch runners pick it up.
- Extract the math into a shared `analysis/breadth.py` module.

`analysis/registry.py` is the single source of truth for per-ticker L2/L3
runners; breadth would break the "fetch candles once per ticker, run skills
in-process" contract those runners are built around. On extraction,
[ADR-0003](./0003-no-premature-ranking-module.md) set the precedent: shared
modules are earned by a second concrete consumer, not anticipated. Breadth
currently has no cross-layer consumer at all — no L3 strategy, risk policy,
or batch runner reads it yet ("land the read first").
`skills/market-movers/lib.py` already ships the skill-local-logic precedent
for reads outside the L2 contract.

## Decision

Ship breadth as a standalone skill `skills/market-breadth/` modelled on
`market-macro`:

- Logic lives in `skills/market-breadth/lib.py` (`compute_breadth` pure
  math; `analyze` for watchlist resolution + fetch). No
  `analysis/breadth.py`.
- The skill is deliberately NOT added to `l2_skills()` /
  `l3_strategies()` and NOT added to `analysis/contracts.py`. It does not
  return the L2 shape and is not consumable by L3 composition code.
- The universe (members and benchmark) resolves from `market-watchlist`
  baskets — the read is config-driven, never hardcoded.
- The `regime` bands (`>=60` alt_rotation / `<=40` btc_led) are
  uncalibrated first guesses. The raw `pct_beating` ships in every payload
  and the narrative discloses the uncalibrated bands explicitly —
  consumers should trust the number, not the label.

## Consequences

- (+) The L2 contract stays honest: everything reachable through
  `l2_skills()` is per-ticker and L3-composable.
- (+) The read ships now; a future consumer can promote it once there is
  history to calibrate against.
- (+) Skill-local logic follows an existing precedent instead of inventing
  a new module boundary.
- (-) Skills that later want breadth must go through
  `analysis.skill_loader.load_skill("market-breadth")` instead of a plain
  `analysis.` import.
- (-) Batch runners that iterate `l2_skills()` do not pick breadth up
  automatically; a cross-sectional runner is a separate follow-up if the
  read earns one.
- (-) The regime label is a first guess; consumers must treat it as
  narration, not a gate.
