# Architecture Decision Records

This directory holds the dated design decisions behind `market-skills`.
Each record captures one moment of choice: what we were weighing, what
we chose, and the consequences we accepted.

`ARCHITECTURE.md` is the descriptive "what the system is" doc — it links
here for the "why we built it this way" record.

## How to add a new ADR

1. Pick the next number (`ls docs/adr/` to find the highest current).
2. Copy the template at the bottom of this file.
3. Save as `docs/adr/NNNN-kebab-case-title.md`.
4. Add a row to the index below.
5. Reference it from `ARCHITECTURE.md` if it's load-bearing for a section.

ADRs are append-only. To reverse a decision, write a new ADR with
`Status: supersedes NNNN` and link back to the old one.

## Status values

- `proposed` — under discussion, not yet binding.
- `accepted` — current doctrine.
- `superseded` — replaced by a newer ADR (link the successor).
- `deprecated` — no longer applies, kept for history.

## Index

| # | Title | Status | Date |
|---|-------|--------|------|
| [0001](./0001-no-paper-mode.md) | No paper mode for execution | accepted | 2026-06-22 |
| [0002](./0002-llm-as-agent-brain.md) | LLM is the agent brain | accepted | 2026-06-22 |
| [0003](./0003-no-premature-ranking-module.md) | No premature `analysis/ranking.py` extraction | accepted | 2026-07-06 |

## Template

```markdown
# NNNN. Short title

- **Status**: proposed | accepted | superseded | deprecated
- **Date**: YYYY-MM-DD

## Context

What was the situation? What were we weighing?

## Decision

What did we choose?

## Consequences

- (+) positive
- (-) negative
```
