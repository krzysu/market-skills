# 0003. No premature `analysis/ranking.py` extraction

- **Status**: accepted
- **Date**: 2026-07-06

## Context

`l3-conviction-scan` (rank L3 ideas by conviction) and `bug-scan`
(rank L2 / anomaly findings by severity) both follow the
"flatten envelope → sort by key → cap to top N → render table" pattern.
The temptation on a 2-skill sample is to extract a shared
`analysis/ranking.py` so the third ranking skill is free.

## Decision

No extraction today. Each skill owns its own `extract_*` /
`rank_*` / `render_*` functions. The shared code is too thin
(~20 lines of sort + cap) to justify the abstraction: the row
schema, sort key, column layout, and JSON envelope are all
domain-specific to what the source skill emits.

**Revisit trigger:** a third ranking skill lands **and** the same
`fmt()` / table-renderer primitives are duplicated three or more
times. At that point, extract `analysis/render.py` (table primitives
only — the `fmt()` helper, the "—" nil marker, the column-width
helpers), not a generic "ranker" abstraction.

## Consequences

- (+) Concrete, specific modules. Each ranking skill's behaviour
  is readable end-to-end without following an abstraction across
  files.
- (+) No coupling to today's design. A future ranking skill with a
  different sort key, different columns, or a different envelope
  shape doesn't have to fight a shared interface.
- (+) Matches the repo's "no re-export shims, no legacy aliases"
  stance (`AGENTS.md`). Speculative abstractions drift toward
  shims.
- (-) When the third ranking skill lands, the shared `fmt()` will
  have to be re-extracted from each skill. That's the cost of
  waiting; it's bounded.
