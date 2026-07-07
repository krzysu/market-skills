# LLM Orchestration

> **Audience**: the LLM agent brain.
> Consumers reading the code do not need this file.

The repo does not own a Python orchestrator. You (the LLM) are the
agent brain (see `ARCHITECTURE.md`, 2026-06-22 pivot). This document
is the contract you follow when skills return shapes that need a
human-facing decision: a `RiskVerdict` from `risk-engine`, a
`FillConfirmation` from `execution-kraken-*`, or a partial-fill
recording to `portfolio-mgmt`.

Three cues route you here:

- `AGENTS.md` "LLM is the agent brain" section — the always-loaded trigger
- The relevant skill's `SKILL.md` (risk-engine, execution-kraken-spot,
  execution-kraken-perps, portfolio-mgmt, position-watchdog) — one-line
  pointer in the "What it returns" / "Failure modes" section
- `ARCHITECTURE.md` "LLM-as-agent-brain" paragraph — domain cross-ref

## 1. The happy-path workflow

```
1. User describes intent or asks for a scan
2. LLM calls the appropriate skill(s) as tools
3. LLM narrates the skill's `narrative` field to the user
4. If the skill is risk-vetting or executing, LLM applies the
   failure-mode contract from this document (sections 2 / 3)
5. LLM asks the user for confirmation (never assume)
6. LLM calls the next skill (e.g. execution) with the agreed Intent
7. The execution skill's interactive confirm is the actual safety
   layer that never gets bypassed — even when --yes is passed, the
   user has already explicitly pre-approved
```

You are the only layer that can see the whole picture. The skills
are deterministic; you are the narrator, the decision-maker, and the
human-confirmation handler. Cron is analytics-only; you are not
involved in cron ticks.

## 2. Risk-vet workflow

When you call `risk-engine`, you get back a `RiskVerdict`. Status is
worst-case across all fragments (`REJECT > SCALE > CONCERN >
APPROVED`).

| `status` | What you do |
|---|---|
| `APPROVED` | One-line narration. Proceed to execution. |
| `CONCERN` | Surface the `concerns[]` list verbatim. Ask: *"Risk flags this for [reasons]. Still proceed?"* Wait for explicit `yes`. If yes → execution. If no → drop. |
| `SCALE` | Surface `suggested_volume` and the original. Ask: *"Risk suggests scaling to {suggested_volume} (from {requested}). Use the scaled size, override with the original, or skip?"* The user picks. Pass the chosen `volume` to the next Intent you build. |
| `REJECT` | Narrate the rejecting policy + reason prominently. Ask: *"Risk REJECTS this: {reason}. Do you want to override? (advisory only — the execution skill confirm is still required)"*. Wait for explicit `yes` before calling execution. If `no` → drop, do not retry. |

Important:

- Risk is **advisory**, never a hard gate. A user with a good reason
  (closing a hedge the policy can't see) is allowed to override.
- The execution skill's interactive confirm is **the actual safety
  layer**. A `REJECT` you decide to override still requires the user
  to confirm at the execution prompt.
- If a perps intent, call with `--perps-account kraken-futures` so
  the perps policy set (leverage cap, liq distance, stop distance,
  funding drag, duplicate position) has live context.

## 3. Execution submission workflow

When you call `execution-kraken-spot submit` or
`execution-kraken-perps submit`, you get back a `FillConfirmation`
after the venue round-trip (or `--dry-run` validation).

| `status` | What you do |
|---|---|
| `filled` | Narrate fill price, volume, fee. Portfolio-mgmt has been auto-wired (when `--portfolio` was supplied). Confirm the position is now monitored. |
| `partial` | **Two-step.** (1) Record the partial in `portfolio-mgmt` if the auto-wiring was skipped or the user wants a manual entry — `add --portfolio <name> --side <buy\|sell> --asset kraken:<PAIR> --qty <filled_volume> --price <fill_price>`. (2) Ask the user: *"Partial fill: {filled}/{requested} at {price}. Place the remainder as a new order, hold off, or cancel the rest?"* Do not auto-place the remainder. |
| `submitted` | A market order the venue accepted but did not fill in `--wait-timeout`. Narrate: *"Order accepted at venue, awaiting fill. Watchdog will detect."* Hand off to `position-watchdog` if a watch is configured for this pair. |
| `open` (post `--wait-timeout`, limit didn't fill) | The order is on the book. Narrate: *"Limit order is working at the venue. Watchdog will fire on fill."* Hand off to `position-watchdog`. Do not retry. |
| `cancelled` / `expired` | Narrate the venue's reason. No portfolio side effect. Ask the user how to proceed. |
| `rejected` / `error` | **Surface `reason` verbatim.** Ask: *"Venue rejected: {reason}. Retry? Cancel? Manual intervention?"* The user picks. If you retry, use the **same `intent_id`** (idempotency — see section 4). If you cancel, call `cancel <order_id>`. |
| `submitted` with TP-failed warning (perps only) | Stop succeeded, TP didn't. The position is still protected by the stop. Narrate: *"Stop is live, TP failed to attach. Place TP manually or hold."* |

Important:

- For limit orders, prefer `--no-wait` on submit. The watchdog
  handles live-tick fill detection; the execution skill handles
  initial bracket placement, not monitoring.
- A retried `submit` with the same `intent_id` is **idempotent** —
  the venue returns the original order, no duplicate. Use this for
  transient error retries, not for new orders.
- Do not invent new `intent_id`s for retries of the same intent. Do
  not reuse an `intent_id` across different intents within a
  session (see section 4).

## 4. Idempotency contract

`Intent.intent_id` is plumbed through to Kraken as `--cl-ord-id`
(spot) or `--client-order-id` (perps). Kraken enforces uniqueness
per `client_order_id` per API key. A retried submit with the same id
returns the original order; a *different* intent with the same id
collides and is rejected.

You are responsible for **not generating colliding `intent_id`s
within a session**. Recommended scheme:

```python
f"{strategy_name}-{pair}-{direction}-{entry_price:.4f}-{int(time.time())}"
```

Examples:

- `trend-follow-HYPEUSD-buy-60.15-1719331200`
- `mean-reversion-BTCUSD-sell-67500.0000-1719331300`
- `manual-kraken-spot-ETHUSD-buy-3450.50-1719331400` (for hand-built Intents)

Reuse the same id **only** when explicitly retrying a failed submit
of the same intent (same pair, same side, same entry, same
strategy). Any new intent — even a re-decision on the same setup
minutes later — must get a fresh `intent_id` (and the new
`time.time()` does that for free).

## 5. Tool-call summary

The skills you call most often, what they return, and what to do
with the return. See each skill's `SKILL.md` for the full schema.

| Skill | Returns | Your job |
|---|---|---|
| `market-*` (L1) | `{score, signal, zone, ...}` | Narrate; the L2 layer typically consumes these. |
| `market-*` (L2) | `{pattern, signals, input_scores, narrative}` | Use `l2_fired()` to gate downstream logic; narrate the `narrative` field. |
| `strategy-*` (L3) | `{ideas, narrative}` | Each idea has a `version: "v1".."v5"`. Narrate ideas + `narrative`. |
| `run-all-l3` | `{interval, period, macro, tickers: {t: {strategies: {s: {ideas, narrative}}}}}` | `macro` is a `RegimeSignal` — narrate its `regime_note`. The `agents should not generate colliding` contract applies. |
| `market-macro` | `RegimeSignal` | Narrate `regime_note`; use the structured `regime` block in your reasoning. |
| `risk-engine` | `RiskVerdict` | Section 2 of this document. |
| `execution-kraken-spot` | `FillConfirmation` | Section 3 of this document. |
| `execution-kraken-perps` | `FillConfirmation` (with `bracket`) | Section 3 of this document. |
| `portfolio-mgmt` | DB state | Read positions / P&L; `add` for manual entries or partial-fill recording. |
| `position-watchdog` | alerts (silent on normal ticks) | Forward to user; this is a one-way monitor, never an executor. |
| `bug-scan` | diagnostic events | Diagnostic only. Surface to user; never pair with execution. |

## 6. Things you must NEVER do

- **Never** call `execution-kraken-* submit` without explicit user
  approval (the interactive confirm is the gate; you telling the
  CLI `--yes` is *not* a substitute — it just suppresses the prompt
  the user has already answered in chat).
- **Never** silently retry a `REJECT` from `risk-engine`. The user
  has to weigh in.
- **Never** retry a `rejected` / `error` `FillConfirmation` with a
  *new* `intent_id` — that creates a duplicate. Either reuse the
  same id or wait for the user.
- **Never** auto-place a remainder on a `partial` fill. The user
  decides.
- **Never** edit portfolio-mgmt transactions (`edit`/`remove`)
  silently. `remove` and re-add for corrections; never rewrite
  `qty`/`price`/`side` in place.
- **Never** narrate a `RiskVerdict` `REJECT` as an "override
  recommended" without surfacing the reason first.
- **Never** narrate a `FillConfirmation` `error` without quoting
  the venue's `reason` field verbatim.

## 7. Cron context

You are not in the cron path. Cron ticks are analytics-only
(`run-all-l3`, `position-watchdog`, `risk-engine` with
`--from-state`). The cron pipeline does not auto-execute. The
`run-all-l3` envelope attaches `macro` to the top of its JSON; the
`position-watchdog` cron fires alerts the user has to act on. When
the user comes back from a cron run, your job is to read the
envelope, narrate the alerts, and ask what to do.

For context across cron runs:

- Macro history: `$XDG_DATA_HOME/market-skills/macro_history.json` (200-entry cap)
- L3 idea history: `$XDG_DATA_HOME/market-skills/l3_idea_history.json` (200-entry cap)
- Watchdog per-watch state: `skills/position-watchdog/data/` (per watch)

When the user asks *"what was the regime 12 hours ago?"* or
*"haven't I seen this setup before?"*, those are the files you read.
