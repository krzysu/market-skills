# risk-engine — `policies.yaml` configuration reference

Canonical schema for `policies.yaml` (resolved at `skills/risk-engine/data/policies.yaml` by default; see **Resolution** below). The file is
optional; missing/unreadable means "use `RiskContext` class defaults"
(matched by `analysis.risk.load_policy_overrides`).

**Resolution order** (later wins):

1. `RiskContext` class default
2. Top-level scalar in `policies.yaml`
3. Per-portfolio block
4. Per-pair block

**Resolution path:** `--config PATH` CLI flag → `$MARKET_SKILLS_RISK_POLICIES_PATH`
env var → `skills/risk-engine/data/policies.yaml`. A missing or unreadable
file is not an error; malformed YAML or unknown top-level keys raise
`ValueError` (CLI exits 2 with a clean message).

## Schema (all fields optional)

```yaml
# Top-level scalars — apply to all portfolios/pairs unless overridden.
max_position_pct: 30      # float -> ctx.max_position_pct
max_drawdown_pct: 15      # float -> ctx.max_drawdown_pct
daily_budget: 8           # int   -> ctx.daily_trade_budget
cooldown_hours: 6         # float -> ctx.pair_cooldown_hours

# Per-tier exposure caps (merged into ctx.tier_limits as max_pct).
tier_caps:
  tier1: 40
  tier2: 25
  tier3: 10

# Perps-specific risk policy data overrides. These win over the code
# defaults in analysis/providers/execution_kraken_perps.py. Pairs not
# listed here fall through to the code dicts (LEVERAGE_CAPS / MM_RATES)
# or the default (DEFAULT_LEVERAGE_CAP = 5).
perps:
  # Per-pair leverage caps. Integer >= 1. Majors (BTC/ETH/SOL) default
  # to 2x; alts default to 5x. Set this to raise or lower a specific
  # pair's cap without forking the code.
  leverage_caps:
    SOLUSD: 3
    BTCUSD: 5
    HYPEUSD: 2
  # Default leverage cap for pairs not in leverage_caps AND not in the
  # code's LEVERAGE_CAPS dict. Default: 5.
  default_leverage_cap: 10
  # Per-pair first-tier maintenance margin rate (0 < rate < 1). Default
  # comes from MM_RATES in the provider (Kraken's published per-pair
  # first-tier MM). Set this to override a specific pair when the venue
  # changes its margin schedule.
  mm_rates:
    SOLUSD: 0.015
    HYPEUSD: 0.02

# Per-portfolio overrides (case-insensitive name match).
portfolios:
  spot:
    max_position_pct: 25
    cooldown_hours: 8
    tier_caps:
      tier1: 40
      tier2: 25
    perps:
      leverage_caps:
        SOLUSD: 5  # this portfolio is more aggressive on SOL
  defi:
    max_position_pct: 40
    cooldown_hours: 4
    daily_budget: 15
    perps:
      default_leverage_cap: 3  # this portfolio is more conservative on alts

# Per-pair overrides (case-insensitive bare-ticker match — HYPEUSD matches HYPE-USD).
pairs:
  HYPEUSD:
    max_position_pct: 5
```

## Field reference

| YAML key | Type | RiskContext field | Default | Notes |
|----------|------|-------------------|---------|-------|
| `max_position_pct` | float | `max_position_pct` | 25.0 | Single-position size as % of portfolio. `position_size_policy` SCALE/REJECT. |
| `max_drawdown_pct` | float | `max_drawdown_pct` | 20.0 | Portfolio drawdown above this → REJECT. `portfolio_drawdown_policy`. |
| `daily_budget` | int | `daily_trade_budget` | 10 | Max trades per day (UTC). `daily_budget_policy`. |
| `cooldown_hours` | float | `pair_cooldown_hours` | 4.0 | Same pair+side within N hours → CONCERN. `per_pair_cooldown_policy`. |
| `tier_caps` | mapping | `tier_limits` (merged) | `{}` | Per-tier cap as `% of portfolio`. `per_tier_exposure_policy` consults `tier_limits`. |
| `perps` | mapping | `leverage_caps`, `mm_rates`, `default_leverage_cap` (merged) | code dicts | Perps risk policy data. See the `perps:` block above. |
| `portfolios` | mapping | applies via `apply_portfolio_overrides` | n/a | Per-portfolio override block. Case-insensitive name match against `args.portfolio`. Supports a nested `perps:` sub-block. |
| `pairs` | mapping | applies via `apply_pair_overrides` | n/a | Per-pair override block. Bare-ticker match (HYPHENS / slashes stripped, uppercased). |

`RiskContext` is the single source of truth for the field list — adding a
new overridable scalar is a one-line change on the dataclass field via
`field(metadata={"yaml_key": ..., "yaml_coerce": ...})`, and the loader
auto-derives both the global map and the unknown-key validator from the
dataclass metadata. See `analysis/risk.py::_GLOBAL_FIELD_MAP` and
`_POLICY_OVERRIDE_TOP_KEYS`.

## Unknown top-level keys

The loader rejects any top-level key not in the schema:

```yaml
sneaky_knob: true
```

raises `ValueError: unknown top-level keys in policy file …: ['sneaky_knob']`.
This is the defensive gate against typos like `max_position` (missing
`_pct`) silently becoming a no-op.

## Application order

```python
# skills/risk-engine/scripts/run.py
ctx = _lib.build_context(args)            # 1. portfolio + watchlist + prices
overrides = load_policy_overrides(args.config)  # 2. parse YAML
apply_global_overrides(ctx, overrides)         # 3. top-level scalars
apply_portfolio_overrides(ctx, overrides, args.portfolio)  # 4. per-portfolio
apply_pair_overrides(ctx, overrides, intent["pair"])       # 5. per-pair
verdict = vet(intent, ctx)                          # 6. policy evaluation
```

Each `apply_*_overrides` mutates `ctx` in place. The integration test
`tests/test_risk_engine.py::TestBuildContext::test_overrides_apply_before_fragment_generation`
verifies a 28% intent SCALES to the per-portfolio 25% cap, not the global
30%, confirming the order is correct.

## Per-portfolio matching

`apply_portfolio_overrides` matches the YAML key against `args.portfolio`
case-insensitively. `portfolios.spot` matches `--portfolio spot`,
`--portfolio SPOT`, and the portfolio name returned by `portfolio.db`
regardless of input casing. No match → block is silently skipped (the
absence of a per-portfolio override is not an error).

## Per-pair matching

`apply_pair_overrides` strips hyphens, slashes, and lowercases the YAML
key for comparison against `intent["pair"]`. `pairs.HYPE-USD` and
`pairs.HYPEUSD` both match a `HYPEUSD` intent. No match → block is
silently skipped.
