---
name: risk-engine
description: "Advisory risk-vetting for trade Intents. Pure function `analysis.risk.vet()` evaluates position size, portfolio drawdown, per-tier exposure, daily trade budget, available funds, and per-pair cooldown — returns a RiskVerdict (APPROVED / CONCERN / SCALE / REJECT) the LLM narrates before asking the user to confirm execution. Not a hard gate; the execution skill confirm is the actual safety layer."
version: 0.1.0
metadata:
  hermes:
    tags: [risk, vet, advisory, portfolio]
    category: risk
compatibility: "Requires Python 3.12+, uv, portfolio-mgmt SQLite DB (optional but recommended)"
---

# risk-engine

Advisory risk-vetting layer between analytics and execution. The LLM calls
this skill *before* asking the user to confirm an order. The verdict is
**advisory, not a hard gate** — the LLM can recommend an override based
on context the policies didn't see, but the human-in-the-loop at
`execution-kraken-spot` is the actual safety layer.

## Why advisory not gate

The LLM is the agent brain (see ARCHITECTURE.md "Agent brain"). A
deterministic REJECT here would prevent the LLM from exercising judgment
the policies can't model (e.g. the user is closing a hedge, the policy
only sees the long). So Risk.vet returns a recommendation; the LLM
narrates it; the user decides; the execution skill enforces the human
confirm.

## Quick Start

```bash
# Vet an Intent from file (machine input, JSON for LLM tool-use)
uv run skills/risk-engine/scripts/run.py --intent intent.json --portfolio spot --json

# Vet direct flags (interactive use)
uv run skills/risk-engine/scripts/run.py \
  --pair HYPEUSD --side buy --order-type limit --volume 1.5 --limit-price 60.15 \
  --portfolio spot

# Without portfolio context — policies degrade to "no info" (CONCERN at worst)
uv run skills/risk-engine/scripts/run.py --intent intent.json
```

> **LLM agent brain**: for the per-status workflow when this skill returns a `RiskVerdict` (APPROVED / CONCERN / SCALE / REJECT), see [`LLM-ORCHESTRATION.md`](../../../LLM-ORCHESTRATION.md) §2.

## What the policies check

Eleven composable policies, each returns a `RiskVerdictFragment`. The
spot set (first six) runs on every intent; the perps set (last five)
runs only when the intent's `venue` ends in `-perps` or has a
`leverage` field. Each perps policy short-circuits on spot intents
so they can be safely mixed.

| Policy | Set | Trigger | Worst status | Notes |
|--------|-----|---------|--------------|-------|
| `position_size` | spot | intent cost > `max_position_pct` of portfolio | SCALE / REJECT | SCALE shrinks to cap; REJECT only if 3× over |
| `portfolio_drawdown` | spot | drawdown > `max_drawdown_pct` | CONCERN / REJECT | Defensive posture — explain before adding risk |
| `per_tier_exposure` | spot | projected tier exposure > cap | SCALE | Tier resolved from watchlist metadata |
| `daily_budget` | spot | trades today + 1 > budget | CONCERN / REJECT | Resets at midnight UTC |
| `insufficient_funds` | spot | cash < cost (buy) / held < qty (sell) | REJECT | Hard reject — no narrative needed |
| `per_pair_cooldown` | spot | same pair+side within `cooldown_hours` | CONCERN | Pure advisory; DCA plans legitimately trade often |
| `leverage_cap` | perps | `intent.leverage` > tier cap (BTC/ETH/SOL=2x, alts=5x) | REJECT | Tier-aware cap from `LEVERAGE_CAPS` |
| `liquidation_distance` | perps | liq price < `liq_min_distance_pct` from entry (30%) | REJECT | Linear approx: `move = 1/lev + mm_rate`; needs `extras.reference_entry` + `ctx.maintenance_margin_rate` |
| `stop_distance` | perps | bracket stop < 2% or > 25% of entry | REJECT | Swing-mode bucket; needs `extras.reference_entry` |
| `funding_drag` | perps | 3-day funding drag > `funding_warn_pct` of notional | CONCERN | Sign convention: positive = this trade pays; needs `ctx.funding_rate_per_8h` + `extras.position_value` |
| `duplicate_perps_position` | perps | open position on same pair+side | REJECT | Pyramiding guard; needs `ctx.open_perps_positions` |

`vet()` aggregates fragments by worst-case status. SCALE suggestions are
combined by taking the smallest suggested volume (most conservative).

## Context snapshot

The skill builds a `RiskContext` from:

