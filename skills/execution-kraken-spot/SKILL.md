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

## When NOT to use

- For live money without explicit user approval — the interactive `y/N` confirm is the safety layer; never invoke `submit` (or pass `--yes`) unless the user has explicitly pre-approved the exact order. This skill places real orders on Kraken.
- For perps orders — use `execution-kraken-perps` (different venue subcommand and Intent fields).
- For analysis, sizing, or risk vetting — those are `risk-engine` and the market-* / strategy-* skills. This skill only executes an Intent handed to it.

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
| `intent_id` | yes | string | Unique id; forwarded to Kraken as `--cl-ord-id`. **Max 18 characters** — measured venue limit (see [Idempotency](#idempotency)); Kraken's docs claim 36, but the venue rejects longer values with `EGeneral:Invalid arguments:cl_ord_id`. Suggested: `<short-strategy>-<pair>-<nn>`, e.g. `tf-hype-0622a`. A UUID (36 chars) is rejected. Override the limit via `KRAKEN_CL_ORD_ID_MAX_LEN`. |
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
| `extras` | optional | object | Two namespaces (see [`extras`: venue flags vs risk-layer keys](#extras-venue-flags-vs-risk-layer-keys)): keys the `kraken order buy\|sell` CLI accepts are forwarded as `--key value` (underscore → dash); keys consumed by the risk layer (`reference_price`, `est_notional`, `position_value`) stay in the Intent and are never forwarded; any other key is rejected before the venue call. |
| `decision_decoration` | optional | object | Augments the auto-built `decision_context` (regime, macro signals, risk verdict, override). Forwarded to `analysis.decision.build_decision_context_from_idea()` and written to the `decisions` table. See the [decision_context auto-population](#decision_context-auto-population) section for the key list. |

### Example Intent

```json
{
  "intent_id": "tf-hype-0622a",
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
`cli-` + 13 random hex chars — 17 chars, within the measured 18-char
venue limit — if not supplied):

```bash
uv run skills/execution-kraken/scripts/run.py submit \
  --pair HYPEUSD --side buy --order-type limit \
  --volume 1.5 --limit-price 60.15 \
  --thesis "Breakout retest" --strategy trend-follow --conviction 4 \
  --source-skills "market-accumulation,market-trend"
```

> **LLM agent brain**: for the per-status workflow when this skill returns a `FillConfirmation`, see [`LLM-ORCHESTRATION.md`](../../../LLM-ORCHESTRATION.md) §3 — the canonical `status` vocabulary is the `status` bullet under "FillConfirmation shape" below. For idempotency rules on `intent_id` / `--cl-ord-id`, see §4.

## `extras`: venue flags vs risk-layer keys

`Intent.extras` carries two namespaces (decision record:
[ADR-0011](../../docs/adr/0011-intent-extras-namespaces.md); the
canonical key sets live in `analysis/providers/execution/base.py`):

1. **Venue flags** — keys whose dash-spelling is in
   `analysis/providers/execution/kraken_spot.py::KRAKEN_ORDER_FLAGS` (the
   order-level allowlist derived from `kraken order buy --help` /
   `kraken order sell --help`): `type`, `price`, `price2`, `displayvol`,
   `trigger`, `leverage`, `reduce-only`, `timeinforce`, `start-time`,
   `expire-time`, `userref`, `cl-ord-id`, `oflags`, `stptype`,
   `close-ordertype`, `close-price`, `close-price2`, `deadline`,
   `asset-class`. These are forwarded as `--key value` (underscore →
   dash, so the underscore spelling works). Process/global CLI options
   (`output`, `verbose`, `api-url`, `api-key`, `api-secret`,
   `api-secret-stdin`, `api-secret-file`, `futures-url`, `otp`, `yes`)
   and `validate` are excluded — they configure the CLI invocation, not
   the order.
2. **Risk-layer keys** — declared in
   `analysis/providers/execution/base.py::NON_VENUE_INTENT_EXTRAS` and
   consumed inside this repo, never forwarded to the venue:
   - `reference_price` → risk-engine spot market-order price hint
   - `est_notional` → risk-engine spot quote-ccy notional
   - `position_value` → risk-engine spot + perps funding-drag notional
   - `reference_entry` → perps liquidation-distance / stop-distance policies
   - `futures_symbol` → perps pair → futures symbol override

3. Anything else is rejected **before any venue call** with:

   ```
   error: extras key 'foo' is not a kraken order CLI flag and no in-repo
   layer consumes it — refusing to forward it to the venue (venue-flag
   extras: asset-class, cl-ord-id, close-ordertype, close-price,
   close-price2, deadline, displayvol, expire-time, leverage, oflags,
   price, price2, reduce-only, start-time, stptype, timeinforce,
   trigger, type, userref; consumed in-repo and never forwarded:
   est_notional, futures_symbol, position_value, reference_entry,
   reference_price)
   ```

   (CLI: exit 2; provider: `status="error"` confirmation.)

**Worked example** — a market Intent carrying the documented risk-engine
price hint. `reference_price` stays in the Intent, is never forwarded,
and the order validates and submits cleanly:

```json
{
  "intent_id": "tf-bnb-1oa",
  "venue": "kraken",
  "pair": "BNBEUR",
  "side": "buy",
  "order_type": "market",
  "volume": 0.29093,
  "extras": { "reference_price": 687.43 }
}
```

**Dry-run/live agreement**: the `--dry-run` branch and the live submit
both build their argv from the same
`analysis/providers/execution/kraken_spot.py::build_order_args`, so an
intent that passes dry-run forwards exactly the same (allowlisted) venue
flags at live time — a dry-run green light can never be followed by an
extras-forwarding failure.

## FillConfirmation shape (output)

After `submit`, the skill emits a `FillConfirmation` (TypedDict in
`analysis/providers/execution_base.py`). Key fields for the LLM to
narrate:

- `status` — terminal state: `filled` / `partial` / `submitted` / `open`
  / `cancelled` / `expired` / `rejected` / `error` / `unknown` — raw
  venue strings never leak through; a venue label outside the provider's
  mapping table normalises to `unknown`
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

**Measured cl-ord-id length limit: 18 characters.** Despite Kraken's
public docs claiming 1–36 characters, the venue rejected every measured
value longer than 18 chars (19/20/22/24/28/34/40 chars) with
`EGeneral:Invalid arguments:cl_ord_id`, while 17 and 18 chars passed.
One anomaly: a 32-char uppercase-hex value was accepted — the venue
appears to special-case some 32-char form — so 18 is the only bound safe
for every value; do not rely on it. The limit is configurable via the
`KRAKEN_CL_ORD_ID_MAX_LEN` env var (an override below 6 characters — the
`cli-` prefix plus 2 hex chars is the smallest id the auto-generator can
emit under the limit — fails default-path id generation with a clear
configuration error). A hand-supplied id over the limit
(`--intent-id` or an `intent_id` in an `--intent` JSON file) is rejected
at the CLI boundary before any venue call; it is never truncated, since
truncating an idempotency key could collide with a different order.

## Portfolio wiring

A confirmation carrying a fill (`filled_volume > 0`) auto-writes a row to
the portfolio-mgmt SQLite DB (`portfolio.db.add_transaction_with_decision`)
when `--portfolio <name|id>` is supplied — the gate is volume-based, not
a status-string test, so a venue fill can never silently skip the ledger
(Kraken reports fully-executed market orders as `closed`). The
transaction row and its
decision trace are written in a single SQLite transaction, so a partial
write (one row without the other) is impossible. The asset notation is
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
- `1` — venue error / CLI failure / cancel failed / **portfolio write failed after a venue fill** (venue and ledger disagree — record the fill manually before trusting cost basis; do not re-submit)
- `2` — input validation failure (bad intent, missing args, REJECT status, or an `extras` key that is neither a `kraken order` venue flag nor a declared in-repo Intent key — rejected **before any venue call**)

A live `submit --json` whose `confirmation.status` is `error` exits `1`
(matching the human path) — a machine caller can rely on the exit code,
not just the payload.

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
