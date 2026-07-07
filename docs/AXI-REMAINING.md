# AXI rollout — remaining work

This document tracks the gaps between the current state of the
market-skills repository and the end-state vision from
[ADR-0004](./adr/0004-axi-adoption.md) (the AXI envelope) and
[AXI-REFERENCE](./AXI-REFERENCE.md) (the contract).

Phases 0-5 are shipped (envelope helpers, pilot, sweep, home view,
session-start dashboard, TOON encoder). The items below are the
remaining work.

## Out-of-scope skills (require new ADR)

These four scripts deliberately stayed on their pre-AXI emit_json path
during phases 1-2 because migrating them changes the on-the-wire
contract for downstream consumers that already pin specific shapes
(`RiskVerdict`, `Intent`, `FillConfirmation`, portfolio watch state).
Each needs its own ADR before envelope migration.

| Skill | Status | Why it's blocked |
|-------|--------|------------------|
| `skills/risk-engine/` | envelope **not** applied (uses raw `emit_json`) | Returns `RiskVerdict`; tests pin the current shape via direct `json.loads`. The existing vettings tests (`tests/test_risk_engine*`) call `run.py` and assert specific verdict fields — migrating changes those keys. Needs an ADR that decides: does the envelope wrap `RiskVerdict` (new `data.risk_verdict` shape) or replace it (breaking)? |
| `skills/execution-kraken-spot/` | envelope **not** applied | Returns `FillConfirmation` and `Intent`; same test-pinning problem as risk-engine. Requires the same wrapping-vs-breaking decision before migration. |
| `skills/execution-kraken-perps/` | envelope **not** applied | Same as spot. Identical contract surface. |
| `skills/portfolio-mgmt/` | envelope **not** applied | Per-user data + watch state; the StateCache write/read pair (`portfolio_mgmt/state.py`) is on the same `state.json` internal path as `market-skills/*_last.json`. Migrating requires designing how `RiskVerdict`/`Intent`/`FillConfirmation` interop with AXI envelopes from sister skills. |

Common work for all four: design a wrapping contract (`data: {risk_verdict: ...}` or `data: {intents: [...]}`) and update the test fixtures that pin shapes. **Estimated: 1 ADR + 4 mechanical migrations + test updates.**

## Per-user data skills (partial migration)

| Skill | Migrated | Missing |
|-------|----------|---------|
| `skills/market-watchlist/scripts/run.py` | `list` subcommand | `show`, `validate`, `tickers`, `resolve`, `add`, `remove` subcommands still emit raw JSON. The 7 subcommands use argparse with positional `--watchlist` and have their own per-subcommand result shape. |
| `skills/market-notes/scripts/run.py` | `list` subcommand (+ cache write) | `add`, `remove`, `prune`, `migrate`, `validate` subcommands still emit raw JSON. These are write/admin paths — they don't need home view, just envelope wrapping for consistency. |
| `skills/position-watchdog/formatter.py` | not run through envelope | Output formatting helper used by cron job + alert path. Should emit envelope so the watch state passed to execution paths is uniform. |

**Estimated: ~6 hours mechanical work, no ADR needed.**

## Coverage gaps in already-shipped skills

### `parse_axi_flags` doesn't recognise every flag

`parse_axi_flags` recognises `--full`, `--fields=<csv>`, `--toon`.
Skills with extra flags (`--from-state`, `--from-json` in `bug-scan`;
`--narrative`, `--top` in `l3-conviction-scan`; `--no-cache`, `--ttl` in
`market-macro` / `market-valuation`; etc.) use `_parse_argv` helpers
or `argparse` directly and re-implement their own handling. The
helper could grow to know about these or the migration could move
towards a single unified parser. Low priority — current approach
works — but the inconsistency shows up when grep'ing for AXI flag
handling.

### `--help` format is heterogeneous

ADR-0004 principle 10 ("consistent `--help`") was deferred to "phase
3" but never landed. Each skill emits its own usage line on
`--help`:

- `safe_parse_args` in `analysis/formatting.py` prints the standard
  `usage: run.py TICKER [--json] [--source=...] ...` line.
- `run-all-l2`, `run-all-l3`, `l3-conviction-scan` print hand-rolled
  variants via `_parse_argv` branches.
- `market-watchlist` and `market-notes` use argparse and inherit its
  `--help` output (longer, includes subcommands).

