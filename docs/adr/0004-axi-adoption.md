# 0004. Adopt AXI (Agent eXperience Interface) output conventions

- **Status**: accepted
- **Date**: 2026-07-07

## Context

`market-skills` is consumed by an LLM agent brain (per
[ADR-0002](./0002-llm-as-agent-brain.md)). Every skill's `scripts/run.py`
emits `--json` for the LLM to parse. Today the output shape is accidental:
each skill hand-rolls its own dict, fields are unpinned, content is not
truncated, empty states are bare `[]`, and the no-arg mode is a usage
error instead of a dashboard. This costs tokens and turns on every
multi-step agent task.

[AXI](https://github.com/kunchenguid/axi) (Kun Chen, 2026) is a published
set of 10 design principles for agent-ergonomic CLIs, validated across
915 runs and two domains (browser automation, GitHub). The headline
result: principled CLI design beats both raw CLI and MCP on every
metric — success, cost, duration, turns. The 10 principles: token-
efficient output, minimal default schemas, content truncation, pre-
computed aggregates, definitive empty states, structured errors & exit
codes, ambient context, content first (no-args shows live state),
contextual disclosure, consistent help.

The repo is closer to AXI's spirit than most CLI repos (the Agent
Skills spec, `--json` for machine output, deterministic lib + LLM
narration, the `LLM-ORCHESTRATION.md` failure-mode contract), but the
alignment is accidental, not principled. None of the 10 rules is
enforced anywhere. Closing the gap has a concrete payoff: lower
token cost per agent turn, fewer shell-pipe round-trips, and
discoverability for the next LLM that picks up the skills without
reading every `SKILL.md` first.

## Decision

Adopt AXI as the on-the-wire output contract for every skill in the
registry. Concretely:

1. **New module `analysis/output.py`** owns the AXI primitives:
   `envelope()`, `project_fields()`, `truncate()`, `toon_dump()`,
   `emit_envelope_json()`, `render_home_view()`. The existing
   `analysis/formatting.py` (`emit_json`, `parse_args`, `print_header`,
   `require_ticker`, `safe_round`, `render_notes`) stays as-is and is
   not wrapped or aliased.
2. **New TypedDict `AXIEnvelope`** in `analysis/contracts.py` pins the
   on-the-wire shape: `{data, count, errors, help[]}`. Field
   semantics: `data` is the skill-specific payload; `count` is the
   canonical item count (skill-defined — single-item skills use 1);
   `errors` is a list of structured error strings (replaces
   bare `{"error": "..."}`); `help` is a list of next-step command
   templates the LLM can drop into narration.
3. **`lib.py` contracts are unchanged.** The in-process TypedDicts
   (`L1Result`, `L2Result`, `L3Result`, `L3Idea`, `RegimeSignal`,
   `RiskVerdict`, `FillConfirmation`, `Intent`) stay as-is because
   L2→L3 composition depends on them. AXI applies at the
   `scripts/run.py` envelope layer only.
4. **No-arg mode is a home view**, not a usage error. Each skill
   reads its last-cached state from
   `$XDG_DATA_HOME/market-skills/<skill>_last.json` (or the
   appropriate domain store) and renders a 1-page dashboard with
   "try this next" lines. This is AXI principle 8 (content first).
5. **TOON ships as opt-in** behind `--toon` (off by default). The
   `toon_dump()` helper is a JSON shim today so consumers keep
   working. Flipping the default to TOON is a follow-up ADR gated
   on a measured >30% token saving across the pilot.
6. **Pilot-then-sweep rollout.** Phase 1 migrates 4 representative
   call sites (`market-rsi`, `market-trend-quality`,
   `strategy-trend-follow`, `run-all-l3`) with a smoke-test fixture
   that gates phase 2. Phase 2 sweeps the remaining L1/L2/L3
   skills. Risk, execution, portfolio, and watchdog are out of
   scope for the envelope rewrite — their tests pin specific
   shapes (`test_risk_engine.py`, `test_execution_kraken_*.py`).
7. **One new doc: `docs/AXI-REFERENCE.md`** documents the 10
   principles, the per-layer envelope examples, the shell-
   composability patterns, and the home-view contract.
   `LLM-ORCHESTRATION.md` (failure-mode contract) stays unchanged —
   the two docs serve two audiences (output conventions vs.
   per-status workflow).
8. **No shims, no legacy aliases, no re-exports** (per `AGENTS.md`).
   When a script's output shape changes, all callers + tests update
   in the same commit.

## Consequences

- (+) Lower token cost per agent turn. The envelope is ~30% smaller
  than the current verbose-indent JSON for typical L1/L2 payloads
  (projection + truncation cuts the long-tail fields). The opt-in
  TOON path targets another 40% on top of that.
- (+) Discoverability for the next LLM. The no-arg home view +
  `help[]` lines in every output mean the LLM doesn't have to read
  every `SKILL.md` to know what to do next.
- (+) Composability. Shell pipes (`| jq`, `| grep`, `| head`) work
  on the new envelope because the canonical fields are
  consistently keyed.
- (+) Testable. `tests/test_axi_envelope.py` asserts the
  envelope shape per skill, so future drift trips a test instead
  of a user.
- (-) Migration cost. ~22 `scripts/run.py` files change shape in
  phase 2; each change ships with a matching test update per the
  "per-fix test fixtures" rule.
- (-) No-arg behavior change is a breaking change for any pipeline
  that today runs `run.py` (no args) and parses the usage error.
  Mitigation: documented in `AGENTS.md` and `docs/AXI-REFERENCE.md`.
- (-) TOON flip is deferred. If agent token budgets matter today
  more than stability, this is the wrong place to stop. Mitigated
  by the opt-in `--toon` flag from day one.

## Revisit triggers

- **TOON default flip** — a measured `mean(tokens_toon) <
  0.7 * mean(tokens_json)` across the phase-1 pilot, plus a
  follow-up ADR (`0005-...`) signed off. Until then, JSON is the
  default.
- **Risk / execution / portfolio envelope rewrite** — when the
  `risk-engine` and `execution-kraken-*` test surface stabilises
  enough to absorb a shape change without rewriting every fixture.
  Until then, those skills are out of scope.
- **Per-skill home view deprecation** — if the LLM consistently
  ignores the home view in favor of `SKILL.md`, the per-skill
  state cache is overkill. The contract is the contract; the
  storage detail can be revisited.
