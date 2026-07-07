# AXI Reference

> Companion to [ADR-0004](./adr/0004-axi-adoption.md). Defines the
> on-the-wire output contract every `--json` mode emits, the
> shell-composability patterns the contract enables, and the
> no-arg home view per skill.

This document is the **LLM-facing output reference**. Read it once
at session start. The failure-mode workflow for `RiskVerdict` and
`FillConfirmation` lives in [`LLM-ORCHESTRATION.md`](./LLM-ORCHESTRATION.md)
— a separate doc, separate audience.

## 1. The 10 AXI principles applied here

| # | Principle | Where it lives |
|---|-----------|----------------|
| 1 | Token-efficient output (TOON) | `analysis.output.toon_dump()` — JSON shim today, TOON path in phase 5 behind `--toon` |
| 2 | Minimal default schemas | `envelope(..., fields=...)` and the per-skill `--fields=` flag (lands in phase 1+) |
| 3 | Content truncation | `analysis.output.truncate()` — narrative / thesis strings capped at 80 chars with size hint |
| 4 | Pre-computed aggregates | `envelope(..., count=N)` — the canonical item count sits next to the data, not in it |
| 5 | Definitive empty states | `analysis.output.empty_state()` — `{data: null, count: 0, errors: [], help: []}` |
| 6 | Structured errors & exit codes | `envelope(..., errors=[...])`; exit codes unchanged (0 success, 1 fatal, 2 input) |
| 7 | Ambient context | `analysis.output.render_home_view()` — no-arg mode shows last-cached state + next-step hint |
| 8 | Content first | Same as #7 — no-arg mode is the home view, not a usage error |
| 9 | Contextual disclosure | `envelope(..., help=[...])` — `help[]` lines appended to every output |
| 10 | Consistent `--help` | Lands in phase 3; until then the per-skill help format is heterogeneous |

## 2. The envelope

Every `--json` call returns a TypedDict of shape:

```json
{
  "data":   <skill-specific payload>,
  "count":  3,
  "errors": [],
  "help":   ["Run `strategy-trend-follow HYPEUSD --json` to see the L3 idea",
             "Run `run-all-l3 HYPEUSD SOLUSD --json` for the batch view"]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `data` | skill-defined | yes | The skill's payload — singleton dict, list, or `null` for empty |
| `count` | int \| null | yes | Canonical item count. Singleton skills use `1`; lists use `len(data)`; null when count is undefined (e.g. macro regime) |
| `errors` | list[str] | yes | Always a list. Empty when no errors. Replaces the bare `{"error": "..."}` pattern |
| `help` | list[str] | yes | Always a list. Next-step command templates. Empty when no next step |

The TypedDict is `analysis.contracts.AXIEnvelope`; the constructor is
`analysis.output.envelope(data, *, count, help, errors, fields)`.

## 3. Field projection (`--fields=`)

L1/L2/L3 skills accept a `--fields=` flag (phase 1+) that maps to
`project_fields(d, fields)`:

```bash
# Default 3-4 fields
uv run skills/market-rsi/scripts/run.py AAPL --json
# {"data": {"ticker": "AAPL", "rsi_14": 42.0, "signal": "NEUTRAL"}, "count": 1, ...}

# Projection
uv run skills/market-rsi/scripts/run.py AAPL --json --fields=rsi_14,signal
# {"data": {"rsi_14": 42.0, "signal": "NEUTRAL"}, "count": 1, ...}
```

Accepted `--fields=` shapes:

- `a,b,c` — comma-separated string
- `--fields=all` (or absent) — full payload
- `--fields=` (empty) — full payload

Unknown fields are silently dropped. The `data` key always projects;
`count`, `errors`, `help` are never filtered.

## 4. Content truncation

`truncate(text, limit=80, hint=True)` caps narrative / thesis strings
at 80 chars (configurable per call) and appends a hint:

```
"HEALTHY_UPTREND with pullback (truncated, 2847 chars total - use --full to see complete body)"
```

Pass `--full` to disable truncation (or `truncate(..., limit=None)`).

## 5. Shell composability

The envelope enables the standard AXI shell patterns:

```bash
# Pipe through jq (count + data is the canonical shape)
uv run skills/run-all-l3/scripts/run.py HYPEUSD SOLUSD --json \
    | jq '.tickers[].strategies[].data.ideas[].pair'

# grep-style filter via jq
uv run skills/run-all-l3/scripts/run.py HYPEUSD SOLUSD --json \
    | jq '.data | map(select(.conviction >= 4))'

# Head N items
uv run skills/run-all-l3/scripts/run.py HYPEUSD SOLUSD --json --top=3 --fields=pair,conviction
```

The flat top-level `count` lets the agent ask "how many ideas did
this skill produce?" without re-counting the array.

## 6. Empty state

A skill that finds nothing returns:

```json
{"data": null, "count": 0, "errors": [], "help": ["Run `market-rsi AAPL --json` to try a different ticker"]}
```

`empty_state(help=..., errors=...)` is the constructor. Bare
empty lists / dicts are forbidden on the wire.

## 7. Home view (no-arg mode)

Running any skill with no args prints the **home view** instead of
a usage error. The view reads `$XDG_DATA_HOME/market-skills/<skill>_last.json`
(written by the skill on each successful run) and renders a
one-line summary plus a `try:` hint:

```
$ uv run skills/market-rsi/scripts/run.py
last cached: AAPL rsi=42 NEUTRAL on 2026-07-07T14:30:00Z (3h ago)
  try: `market-rsi AAPL --json`