The LLM consumer pattern is `--help` for unknown flag → usage text → recovery. Heterogeneity breaks that. **Fix: extend `safe_parse_args` to handle `--help` for ticker-required skills (already does), document the heterogeneous patterns for argparse-subcommand skills, or unify via `python -m market_skills.cli <skill> ...`. Low priority.**

## Perps funding (originally flagged during phase 4 audit)

`market-state` composes 6 cached sources but **does not include a
perps-funding panel**. `market-basis` caches a per-ticker snapshot,
not a market-wide funding view, so it can't serve as the dashboard
source without per-ticker iteration.

The intended fix is a new `skills/market-funding/` skill that:

1. Scans the HL perp universe for outlier funding rates (positive or
   negative) once per session.
2. Caches the result as `market-funding_last.json`.
3. Adds itself as a 7th source to the `market-state` composition.

This is "phase 4.1" — a follow-up added to the dashboard, not a fix
to the phase 4 commit.

**Estimated: 1 day (new skill + cache + dashboard source slot + tests).**

## TOON default-flip decision (open ADR)

ADR-0004's gate was "measured >30% token saving." Phase 5 measured:

| Payload shape | JSON | TOON | Saved |
|---------------|------|------|-------|
| `market-state --json --full` (typical dashboard) | 1675 B | 1172 B | **30%** |
| 30 L3 ideas nested under `data.ideas` | 10907 B | 2382 B | **78%** |
| 30 L3 ideas at top level (`toon_dump(list)` directly) | 9467 B | 7608 B | **20%** (no tabular compression) |

The dashboard sits at the gate boundary; L3 tabular payloads blow
past it; top-level-list-of-dicts gets no compression because the
encoder only emits tabular when the list is a value of a dict key.

The two paths:

1. **Drop the gate to ~20%**: accept that the savings are uneven and
   flip `--toon` to default after a soak period. Pro: uniform
   contract. Con: changes the wire format for every consumer.
2. **Carve per-skill defaults**: `l3-conviction-scan`,
   `strategy-trend-follow`, `run-all-l3` flip to TOON-by-default
   (clearly above the bar); `market-state` and `market-macro` /
   `market-valuation` stay JSON-by-default (at the boundary).
   Pro: tracks the real-world distribution. Con: per-skill default
   decisions scattered across `scripts/run.py` instead of one rule.

**Needs: 1 ADR (2-3 paragraphs), no code change.** The ADR is small
and the decision is mostly a recording of what was measured.

## Consumer-side work (low risk, medium effort)

Right now `analysis.output.toon_load` ships. Consumers that need
TOON parsing can call it directly. The LLM agent brain doesn't need
to know — it reads the on-the-wire bytes and reasons over them. The
only consumers that need to parse are:

- Any local script that reads `--toon --json` output programmatically
  (e.g. a future cron job that ingests conviction-scan rows).
- The `market-state` self-cache: currently writes JSON, reads JSON;
  no change needed for v1.

**Estimated: ~1 hour per consumer, gated on need.**

## What is NOT in scope

These were considered and explicitly deferred:

- **TOON real encoder library** (`python-toon`) — multiple open bugs
  (round-trip fails for strings with structural chars; see
  [#47](https://github.com/toon-format/toon-python/issues/47),
  [#58](https://github.com/toon-format/toon-python/issues/58),
  [#61-64]). The hand-rolled encoder in `analysis.output.toon_dump`
  is the production path. Revisit when the library hits v1.
- **Backward compatibility shims** for the envelope — explicitly
  forbidden by AGENTS.md ("No backward compatibility. When you change
  a public name, signature, or path, update every caller in the same
  commit.").
- **Cross-skill cache invalidation** — the freshness map in
  `market-state` reports staleness but doesn't trigger refresh. Out
  of scope per the AXI ADR; refreshing is a runtime/orchestrator
  concern that the LLM agent handles.

## Phasing suggestion

If this backlog gets actioned, the suggested order is:

1. **TOON default-flip ADR** (no code) — 30 min.
2. **`market-funding` skill** (phase 4.1) — 1 day.
3. **Per-user-data subcommand envelope wrapping** (mechanical) —
   6 hours.
4. **Risk/execution ADR + migrations** — 1 ADR + 1 day mechanical.
5. **`--help` unification** — 1 day, possibly a new helper.

Steps 1-3 are unblocked and ship-ready. Steps 4-5 depend on
downstream-consumer sign-off.