| Source | Fields |
|--------|--------|
| `portfolio-mgmt` (`--portfolio`) | positions (via `compute_positions`), base_ccy, daily trades; **prices** read from the SQLite `price_cache` table (`get_cached_prices`), refreshed by `portfolio.db.refresh_prices()` |
| `market-watchlist` (`--watchlist`) | tier metadata per pair → tier exposure |
| `--drawdown-pct` (or 0.0) | portfolio drawdown override |
| `kraken futures` (`--perps-account`) | open perps positions, current funding rate — only when intent's `venue` ends in `-perps` |
| Static `MM_RATES` (in `analysis/providers/execution_kraken_perps.py`) | first-tier maintenance margin rate for the intent's pair — only when intent is perps |
| CLI overrides | `--funding-rate-per-8h`, `--maintenance-margin-rate`, `--open-perps-positions` — win over auto-fetch (testing, custom sourcing) |
| Built-in defaults | `max_position_pct=25`, `max_drawdown_pct=20`, `daily_trade_budget=10`, `pair_cooldown_hours=4`, perps thresholds `liq_min_distance_pct=30`, `stop_min_distance_pct=2`, `stop_max_distance_pct=25`, `funding_warn_pct=1.0` |

Without `--portfolio`, policies degrade to "no info" → APPROVED with the
relevant `CONCERN` fragment (e.g. position_size emits CONCERN if no
portfolio total_value to size against).

### Perps context

When the intent's `venue` ends in `-perps` (or `intent.leverage` is
set) the perps policy set is added to the spot set. Three
`RiskContext` fields are perps-specific:

| Field | Source | Notes |
|-------|--------|-------|
| `open_perps_positions` | `kraken futures positions --output json` (auto) or `--open-perps-positions <json>` (override) | List of `{symbol, size}` (positive=long, negative=short). `None` / empty means the duplicate-position policy is a no-op. |
| `funding_rate_per_8h` | `kraken futures historical-funding-rates <PF_SYMBOL>` (auto, takes most-recent entry) or `--funding-rate-per-8h` (override) | Sign convention: positive = this trade pays. Longs pay when funding > 0, shorts pay when funding < 0. The auto-fetch flips the sign for shorts. |
| `maintenance_margin_rate` | Static `MM_RATES` table (auto) or `--maintenance-margin-rate` (override) | First-tier MM (smallest notional, most conservative). Higher-notional-tier MM is a few percent looser — out of scope for the policy's lower bound. |

Without `--perps-account` AND without overrides, all three fields
default to `None` / empty. The perps policies degrade to no-info
paths: `duplicate_perps_position` is a no-op when there are no
positions; `funding_drag` emits a CONCERN asking the caller to load
the rate; `liquidation_distance` and `stop_distance` emit CONCERN
when their required inputs (mm rate / reference entry) are missing.
No perps policy ever REJECTs on missing context.

`--perps-account` only triggers the auto-fetch when the intent is
perps — for spot intents, the perps branch is fully skipped.

### Price source

Prices come from portfolio-mgmt's price cache (the same data the rest of
the portfolio UI consumes). `portfolio-mgmt prices refresh` should run on
cron; risk-engine then reads the cache directly. For cache misses,
risk-engine falls back to a one-shot live fetch (`fetch_spot_price`) so
a cold cache doesn't regress a hot one. Pass `--refresh-prices` to force
a refresh before vetting (rare — usually only for ad-hoc verification).

## Configuration

Risk-engine parameters (`max_position_pct`, `max_drawdown_pct`, `daily_budget`,
`cooldown_hours`, `tier_caps`) ship with sane defaults from `RiskContext`.
Override them per portfolio / per pair via a YAML config file. Resolution
matches the rest of the skill config loaders:

1. `--config PATH` CLI flag
2. `$MARKET_SKILLS_RISK_POLICIES_PATH` env var
3. `skills/risk-engine/data/policies.yaml` (repo default — gitignored)

A missing or unreadable file is not an error — `RiskContext` defaults are
used. Malformed YAML or unknown top-level keys raise `ValueError` (CLI
exits with code 2 and a clean message).

### Schema

