# Market Skills — Agent Guide

## Setup & verification

```bash
uv sync                          # install deps + dev
uv run ruff check                # lint (py312, line-length 120, rules E/F/I/N/W/UP)
uv run ruff format               # auto-format (double quotes)
uv run pytest tests/             # all tests
uv run pytest tests/test_X.py -v # single file
```

## Output conventions (AXI envelope)

Every skill's `--json` mode emits the canonical AXI envelope
([ADR-0004](./docs/adr/0004-axi-adoption.md),
[`docs/AXI-REFERENCE.md`](./docs/AXI-REFERENCE.md)):

```json
{"data": <payload>, "count": N, "errors": [], "help": ["..."]}
```

| Helper                                           | Source            | Use                          |
| ------------------------------------------------ | ----------------- | ---------------------------- |
| `envelope(data, *, count, help, errors, fields)` | `analysis.output` | Construct the envelope       |
| `emit_envelope_json(data, ...)`                  | `analysis.output` | Print the envelope to stdout |
| `project_fields(d, fields)`                      | `analysis.output` | `--fields=` projection       |
| `truncate(text, limit, hint)`                    | `analysis.output` | Narrative / thesis capping   |
| `empty_state(help, errors)`                      | `analysis.output` | AXI principle 5 zero result  |
| `render_home_view(skill_name)`                   | `analysis.output` | No-arg mode dashboard        |
| `write_state_cache(skill_name, payload)`         | `analysis.output` | Home-view state store        |

The lib.py contracts (`L1Result`, `L2Result`, `L3Result`, `L3Idea`,
`RegimeSignal`, `RiskVerdict`, `FillConfirmation`, `Intent`) are
unchanged — the envelope wraps them at the `scripts/run.py` boundary.
TOON ships as opt-in behind `--toon`; the default is indent-2 JSON.

## Architecture

- **`docs/adr/` holds dated architecture decision records.** Read the
  index at `docs/adr/README.md` before proposing new abstractions,
  refactors, or new shared modules — past "we considered X, chose not
  to" decisions live there. When you make a new design decision, add a
  new ADR with the next number (`0004-...`), link it from the index,
  and reference it from `ARCHITECTURE.md` if it's load-bearing. ADRs
  are append-only; to reverse one, write a new ADR with
  `Status: supersedes NNNN`. `ARCHITECTURE.md` is the descriptive
  "what the system is" doc; ADRs are the "why we built it this way"
  record.
- **L1/L2/L3 skills.** L1 = pure-math indicators (`skills/market-*/lib.py`, no I/O). L2 = pattern detectors that compose L1s and return `{pattern, signals, input_scores, narrative}`. L3 = strategies that compose L2s and return `{ideas, narrative}` — each idea carries `version: "v1".."v5"` via `conviction_version()` and is validated by `validate_l3_tp_ladder()` before return. Read each skill's `SKILL.md` for CLI flags and `When to use / NOT to use` boundaries.
- **Batch runners** (`run-all-l2`, `run-all-l3`, `run-watchlist`): fetch candles once per ticker, then run all skills in-process.
- **`analysis/registry.py`** — single source of truth for L2/L3 skill lists (`l2_skills()`, `l3_strategies()`). New skills go here once and all runners pick them up.
- **`analysis/skill_loader.py`** — `load_skill(name)` for cross-skill loads; `load_lib_for_script(__file__)` is what every `scripts/run.py` uses for its own `lib.py`. Never reimplement the `importlib.util.spec_from_file_location` dance inline.
- **`analysis/contracts.py`** — TypedDict return shapes plus sanity helpers `l2_fired()`, `l2_classification()`, `validate_l3_tp_ladder()`, `conviction_version()`. `l2_fired()` / `l2_classification()` are the single read site for "did the L2 actually fire?" — never inline `pattern.get("classification")`.
- **`analysis/risk/`** — advisory `vet(intent, ctx, *, policies=None)`. `is_perps_intent(intent)` + `select_policies(intent, ...)` pick the right set (spot: position size / drawdown / per-tier / daily budget / insufficient funds / per-pair cooldown; perps: leverage cap / liquidation distance / stop distance / funding drag / duplicate position). Pure function. Worst-case aggregation; SCALE picks min suggested_volume.
- **`analysis/providers/data/`** — `Provider` Protocol + `hl:` / `yf:` / `kraken:` / CCXT adapters. Registry on `analysis/data.py`. Auto-detect order is pinned by `tests/test_data.py::TestProviderRegistryOrder`.
- **`analysis/providers/execution/`** — `ExecutionProvider` Protocol + `Intent` / `FillConfirmation` TypedDicts + Kraken spot/perps adapters. The `Intent` / `FillConfirmation` contracts are shared by Risk and Execution.
- **`analysis/macro/`** vs **`analysis/chop.py`** — Macro fetches external cross-asset state (F&G, VIX, DXY, US10Y, BTC.D, total mcap) into a `RegimeSignal`. Chop reads the rolling L3 idea history into a `chop_score` (conviction-calibration indicator). Different signals that share a name prefix in the L3 envelope.
- **`analysis/` and `portfolio/` are installable packages** (`pyproject.toml` → `packages.find = {include = ["analysis*", "portfolio*"]}`). `uv sync` makes `from analysis.X import Y` work everywhere — no `sys.path` hacks.
- **Per-user data** (`market-watchlist`, `market-notes`, `position-watchdog`, `portfolio-mgmt`) lives under `skills/<name>/data/` (gitignored); samples ship under `skills/<name>/examples/`. CLI flag wins over env var, env var wins over default.

