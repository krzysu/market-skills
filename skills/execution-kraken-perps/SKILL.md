---
name: execution-kraken-perps
description: "Place Kraken perpetual-futures bracket orders (open + stop + take-profit) via the `kraken` CLI. Reads an Intent, runs analysis.risk.vet with perps policies auto-selected, places the bracket via `kraken futures ...`, and wires successful fills to portfolio-mgmt. Read-only ops: balance / positions / cancel. Interactive confirm; no paper mode."
version: 0.1.0
metadata:
  hermes:
    tags: [execution, kraken, perps, futures, bracket]
    category: execution
compatibility: "Requires Python 3.12+, uv, and the `kraken` CLI on PATH"
---

# execution-kraken-perps

Perps execution adapter for Kraken flexible futures. Sibling of
`execution-kraken-spot` — same Intent contract, separate venue surface.

## When to use

- You have a TradeIdea (L3 strategy output) or hand-built Intent for a
  perps entry with explicit stop + take-profit.
- You want the risk layer (`analysis.risk.vet`) to vet the perps
  bracket before venue submission.

## When NOT to use

- For spot orders — use `execution-kraken-spot`. Perps and spot share
  the Intent contract but place orders through different Kraken CLI
  subcommands (`futures` vs `order`); the provider dispatch is on
  `Intent.venue`.
- For non-Kraken venues — covered by the ExecutionProvider Protocol;
  see `analysis/providers/execution_base.py`.

## Pre-submit workflow

The full agent flow for a perps trade:

1. **Build the Intent.** Start from an L3 idea or manual inputs. Required
   perps-only fields: `venue: "kraken-perps"`, `leverage` (int), `bracket`
   (`{stop_loss, take_profit}`), `extras.reference_entry` (for liq-distance
   and stop-distance policies), `extras.position_value` (for funding-drag).
2. **Vet with risk-engine**, with perps auto-fetch enabled:
   ```bash
   uv run skills/risk-engine/scripts/run.py \
     --intent <intent.json> \
     --portfolio spot \
     --perps-account kraken-futures \
     --json
   ```
   The `--perps-account` flag triggers `kraken futures positions` (open
   perps positions) and `kraken futures historical-funding-rates` (current
   rate) so the perps policies have live context. MM rate comes from the
   static `MM_RATES` table in `analysis/providers/execution_kraken_perps.py`
   — no fetch needed. Read the verdict JSON: if `status=REJECT`, narrate
   the reasons and ask the user before proceeding. If `status=APPROVED`/
   `CONCERN`/`SCALE`, narrate the concerns and ask for confirmation.
3. **Submit with execution-kraken-perps**:
   ```bash
   uv run skills/execution-kraken-perps/scripts/run.py submit \
     --intent <intent.json> --yes
   ```
   The CLI prints a bracket summary and prompts for the user's explicit
   `y/N` (suppressed only with `--yes`). On fill, auto-writes a row to
   portfolio-mgmt with `source="execution-kraken-perps"`.
