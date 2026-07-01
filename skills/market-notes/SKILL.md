---
name: market-notes
description: "Per-pair thesis notes with timestamps and optional expiration. Shared context for any market skill to surface alongside its verdict (cycle theses, watch levels, planned setups). Stores notes as JSON, one file keyed by ticker."
version: 0.1.0
metadata:
  hermes:
    tags: [notes, thesis, context, storage]
    category: utility
compatibility: "Requires Python 3.12+ and uv"
---

# market-notes

Per-pair free-form thesis notes with timestamps and optional expiration. The simplest possible context layer: a JSON file keyed by ticker, plus a CLI to manage it. Other skills (`run-all-l2`, `run-all-l3`, `run-watchlist`, `position-watchdog`) can pull active notes alongside their verdict so the agent brain sees "L3 says LONG conv 4 *and* there's a cycle-bottom-wait note for BTC" together.

Notes are personal context — the data file is **gitignored** and lives at `skills/market-notes/data/notes.json`. Shipped example: `skills/market-notes/examples/notes.example.json`.

## Quick Start

```bash
# Add a note
uv run skills/market-notes/scripts/run.py add BTCUSD "cycle bottom thesis: wait for $45-50k or 1w structure flip" --expires 30d

# List active notes (one pair or all)
uv run skills/market-notes/scripts/run.py list
uv run skills/market-notes/scripts/run.py list BTCUSD

# Include expired notes (audit)
uv run skills/market-notes/scripts/run.py list --all

# Remove a note by index
uv run skills/market-notes/scripts/run.py remove BTCUSD 0

# Drop expired notes from disk
uv run skills/market-notes/scripts/run.py prune

# Machine output
uv run skills/market-notes/scripts/run.py --json list BTCUSD

# Custom file location
uv run skills/market-notes/scripts/run.py --config /path/to/notes.json list

# Validate the storage file
uv run skills/market-notes/scripts/run.py validate
```

## Data file

Default: `skills/market-notes/data/notes.json`. Override via:

- `--config PATH` on every subcommand
- `MARKET_SKILLS_NOTES_PATH` env var (absolute path)

## Schema

```json
{
  "BTCUSD": [
    {
      "note": "cycle bottom thesis: wait for $45-50k or 1w structure flip",
      "added": "2026-04-13T09:38:14+00:00",
      "expires": "2026-07-12T09:38:14+00:00",
      "updated": null,
      "status": "thesis",
      "type": "thesis",
      "state": null,
      "active_timeframe": null,
      "dependencies": null,
      "price_refs": null,
      "invalidates_on": "weekly_close_above_EMA21",
      "tags": []
    }
  ]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `note` | yes | Free-text thesis / observation / plan. |
| `added` | yes | ISO timestamp, UTC. Set automatically on `add`. |
| `expires` | no | ISO timestamp or `null`. Accepts shorthand on input: `14d`, `2w`, `1m`, `6h`. Pass `--expires never` to omit. |
| `updated` | no | ISO timestamp of last edit. Not yet auto-managed — reserved for future `edit` command. |
| `status` | recommended | Lifecycle state — one of `thesis`, `open`, `setup`, `watchlist`, `hedge`, `starter`, `invalidated`, `post_mortem`. |
| `type` | recommended | Kind of note — one of `thesis`, `setup`, `observation`, `plan`, `note`. |
| `state` | optional | Structural state of the underlying — one of `coiled_range_intact`, `coiled_range_broken`, `trending_up`, `trending_down`, `range_bound`, `unknown`. Queryable separately from prose. |
| `active_timeframe` | optional | First-class timeframe the note applies to (e.g. `"1d"`, `"4h"`). Distinguishes cross-TF theses like NEAR's 4h setup vs 1d macro. |
| `dependencies` | optional | List of pair keys whose thesis this note rides on. Lets the scanner warn when a base thesis is invalidated. |
| `price_refs` | optional | Typed price levels — keys: `stop`, `target`, `target_2`, `target_3`, `entry`, `invalidation_below`, `invalidation_above`. Replaces ad-hoc `meta.stop`, `meta.target`, etc. |
| `invalidates_on` | optional | Free-text condition (e.g. `"weekly_close_above_EMA21"`, `"structure_break_below_484"`). |
| `tags` | optional | Escape-hatch for ad-hoc markers that don't fit the triple (`"wait"`, `"re-scoped"`, etc.). |

### Legacy `meta` field

Older notes may still carry a `meta` blob. New writes use the typed fields only. The CLI's `migrate` subcommand rewrites legacy entries in-place:

```bash
uv run skills/market-notes/scripts/run.py migrate            # rewrite in place
uv run skills/market-notes/scripts/run.py migrate --dry-run  # report only
```

The migration:
- Maps `meta.tags` into `status` / `type` via a built-in tag dictionary (see `analysis/notes_format._LEGACY_TAG_MAP`).
- Moves `meta.invalidates_on` → `invalidates_on`.
- Moves numeric `meta.stop` / `meta.target` / `meta.entry` / `meta.invalidation_below` / `meta.invalidation_above` → `price_refs.*`.
- Maps `1d_only` / `4h_only` / `1wk_only` tags → `active_timeframe`.
- Drops unknown meta keys after warning (and keeps any leftover free-form tag in `tags`).

After migration, `validate` no longer reports the legacy-`meta` error.

Expired notes remain on disk; `list` hides them, `list --all` shows them, `prune` removes them.

## Library use (from other skills / scripts)

```python
from analysis.notes import load_active, add_note