```

Fresh-install fallback:

```
$ uv run skills/market-rsi/scripts/run.py
no cached state yet - run `market-rsi --json` to populate this view,
or pass `--help` to see usage.
```

`render_home_view(skill_name, *, command_hint=None)` is the
constructor. Storage is best-effort — a read failure falls back to
the hint message; a write failure is silent.

`maybe_render_home_view(script_file, ticker, json_mode)` is the
main() entry point. Returns `True` when the home view was emitted
(so the caller should `return`); returns `False` when a ticker was
given (caller proceeds with the normal analyze path). The
`script_file` arg is `__file__` — the helper derives the skill
name from the path.

`cache_run_result(script_file, result)` writes the per-skill state.
It adds a `cached_at` ISO timestamp and skips silently when
`result` is `None` or contains an `"error"` key (errors are not
state).

## 7b. Session-start dashboard (market-state)

[`market-state`](../../skills/market-state/SKILL.md) is a meta-skill
that reads the per-skill caches and composes a single dashboard
intended for the LLM's first call at session start. It composes 6
sources: `market-macro`, `market-valuation`, `market-movers`,
`run-watchlist`, `l3-conviction-scan`, `market-notes`. Each source
contributes a slim view (a `summary` one-liner plus a handful of
headline fields) and a `cached_at` ISO timestamp; a top-level
`freshness` map reports the age of each source so the LLM can
decide which to refresh before acting. No I/O at runtime — every
field is a read from a JSON cache.

## 7c. TOON encoder (`--toon`)

Every `--json` call accepts an opt-in `--toon` flag that switches the
on-the-wire format from indent-2 JSON to **TOON** (Token-Oriented
Object Notation). TOON is a compact, YAML-flavoured encoding designed
for LLM consumption:

```bash
$ market-state --json --full              # 1675 bytes
$ market-state --toon --json --full       # 1172 bytes  (30% smaller)
```

The encoder/decoder live in `analysis.output.toon_dump` /
`analysis.output.toon_load` — hand-rolled, no external dep. Round-trip
is pinned by `tests/test_toon.py`. Measured byte savings on
representative AXI envelopes (JSON vs TOON):

- 22-28% smaller for envelopes with primitive-only payloads
  (e.g. `market-macro`, `market-valuation`).
- 35-55% smaller for envelopes with lists of uniform objects
  (e.g. `strategy-trend-follow` ideas, `l3-conviction-scan` rows) —
  the tabular CSV-row format collapses repeated keys.

Off by default per the AXI ADR. The `parse_axi_flags` helper now
returns `(fields, full, toon, filtered_argv)` so every migrated
script threads the flag through `emit_envelope_json(..., toon=toon)`
with no per-skill branching.

## 8. Errors

Errors flow through the `errors` list, not as a `data` field:

```json
{"data": null, "count": 0,
 "errors": ["insufficient data (need 30+ candles, got 12)"],
 "help": ["Try a longer --period", "Default --period=1y gives ~250 daily bars"]}
```

Exit codes are unchanged: `0` success, `1` fatal (bad input that
prevents the skill from running at all), `2` input validation
(unknown flag, missing ticker). A skill that ran and found nothing
exits `0` with `count: 0` — "no results" is not a failure.

## 9. Migration status

| Layer | Status | Notes |
|-------|--------|-------|
| `analysis.output` (envelope, project_fields, truncate, toon_dump, home view) | **shipped (phase 0)** | This document is the contract. |
| L1 pilots (`market-rsi`) | phase 1 | First per-skill migration |
| L2 pilots (`market-trend-quality`) | phase 1 | Narrative truncation live |
| L3 pilots (`strategy-trend-follow`) | phase 1 | `count: N` over `ideas[]` |
| Batch pilot (`run-all-l3`) | phase 1 | Top-level envelope + `--top=N --fields=` |
| Sweep (remaining L1/L2/L3/specialized) | phase 2 | Per-skill, gated on pilot exit criteria |
| Home view (no-arg mode) | **shipped (phase 3)** | `maybe_render_home_view` + `cache_run_result` in 27 skills |
| Session-start dashboard | **shipped (phase 4)** | `market-state` skill (SKILL.md + lib.py + scripts/run.py + tests) — meta-skill pattern, not a single script |
| TOON encoder (opt-in `--toon`) | **shipped (phase 5)** | Hand-rolled `toon_dump()` + `toon_load()` in `analysis.output`; 30-50% smaller on representative envelopes |

## 10. What this doc is NOT

- **Not the failure-mode contract** — see `LLM-ORCHESTRATION.md` for
  per-`RiskVerdict`-status and per-`FillConfirmation`-status workflows.
- **Not the lib.py contract** — the in-process TypedDicts
  (`L1Result`, `L2Result`, `L3Result`, `RiskVerdict`, `FillConfirmation`)
  are unchanged. This doc covers the on-the-wire envelope.
- **Not a backwards-compat promise** — when a script's output shape
  changes, all callers + tests update in the same commit. There are
  no shims, no legacy aliases, no re-exports.
