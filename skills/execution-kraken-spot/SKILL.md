---
name: execution-kraken-spot
description: "Place Kraken spot orders via the `kraken` CLI. Read-only ops (balance / open-orders / cancel) and live order submission with `--dry-run` validation, interactive confirm, fill polling, and automatic portfolio-mgmt wiring on success."
version: 0.1.0
metadata:
  hermes:
    tags: [execution, kraken, orders, trading]
    category: execution
compatibility: "Requires Python 3.12+, uv, and the `kraken` CLI on PATH"
---

# execution-kraken

Kraken spot execution adapter. Wraps the `kraken` CLI in an
`ExecutionProvider` (`analysis/providers/execution_base.py:ExecutionProvider`)
so other skills can place orders through a
pluggable interface.

> **LLM-facing.** This `SKILL.md` is the schema. Read this file before
> calling the skill; the [Intent shape](#intent-shape) section is what
> you build and pass to `submit --intent`. The skill is a **tool** — it
> does not auto-execute. Always ask the user to confirm before invoking
> `submit` without `--yes`, and prefer `--dry-run` first.

## Quick Start

```bash
# Live limit buy — prints the order, asks for confirmation, submits, polls for fill
uv run skills/execution-kraken/scripts/run.py submit \
  --pair HYPEUSD --side buy --order-type limit \
  --volume 1.5 --limit-price 60.15

# Dry-run (kraken --validate, no venue side-effect)
uv run skills/execution-kraken/scripts/run.py submit \
  --pair HYPEUSD --side buy --order-type limit \
  --volume 1.5 --limit-price 60.15 --dry-run

# From an Intent JSON file
uv run skills/execution-kraken/scripts/run.py submit \
  --intent examples/intent.example.json --yes

# Skip confirmation (LLM-driven run; user has explicitly pre-approved)
uv run skills/execution-kraken/scripts/run.py submit \
  --pair BTCUSD --side buy --order-type market --volume 0.01 --yes

# Read-only ops
uv run skills/execution-kraken/scripts/run.py balance
uv run skills/execution-kraken/scripts/run.py orders
uv run skills/execution-kraken/scripts/run.py balance --json | jq '.'

# Cancel
uv run skills/execution-kraken/scripts/run.py cancel OABCDE-12345-FGHIJ
```

## Subcommands

| Subcommand | Default | Purpose |
|------------|---------|---------|
| `submit`   | yes (when no subcommand given) | Place an order |
| `balance`  | —       | Show cash balances (`kraken balance`) |
| `orders`   | —       | List open orders (`kraken open-orders`) |
| `cancel`   | —       | Cancel one order by id (`kraken order cancel`) |

## Modes

There is no paper mode by design — fills always hit the venue.
All order placements hit the venue. Two guard rails apply:

1. **`--dry-run`** — calls the CLI with `kraken order --validate`. Same
   shape as a real submit, but no order is placed. This is the safe
   pre-flight check for "would this order actually go through?".
2. **Interactive confirm** — `submit` prints the order summary and asks
   `Submit this order to Kraken? (y/N)` before placing. Pass `--yes` /
   `-y` to skip the prompt when the LLM has the user's explicit
   pre-approval. The confirm prompt is the actual safety layer; never
   bypassed silently.

For market orders, `submit` blocks up to `--wait-timeout` (default 5 s)
for the venue to report a terminal fill. For limit orders, prefer
`--no-wait` — the order may sit on the book for hours and the watchdog
should observe fills, not the execution skill.

## Intent shape

This skill consumes an **Intent** — the single contract shared between
the risk layer and execution. The canonical TypedDict lives at
`analysis/providers/execution_base.py:Intent`; the table below is the
LLM-facing summary that mirrors it. **If you're an LLM building an Intent
to pass to this skill, copy the example below and edit the values.**

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `intent_id` | yes | string | Unique id; forwarded to Kraken as `--cl-ord-id`. Suggested: `<strategy>-<pair>-<timestamp>` or a UUID. |
| `venue` | yes | string | Must be `"kraken"` for this skill. |
| `pair` | yes | string | Kraken pair notation, no dash/slash. e.g. `BTCUSD`, `HYPEUSD`, `ETHEUR`. |
| `side` | yes | `"buy"` \| `"sell"` | |
| `order_type` | yes | enum | `market` \| `limit` \| `stop-loss` \| `take-profit` \| `stop-loss-limit` \| `take-profit-limit` \| `trailing-stop` \| `trailing-stop-limit` \| `iceberg` \| `settle-position` |
| `volume` | yes | number > 0 | Base-asset quantity (e.g. `0.01` BTC, `1.5` HYPE). |
| `limit_price` | required for non-market | number > 0 | Trigger price for stop/take-profit variants; primary price for limit. |
| `stop_price` | optional | number > 0 | Secondary trigger for `*-limit` order variants. |
| `time_in_force` | optional | `"GTC"` \| `"IOC"` \| `"GTD"` | Defaults to GTC at venue. |
| `deadline` | optional | RFC3339 string | Matching-engine arrival deadline. |
| `thesis` | optional | string | Free-text; persisted in portfolio-mgmt notes. |
| `strategy` | optional | string | e.g. `"trend-follow"`; persisted. |
| `conviction` | optional | int 1–5 | L3 conviction; persisted. |
| `source_skills` | optional | list of strings | L2/L3 skills that produced this Intent; persisted. |
| `notes` | optional | object | Free-form metadata persisted into portfolio-mgmt notes blob. |
| `extras` | optional | object | Provider-specific kwargs forwarded as `--key value` to Kraken (underscore → dash). |
| `decision_decoration` | optional | object | Augments the auto-built `decision_context` (regime, macro signals, risk verdict, override). Forwarded to `analysis.decision.build_decision_context_from_idea()` and written to the `decisions` table. See the [decision_context auto-population](#decision_context-auto-population) section for the key list. |

### Example Intent

```json
{
  "intent_id": "trend-follow-HYPEUSD-2026-06-22-001",
  "venue": "kraken",
  "pair": "HYPEUSD",
  "side": "buy",
  "order_type": "limit",
  "volume": 1.5,
  "limit_price": 60.15,
  "time_in_force": "GTC",
  "thesis": "Breakout retest at ascending trendline",
  "strategy": "trend-follow",
  "source_skills": ["market-accumulation", "market-trend"],
  "conviction": 4
}
```

A full machine-readable copy lives at `examples/intent.example.json`.

The CLI accepts intents two ways:

**From file:**

```bash
uv run skills/execution-kraken/scripts/run.py submit --intent path/to/intent.json
```

**From direct flags** (the CLI builds an Intent; `intent_id` defaults to
`cli-<uuid>` if not supplied):

```bash
uv run skills/execution-kraken/scripts/run.py submit \
  --pair HYPEUSD --side buy --order-type limit \
  --volume 1.5 --limit-price 60.15 \
  --thesis "Breakout retest" --strategy trend-follow --conviction 4 \
  --source-skills "market-accumulation,market-trend"
```

> **LLM agent brain**: for the per-status workflow when this skill returns a `FillConfirmation` (filled / partial / open / error / rejected / submitted), see [`LLM-ORCHESTRATION.md`](../../../LLM-ORCHESTRATION.md) §3. For idempotency rules on `intent_id` / `--cl-ord-id`, see §4.

## FillConfirmation shape (output)

After `submit`, the skill emits a `FillConfirmation` (TypedDict in
`analysis/providers/execution_base.py`). Key fields for the LLM to
narrate:

- `status` — terminal state: `filled` / `partial` / `submitted` / `open`
  / `cancelled` / `expired` / `rejected` / `error`
- `order_id` — Kraken txid (use this with `cancel <order_id>` if needed)
- `filled_volume` — what the venue reported as filled (`0.0` for
  `submitted` / `open`)
- `fill_price` — weighted-avg price for partials; `None` if no fills yet
- `cost_quote` — venue-reported total cost in quote currency
- `fee`, `fee_currency` — venue-reported fees (`ZUSD`/`XXBT` canonicalised
  to `USD`/`BTC`)
- `reason` — human-readable status detail; populated for rejected/error
- `raw` — full submit + query envelopes for debugging

`--json` flag emits the full payload as a single JSON object to stdout
for machine consumers.

## Idempotency

`Intent.intent_id` is forwarded as `--cl-ord-id` to Kraken on every
submit. Kraken enforces uniqueness per `cl-ord-id` per API key — a
retried intent with the same `cl-ord-id` returns the original order
instead of placing a duplicate. Server-side dedup of "intent already
executed" is the caller's job. This skill just plumbs the
field through.

## Portfolio wiring

Successful fills (`status="filled"` or `status="partial"`) auto-write a
row to the portfolio-mgmt SQLite DB (`portfolio.db.add_transaction`)
when `--portfolio <name|id>` is supplied. The asset notation is
`kraken:<PAIR>` (e.g. `kraken:HYPEUSD`) — same convention the data
provider uses, so `prices refresh` works without a registry update.

Side effects recorded per row:

- `side` — buy/sell from the fill
- `asset` — `kraken:<PAIR>`
- `qty` — `filled_volume`
- `price` — `fill_price`
- `cost_quote` — `cost_quote` (venue-reported)
- `fee` + `notes.fee_currency` — venue fee
- `tx_hash` — Kraken order id (txid)
- `ref` — `intent_id` for downstream reconciliation
- `notes` (JSON) — full provenance: `order_id`, `cl_ord_id`, `venue`,
  `strategy`, `source_skills`, `thesis`, `intent_id`, plus a structured
  `decision_context` block capturing the *state at decision time* —
  L3 idea summary, regime, macro signals, risk verdict, override flag.
  The canonical schema lives in ``analysis/decision.py::DecisionContext``;
  see `portfolio-mgmt` SKILL.md §"decision_context — structured
  decision trace" for a human-readable summary.

### `decision_context` auto-population

The auto-log path reads from the live Intent + the risk verdict / macro snapshot the LLM passed at submit time, calls `analysis.decision.build_decision_context_from_idea()` to build the trace, and writes it to two places:

- The **`decisions` table** (system of record in `portfolio.db`) — one row per `intent_id`, typed schema. Idempotent on `intent_id`: a retried submit with the same id returns the existing row, never raises.
- A nested copy in `notes.decision_context` (backward compat with tools that read the transaction notes JSON)

The LLM supplies the risk verdict / macro snapshot / override flag via the Intent field `decision_decoration` (or the CLI flag `--decision-decoration` for one-shot runs). The lib merges those into the auto-built `DecisionContext` and writes the result to the `decisions` table.

Schema fields populated from each source:

| Source | Field |
|--------|-------|
| Intent | `intent_id`, `source_skill` (= `Intent.strategy` or `"manual"`), `l3_idea.direction` (from `Intent.side`, mapped to canonical `long`/`short`), `l3_idea.conviction`, `l3_idea.entry_price`, `l3_idea.stop`, `l3_idea.tp1/2/3` (from `Intent.bracket` or L3 idea ladder) |
| Last L3 idea for the pair (cached) | `l3_idea.summary` (1-line), `l3_idea.rr_to_tp2` |
| Macro snapshot at submit time (from `Intent.decision_decoration` or `--decision-decoration`) | `regime.label`, `regime.fng`, `regime.btc_dominance`, `regime.divergence`, `macro_signals[]` |
| Risk verdict JSON (from `Intent.decision_decoration` or `--decision-decoration`) | `risk_verdict.status`, `risk_verdict.concerns[]`, `risk_verdict.position_size_pct` (computed from intent cost / portfolio total_value) |
| Override flag (`--override-from-suggestion` or `Intent.decision_decoration.override_from_suggestion`) | `override.from_suggestion` (default false; flipped to true if the user accepted but modified suggested stop/tp/volume before confirming) |
| Fill timestamp | `captured_at` (ISO UTC) |

If a field's source is unavailable (e.g. macro snapshot wasn't passed, no cached L3 idea), the field is left as `null` — never fabricated. The auto-log path is additive: it never overwrites user-supplied `decision_context` from the Intent.

The CLI accepts the decoration two ways:

```bash
# JSON blob
uv run skills/execution-kraken-spot/scripts/run.py submit \
  --intent intent.json \
  --decision-decoration '{"regime_label":"RISK_ON","risk_status":"APPROVED","macro_signals":["fng_greed"]}' \
  --override-from-suggestion

# Or in the Intent JSON file:
# { "intent_id": "...", ..., "decision_decoration": { "regime_label": "RISK_ON", ... } }
```

Recognised `--decision-decoration` keys: `regime_label`, `regime_fng`, `regime_btc_dominance`, `regime_divergence`, `macro_signals`, `risk_status`, `risk_position_size_pct`, `risk_concerns`, `override_field`, `override_reason`. Unknown keys are passed through for forward compatibility.

Skip portfolio wiring by omitting `--portfolio`. The skill still prints
the fill confirmation but does not touch the SQLite DB — useful for
reconciliation / dry audits.

## Cron integration

Suggested schedule: on-demand. This skill
does not poll or schedule itself — `position-watchdog` does
monitoring; this skill does the actual placing when the LLM is told
to execute.

```bash
# Cron-friendly: --yes skips the prompt, --json gives machine output
uv run skills/execution-kraken/scripts/run.py submit \
  --intent /path/to/intent.json --yes --json \
  | tee /var/log/market-skills/fills/$(date -u +%Y%m%dT%H%M%S).json
```

## Exit codes

- `0` — success (live submit returned, dry-run validated, read-only op succeeded)
- `1` — venue error / CLI failure / cancel failed
- `2` — input validation failure (bad intent, missing args, REJECT status)

## Safety checklist before running live

1. `kraken auth status` — confirm API key is configured
2. Run with `--dry-run` first to see what the venue thinks
3. Start with a tiny `--volume` on a non-critical pair
4. Confirm `watches.json` in `position-watchdog` is set up to track the
   position after fill — the watchdog handles stop / TP monitoring, not
   this skill
5. For limit orders, prefer `--no-wait` + let the watchdog detect fills

## Files

```
skills/execution-kraken-spot/
├── SKILL.md                          # this file
├── lib.py                            # pure helpers (intent loading, render, portfolio wiring)
├── scripts/
│   └── run.py                        # CLI (argparse, confirm prompt, dispatch)
├── examples/
│   └── intent.example.json           # sample Intent for testing
└── data/                             # empty; gitignored, reserved for future state
```