notes = load_active("BTCUSD")    # -> list[dict], filtered to non-expired
entry = add_note("NEARUSD", "pullback-bounce trigger at $1.545", expires="14d", meta={"tags": ["setup"]})
```

The skill's `lib.py` re-exports the same surface for compatibility with `analysis.skill_loader.load_skill("market-notes")`.

## Integration with other skills

| Skill | Flag | Behaviour |
|-------|------|-----------|
| `run-all-l2` | `--include-notes` | Adds `notes` to per-ticker output. Off by default. |
| `run-all-l3` | `--include-notes` | Same. |
| `run-watchlist` | `--include-notes` / `--no-notes` | Default ON for run-watchlist (it is the "morning brief" use case). |

The watchdog (`position-watchdog`) reads its own per-position config (`skills/position-watchdog/data/watches.json`) — different semantics (entry_price/position_size, stop/TP ladders). Keep them separate.

## Cron integration

`scripts/run.sh` is a thin wrapper that activates uv and calls `scripts/run.py`. Cron jobs can reference it directly:

```bash
# Weekly prune of expired notes
0 6 * * 1  bash /path/to/market-skills/skills/market-notes/scripts/run.sh prune
```

Most use cases for notes are human-driven (add a thesis, list before acting). Cron only needs `prune`.

## Workflows

**Capture a thesis after a session:**
```bash
uv run skills/market-notes/scripts/run.py add BTCUSD \
    "Mayer approaching MEAN_REVERT; F&G extreme fear supports accumulation timing. Wait for weekly close > EMA21 as first sign of structure recovery, or capitulation washout below \$60k for real bottom entry." \
    --expires 30d --meta '{"tags":["thesis","wait"],"invalidates_on":"weekly_close_above_EMA21"}'
```

**Survey context before a morning brief:**
```bash
uv run skills/run-watchlist/scripts/run.py crypto_majors    # auto-includes notes
```

**Audit expired notes before deleting:**
```bash
uv run skills/market-notes/scripts/run.py list --all
uv run skills/market-notes/scripts/run.py prune
```

## Validation

`validate` walks the file and reports schema errors. Use after manual edits:

```bash
uv run skills/market-notes/scripts/run.py validate
# OK — 7 pair(s) with notes, 11 note(s) total
```

## Note content structure

Notes are a **letter to future-us**. Every note should answer:
- **What's the thesis?** Why hold, watch, or avoid this pair?
- **Key structural levels** — support, resistance, invalidation.
- **What to watch for** — catalysts or conditions that would change the thesis.

**Do NOT include:**
- **Tier** — that's in `market-watchlist`.
- **Current prices or today's scores** — go stale by next scan.
- **Position details** (qty, cost basis, P&L) — that's `position-watchdog` and `portfolio-mgmt`.
- **Live macro values** — describe the regime instead ("F&G extreme fear").

Good note patterns:
- **Cycle thesis (BTC/ETH):** "$45-50k target, invalidation at daily close below shakeout low"
- **Open position (NEAR):** "Setup type, structural stop, target, invalidation level"
- **Hedge (PAXG):** "Not a cycle play, buy dips to EMA50"
- **Watchlist (SOL):** "Cycle valuation zone, no trigger yet"
- **Conviction (ZEC):** "Coiled range framework with $X upside / $Y base"

**Pitfall — stale notes:** When price breaches a note's invalidation level, remove the old note and add a fresh one. `remove` uses 0-based index from `list` output — remove highest index first if clearing multiple.

## Exit codes

- `0` — success
- `1` — fatal (bad `--meta`, file I/O error, schema error)
- `2` — invalid usage (missing args, unknown subcommand)