4. **Hand off to position-watchdog** for stop / TP monitoring (the watchdog
   handles live price ticks and exit detection; this skill places the
   initial bracket and that's it).

The execution CLI also runs the same perps risk vet internally before
submitting, so a direct call without step 2 still gets the perps
guardrails — but the LLM should still call risk-engine first so it can
narrate the verdict and get user confirmation before reaching the
submit-prompt layer.

## Quick start

```bash
# Live bracket — interactive confirm
uv run skills/execution-kraken-perps/scripts/run.py submit \
  --pair SOLUSD --side sell --volume 11.5 \
  --leverage 2 --stop-loss 76.66 --take-profit 58.07 \
  --reference-entry 69.22 --position-value 800 \
  --thesis "Breakdown retest at ascending trendline" \
  --strategy trend-follow --conviction 4 \
  --portfolio spot

# Dry-run — build Intent, run risk vet, render summary. No venue side-effect.
uv run skills/execution-kraken-perps/scripts/run.py submit \
  --pair SOLUSD --side sell --volume 11.5 \
  --leverage 2 --stop-loss 76.66 --take-profit 58.07 \
  --reference-entry 69.22 --position-value 800 \
  --dry-run

# From an Intent file
uv run skills/execution-kraken-perps/scripts/run.py submit \
  --intent examples/intent.example.json --yes --json

# Read-only ops
uv run skills/execution-kraken-perps/scripts/run.py balance
uv run skills/execution-kraken-perps/scripts/run.py balance --json | jq .
uv run skills/execution-kraken-perps/scripts/run.py positions
uv run skills/execution-kraken-perps/scripts/run.py cancel OFILL-12345
```

## Subcommands

| Subcommand | Default | Purpose |
|------------|---------|---------|
| `submit`   | yes (when no subcommand given) | Place a perps bracket |
| `balance`  | —       | Show futures account balances |
| `positions`| —       | List open perps positions |
| `cancel`   | —       | Cancel one open order by id |

## Modes

There is no paper mode by design. Two guard rails apply:

1. **`--dry-run`** — builds the Intent, runs risk vet, prints the
   bracket summary. No venue side-effect. This is the safe pre-flight
   check.
2. **Interactive confirm** — `submit` prints the bracket summary and
   asks `Submit this perps bracket to Kraken? (y/N)` before invoking
   the provider. Pass `--yes` / `-y` to skip the prompt when the user
   has explicitly pre-approved (LLM-driven runs).

## Intent shape

This skill consumes an **Intent** with `venue="kraken-perps"`. The
canonical TypedDict lives at
`analysis/providers/execution_base.py:Intent`; the perps-specific
fields are:

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `intent_id` | yes | string | Plumbed as `--client-order-id` to Kraken for idempotent retries |
| `venue` | yes | `"kraken-perps"` | Required |
| `pair` | yes | string | Spot pair notation (`SOLUSD`); the provider resolves to the futures symbol |
| `side` | yes | `"buy"` (long) / `"sell"` (short) | |
| `order_type` | yes | `"market"` (default) / `"limit"` | Open-leg order type |
| `volume` | yes | number > 0 | Base-asset quantity |
| `leverage` | yes (perps) | int > 0 | Tier-capped: 2x for BTC/ETH/SOL, 5x default for alts |
| `bracket` | yes (perps) | `{stop_loss, take_profit}` | Both required for full bracket submission |
| `extras.position_value` | optional | number > 0 | Position notional in quote ccy; used by funding-drag risk policy |
| `extras.reference_entry` | optional | number > 0 | Reference entry for liq-distance + stop-distance risk policies |
| `limit_price` | optional | number > 0 | Limit price for `--order-type=limit` opens |
| `time_in_force` | optional | `"GTC"` / `"IOC"` / `"GTD"` | |
| `deadline` | optional | RFC3339 string | |
| `thesis`, `strategy`, `source_skills`, `conviction` | optional | provenance | Persisted into portfolio-mgmt notes |
| `decision_decoration` | optional | object | Augments the auto-built `decision_context` (regime, macro signals, risk verdict, override). Forwarded to `analysis.decision.build_decision_context_from_idea()` and written to the `decisions` table. See the [Portfolio wiring](#portfolio-wiring) section for the key list. |

A full machine-readable example lives at
`examples/intent.example.json`.

> **LLM agent brain**: for the per-status workflow when this skill returns a `FillConfirmation` (filled / partial / open / error / rejected / submitted), see [`LLM-ORCHESTRATION.md`](../../../LLM-ORCHESTRATION.md) §3. The perps-specific TP-failed-after-stop case is documented there. For idempotency rules on `intent_id` / `--client-order-id`, see §4.

## FillConfirmation shape (output)

After `submit`, the skill emits a `FillConfirmation` whose `bracket`
field carries per-order ids:

```json
{
  "intent_id": "trend-follow-SOLUSD-2026-06-22-001",
  "order_id": "OFILL-OPEN-001",
  "pair": "SOLUSD",
  "side": "sell",
  "order_type": "market",
  "requested_volume": 11.5,
  "filled_volume": 11.5,
  "fill_price": 69.22,
  "cost_quote": 796.03,
  "status": "filled",
  "timestamp": "2026-06-22T15:00:00+00:00",
  "venue": "kraken-perps",
  "bracket": {
    "open_order_id": "OFILL-OPEN-001",
    "stop_order_id": "OFILL-STOP-001",
    "take_profit_order_id": "OFILL-TP-001"
  }
}
```

Key fields:
- `order_id` is the **open** order id; the stop and TP ids are in `bracket`.
- `status` — terminal: `filled` / `partial` / `submitted` / `open` /
  `rejected` / `cancelled` / `expired` / `error`.
- `reason` — populated for rejected/error, including TP-failed warnings
  when stop succeeded but TP didn't.
- `raw` — full submit envelopes for audit.

## Idempotency

`Intent.intent_id` is forwarded as `--client-order-id` to Kraken on the
open order. Kraken enforces uniqueness per `client_order_id` per API key
— a retried intent with the same id returns the original order instead
of placing a duplicate. The stop and TP orders do not carry a
`client_order_id` (Kraken's perps CLI does not accept one on reduce-only
orders); retry idempotency for the protective legs is the caller's
responsibility.

## Risk layer integration

Before any live submission, `submit` validates the Intent against
`analysis.risk.vet`. For `venue="kraken-perps"` (or any intent with a
`leverage` field), `vet` auto-selects the perps policy set in
addition to the spot set:

- `leverage_cap_policy` — REJECT if leverage exceeds the tier cap
  (BTC/ETH/SOL=2x, alts=5x)
- `liquidation_distance_policy` — REJECT if liquidation distance is
  less than 30% of entry (caller must supply `extras.reference_entry`)
- `stop_distance_policy` — REJECT if bracket stop is outside the swing
  bucket (2%–25% of entry)
- `funding_drag_policy` — CONCERN (advisory, non-blocking) if 3-day
  projected funding drag exceeds 1% of notional (caller must supply
  `extras.position_value`; the policy expects `ctx.funding_rate_per_8h`
  in the "this trade pays" sign convention)
- `duplicate_perps_position_policy` — REJECT if a same-pair, same-side
  perps position is already open (caller must populate
  `ctx.open_perps_positions` from `kraken futures positions`)

Spot policies (`position_size`, `portfolio_drawdown`,
`per_tier_exposure`, `daily_budget`, `insufficient_funds`,
`per_pair_cooldown`) also run on perps intents.

The CLI's local `--leverage` cap check is a fast-path duplicate of
`leverage_cap_policy` for early rejection without a full risk vet.

## Bracket model

A perps `place_order` places three orders:

1. **open** — market (or limit) at the trigger price, side matches
   `Intent.side`
2. **stop** — `reduce-only` protective stop at `bracket.stop_loss`,
   side opposite to `Intent.side`
3. **take_profit** — `reduce-only` profit-taking order at
   `bracket.take_profit`, side opposite to `Intent.side`

If the stop fails after a successful open, the provider rolls back by
closing the position at market so the caller is never left with an
unprotected trade. If the take-profit fails after a successful open +
stop, the result is `status="submitted"` with `reason` populated — the
position is still protected by the stop; the operator can place the
TP manually.

## Portfolio wiring

Successful fills (`status="filled"` or `status="partial"`) auto-write a
row to portfolio-mgmt's SQLite DB (`portfolio/db.py:add_transaction`)
when `--portfolio` is supplied. Asset notation is `kraken:<PAIR>`,
matching the spot adapter. Side mirrors the intent direction: `BUY`
for long perps / `SELL` for short perps. The `notes` JSON carries:

- `venue: "kraken-perps"`
- `open_order_id`, `bracket: {stop_order_id, take_profit_order_id}`
- `strategy`, `source_skills`, `thesis`, `intent_id`, `leverage`
- `stop_loss`, `take_profit`
- A structured `decision_context` block (regime, macro signals, L3
  idea summary, risk verdict, override flag). The canonical schema is
  ``analysis/decision.py::DecisionContext``. The trace is also written
  to the ``decisions`` table (system of record in ``portfolio.db``),
  one row per ``intent_id``. The decisions table is idempotent on
  ``intent_id`` — a retried submit with the same id is a no-op,
  preserving the original trace.
  See `portfolio-mgmt` SKILL.md §"decision_context" and the
  `execution-kraken-spot` "decision_context auto-population" section
  for the field-by-field source map.

The LLM supplies the risk verdict / macro snapshot / override flag via
the Intent field `decision_decoration` (or the CLI flag
`--decision-decoration` for one-shot runs):

```bash
uv run skills/execution-kraken-perps/scripts/run.py submit \
  --intent intent.json \
  --decision-decoration '{"regime_label":"RISK_OFF","risk_status":"CONCERN","macro_signals":["fear_panic"]}' \
  --override-from-suggestion
```

Recognised `--decision-decoration` keys: `regime_label`, `regime_fng`,
`regime_btc_dominance`, `regime_divergence`, `macro_signals`,
`risk_status`, `risk_position_size_pct`, `risk_concerns`,
`override_field`, `override_reason`. Unknown keys are passed through
for forward compatibility.

Skip portfolio wiring by omitting `--portfolio`. The skill still prints
the fill confirmation but does not touch the SQLite DB.

## Cron integration

Suggested schedule: on-demand, called by the agent before live
submission. This skill does not poll or schedule itself — the LLM
(or whatever agent drives the workflow) decides when to act.

```bash
# Cron-friendly: --yes skips the prompt, --json gives machine output
uv run skills/execution-kraken-perps/scripts/run.py submit \
  --intent /path/to/intent.json --yes --json \
  | tee /var/log/market-skills/perps-fills/$(date -u +%Y%m%dT%H%M%S).json
```

## Exit codes

- `0` — success (live submit returned, dry-run validated, read-only op
  succeeded)
- `1` — venue error / CLI failure / cancel failed
- `2` — input validation failure (bad intent, missing args, REJECT
  status, leverage cap exceeded)

## Safety checklist before running live

1. `kraken auth status` — confirm API key is configured (the perps
   adapter uses the spot API key by default; some setups require a
   separate futures API key — see `kraken auth` for environment
   variables)
2. Run with `--dry-run` first to see what the bracket looks like and
   what the risk verdict says
3. Start with a tiny `--volume` on a non-critical pair
4. Confirm `--portfolio` resolves to the right portfolio
5. Verify the bracket order ids in the fill confirmation match what
   `kraken futures open-orders` shows before assuming the position is
   live

## Files

```
skills/execution-kraken-perps/
├── SKILL.md                          # this file
├── lib.py                            # pure helpers (intent loading, render, portfolio wiring)
├── scripts/
│   └── run.py                        # CLI (argparse, confirm prompt, dispatch)
└── examples/
    └── intent.example.json           # sample Intent for testing
```