## LLM is the agent brain

This repo does NOT own a Python orchestrator that auto-pipes signals to execution. The LLM agent reads `SKILL.md`, calls skills as tools, narrates, asks the user to confirm, and (with explicit approval) calls `execution-kraken-spot` or `execution-kraken-perps`. Cron is analytics-only (`run-all-l3`, `position-watchdog`).

**Safety invariant**: `execution-kraken-spot submit` / `execution-kraken-perps submit` always prompts for confirmation unless `--yes` is passed. That prompt is the safety layer — never bypassed silently. The LLM owns `intent_id` uniqueness within a session (`--cl-ord-id` plumbs the id to the venue for retry dedup).

> **Failure-mode contract**: before narrating a `RiskVerdict`, handling a `FillConfirmation`, recording a partial fill, or generating an `intent_id`, consult [`LLM-ORCHESTRATION.md`](./LLM-ORCHESTRATION.md) for the per-status workflow and the things-you-must-NEVER list.

## Conventions

- All `scripts/run.py` accept `--json` for machine output, require a ticker as first positional argument, and `--source=<provider>`.
- Ruff: `skills/*/scripts/run.py` only ignore E501 (long display f-strings). No E402 — `analysis.skill_loader.load_lib_for_script(__file__)` replaced the `sys.path.insert` dance.
- Provider notation: `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`, `kraken:BTCUSD`).
- Never use `l` as a variable name — ambiguous with `1`, triggers E741.
- **No backward compatibility.** When you change a public name, signature, or path, update every caller in the same commit. Do not add re-export shims, deprecation aliases, or `*_legacy` modules. The repo is consumed by an LLM that reads fresh `SKILL.md` on every call — there is no installed-base to protect.
- Commit messages: single line, semantic prefix (`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:` / `perf:` / `style:` / `build:` / `ci:`). Format: `<prefix>: <imperative summary, lowercase after the prefix>`. Good: `fix: use live Kraken ticker instead of stale daily OHLC close`. Bad: `Fix: address HYPE price issue`, `fix(portfolio): ...\n\nLong body...`. Enforced by a global `commit-msg` template hook (~/.git-templates/hooks/commit-msg via `init.templateDir`).
- **Bundle post-hoc fixups into the original commit.** A `docs:` or `fix:` patch that lands immediately after a `feat:` commit and only touches what that commit introduced belongs in the original commit, not as a follow-up. Hold the follow-up only when (a) it's independent of the original or (b) there's enough material for a real standalone change. Avoid the "feat + immediate docs fixup" two-step.

### Per-fix test fixtures are required

Every `fix:` commit must include a test case in `tests/test_<area>.py` that reproduces the exact shape that triggered the bug. The test must fail on the pre-fix code and pass on the post-fix code. A `fix:` commit without a fixture is incomplete — follow up with a `test:` commit that adds the missing one.

## What to avoid

- No hardcoded paths to private repos.
- Don't create `__pycache__/`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`, `*.egg-info/`, `dist/` — already in `.gitignore`.
- **No references to local filesystem structures in library code.** Comments, docstrings, and `SKILL.md` files must only describe code that could ship to a fresh open-source consumer. Never mention host-specific paths (absolute paths under any user's home, profile-specific dirs, personal config files, paths to any particular machine's filesystem) in library code, docs, commit messages, or TODOs. Document only the env var / API contract, not the resolved path on any particular machine. \*\*When the env var is unset, raise — don't fall back to a host-specific default.
- **Never commit SPEC/ or BUGS files.** Planning artifacts (SPECS/\*.md, BUGS, BUGS.md) are session-only and must never be included in a commit. Keep them untracked or add to .gitignore if needed.\*\*

<!-- BEGIN BEADS INTEGRATION v:1 profile:full hash:19cc25d9 -->

## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Dolt-powered version control with native sync
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Quality

- Use `--acceptance` and `--design` fields when creating issues
- Use `--validate` to check description completeness

### Lifecycle

- `bd defer <id>` / `bd supersede <id>` for issue management
- `bd stale` / `bd orphans` / `bd lint` for hygiene
- `bd human <id>` to flag for human decisions
- `bd formula list` / `bd mol pour <name>` for structured workflows

### Sync

bd stores issue history in Dolt:

- Each write auto-commits to Dolt history
- Use `bd dolt push`/`bd dolt pull` for remote sync
- Do not treat `.beads/issues.jsonl` as the sync protocol

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:

   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```

5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**

- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.

<!-- END BEADS INTEGRATION -->