```yaml
# Top-level scalars — apply to all portfolios/pairs unless overridden below.
max_position_pct: 30      # -> ctx.max_position_pct
max_drawdown_pct: 15      # -> ctx.max_drawdown_pct
daily_budget: 8           # -> ctx.daily_trade_budget
cooldown_hours: 6         # -> ctx.pair_cooldown_hours

# Per-tier exposure caps (merged into ctx.tier_limits as max_pct).
tier_caps:
  tier1: 40
  tier2: 25
  tier3: 10

# Per-portfolio overrides (case-insensitive name match).
portfolios:
  spot:
    max_position_pct: 25
    cooldown_hours: 8
    tier_caps:
      tier1: 40
      tier2: 25
  defi:
    max_position_pct: 40
    cooldown_hours: 4
    daily_budget: 15

# Per-pair overrides (case-insensitive bare-ticker match — <PRIVATE_PERP>-USD matches <PRIVATE_PERP>USD).
pairs:
  <PRIVATE_PERP>USD:
    max_position_pct: 5
```

### Precedence

For each scalar field, the value is resolved as:
**class default → top-level → per-portfolio → per-pair** (later wins).
This matches the spec at `POLICIES_CONFIG.md`.

## LLM tool use

The skill is shaped as an LLM tool. Before asking the user to confirm an
order, the LLM should:

1. Build an Intent (or read one from an L3 strategy output).
2. Call `risk-engine`:
   - **Spot**: `risk-engine --intent <intent> --portfolio spot --json`
   - **Perps**: `risk-engine --intent <intent> --portfolio spot --perps-account kraken-futures --json` (the `--perps-account` flag triggers auto-fetch of open positions + funding rate from `kraken futures`; MM rate comes from the static table)
3. Read the verdict JSON. Narrate the `narrative_hint` field and any
   REJECT/SCALE concerns.
4. Ask the user: "Risk says X. Should I proceed?"
5. If yes, call ``execution-kraken-spot submit` or `execution-kraken-perps submit` --intent <intent>` which prints
   the order summary and asks for the user's explicit `y/N`.

Example system-prompt fragment for the LLM:

> Before executing any trade, vet the Intent with `risk-engine`. For
> perps Intents, add `--perps-account kraken-futures` so the perps
> policies (leverage cap, liquidation distance, stop distance, funding
> drag, duplicate position) can evaluate against live state. Surface
> any REJECT or SCALE verdicts to the user before asking for
> confirmation. Risk is advisory — you may recommend an override if
> the user explains context the policies didn't see. The actual
> safety gate is the execution skill confirm; never bypass it.

## Adding a new policy

Each policy is a function `(Intent, RiskContext) -> RiskVerdictFragment`.
Add spot policies to `analysis/risk/spot.py` and perps policies to
`analysis/risk/perps.py` (perps policies must short-circuit on spot
intents via `_is_perps_intent`). To add e.g. a "concentration check"
(no single position > 50% of portfolio) as a spot policy:

```python
# analysis/risk/spot.py
def concentration_policy(intent, ctx):
    asset = f"kraken:{intent['pair']}"
    held_pct = (ctx.positions.get(asset, {}).get("market_value", 0) / ctx.total_value) * 100
    if ctx.total_value <= 0:
        return _empty_fragment("concentration")
    if held_pct > 50:
        return RiskVerdictFragment(
            policy="concentration",
            status="CONCERN",
            reason=f"{asset} would be {held_pct:.0f}% of portfolio after this trade",
        )
    return _empty_fragment("concentration")

SPOT_POLICIES.append(concentration_policy)
```

No changes to `vet()` or the contract. Tests can isolate the new policy
by passing `policies=[concentration_policy]` to `vet()`.

## Verdict schema

```json
{
  "intent_id": "...",
  "pair": "HYPEUSD",
  "side": "buy",
  "status": "SCALE",
  "fragments": [
    {"policy": "position_size", "status": "SCALE", "reason": "...",
     "suggested_volume": 1.2, "detail": {"pct": 27.5, "max_pct": 25.0}},
    {"policy": "insufficient_funds", "status": "APPROVED", "reason": "no objection"},
    ...
  ],
  "concerns": ["per_pair_cooldown: already traded HYPEUSD buy within 4h cooldown"],
  "suggested_volume": 1.2,
  "narrative_hint": "Risk layer suggests reducing volume to 1.200000 HYPEUSD: ..."
}
```

## References

- [`references/sentiment-vs-structure.md`](references/sentiment-vs-structure.md) — judgment
  playbook for when extreme F&G is a valid contrarian signal versus a
  trailing indicator (structure already broken). Consult before overriding
  a risk verdict on sentiment grounds.

## Files

```
skills/risk-engine/
├── SKILL.md                          # this file
├── lib.py                            # build_context, render_verdict (pure helpers)
├── scripts/
│   └── run.py                        # CLI wrapper
├── examples/
│   └── intent.example.json
└── references/
    └── sentiment-vs-structure.md     # judgment playbook
```
