# Market Skills — Architecture

> Dated design decisions live in [`docs/adr/`](./docs/adr/README.md).
> This document describes **what the system is**. ADRs describe
> **why we built it this way**.

## Output conventions

Every skill's `--json` mode emits the canonical AXI envelope
(ADR-0004): `{data, count, errors, help[]}`. The envelope is the
on-the-wire contract; the in-process TypedDicts
(`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal` /
`RiskVerdict` / `FillConfirmation` / `Intent`) describe the lib.py
contracts and are unchanged.

| Layer | Contract module | Reference |
|-------|-----------------|-----------|
| On-the-wire envelope | `analysis.output` | [`docs/AXI-REFERENCE.md`](./AXI-REFERENCE.md) |
| In-process TypedDicts | `analysis.contracts` | `L1Result`, `L2Result`, `L3Result`, `RiskVerdict`, `FillConfirmation` |
| Failure-mode workflow | (doc only) | [`LLM-ORCHESTRATION.md`](./LLM-ORCHESTRATION.md) |

TOON is opt-in behind `--toon` (phase 5). The default is indent-2
JSON. Migration to TOON-by-default is gated on a measured
>30% token saving across the phase-1 pilot.

## Status

A composable technical-analysis + execution stack. L1 indicator skills,
L2 pattern/composite verdicts, L3 strategy ideas, an advisory Risk
layer, Kraken execution, portfolio tracking, and per-user config/notes
— all wired via the Agent Skills spec so any LLM agent can call them
as tools.

The LLM is the agent brain (see [ADR-0002](./docs/adr/0002-llm-as-agent-brain.md)):
it reads `SKILL.md`, calls skills as tools, narrates results, asks the
user, and (with explicit approval) calls the execution skill whose
interactive confirm is the actual safety layer. Cron usage is
analytics-only (`run-all-l3`, `position-watchdog`).

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│                     AGENT BRAIN (LLM)                       │
│  Reads SKILL.md → calls skills → narrates → user confirms   │
└───────┬─────────────────────────────────┬───────────────────┘
        │                                 │
        ▼                                 ▼
┌──────────────────┐               ┌──────────────────────┐
│   L3 Strategies  │               │   Batch runners      │
│  strategy-*      │               │  run-all-l2/l3       │
│  {ideas,         │               │  run-watchlist       │
│   narrative}     │               └──────────┬───────────┘
└────────┬─────────┘                          │
         │                                    │
         └─────────────┬──────────────────────┘
                       │
┌──────────────────────▼─────────────────┐
│   L2 Skills                            │
│  market-*  {pattern, signals, ...}     │
└────────┬───────────────────────────────┘
         │
┌────────▼───────────────────────────────┐
│   L1 Skills                            │
│  market-*  {score, signal, zone}       │
└────────┬───────────────────────────────┘
         │
┌────────▼───────────────────────────────┐
│   analysis/ package                    │
│   indicators/  contracts.py           │
│   conviction_thresholds.py            │
│   track_record.py  data.py            │
│   providers/*                         │
└────────────────────────────────────────┘

Sidecars: Risk (advisory vet) · Execution (Kraken spot + perps)
          · Portfolio (SQLite) · Config · Notes · Monitoring
```

## Event flow

Every layer produces a typed event consumed by the next. Layers never
import each other; the LLM bridges them by reading one event,
reasoning, and (with user approval) calling the next skill.

- Analysis → `MarketVerdict{pair, rsi, trend, pattern, score, ...}`
- Macro → `RegimeSignal{timestamp, inputs, regime, errors, regime_note}` — 6 inputs (F&G, VIX, DXY, US10Y, BTC.D, total mcap), 3-axis derived labels (risk_appetite / liquidity / sentiment). Lives in `analysis/macro/` — fetches external cross-asset state.
- Valuation → `ValuationSignal{timestamp, inputs, regime, errors, regime_note}` — SP500 spot + Shiller CAPE vs 50y mean/std → z-score + 5-band regime (OVEREXTENDED / ELEVATED / FAIR / DEPRESSED / OVERSOLD). Lives in `analysis/valuation.py`. Same best-effort + error-isolated contract as Macro. Narrate-only; consumed as a soft `veto_reasons` tag by `strategy-mean-reversion` when CAPE disagrees with the trade direction.
- Conviction calibration → `chop_score{timestamp, ideas, score, window}` — fraction of recent L3 ideas at conviction ≤ 2. Lives in `analysis/chop.py` (was `regime.py` until the Macro/Regime name collision was resolved). Reads the L3 idea history store, not external market data.
- Conviction gate → `lookup_min_conviction(strategy, ticker, interval) -> int` — per-(strategy, ticker, interval) minimum-conviction-to-emit floor for L3 ideas. Lives in `analysis/conviction_thresholds.py`; `MIN_CONVICTION_TO_EMIT_BY_STRATEGY` holds the overrides (loaded from `$MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH` so per-tuner evidence stays outside the open-source repo), `GLOBAL_MIN_CONVICTION_TO_EMIT = 1` is the no-op default. Read by `strategy-trend-follow` and `strategy-liquidity-sweep` at the end of `analyze()` to drop ideas whose conviction is below the threshold before emit.
- Track record → `TrackRecord{hit_rate, n_closed, n_hits, n_misses, avg_return_pct, multiplier, eligible}` — per-ticker hit-rate signal from the DTP journal. Lives in `analysis/track_record.py`. Read-only, no I/O, no caching; consumer passes the parsed `picks.json` array. The `multiplier` (1.0–3.0) scales `suggested_size_eur` for picked ideas with a strong recent track record.
- Strategy → `TradeIdea{pair, direction, entry_zone, stop, target, conviction, version, strategy_name}`
- Risk → `RiskVerdict{intent_id, status, fragments[], concerns[], narrative_hint}` — advisory
- Execution → `FillConfirmation{order_id, fill_price, volume, fee, venue, timestamp, intent_id, status}`

Strategy subscribes to MarketVerdict + RegimeSignal. Risk reads from Portfolio + Config. Portfolio subscribes to
FillConfirmation; execution never calls Portfolio directly — fills wire through the skill wrapper.

The LLM agent brain follows the failure-mode contract documented in [`LLM-ORCHESTRATION.md`](./LLM-ORCHESTRATION.md) (per-`RiskVerdict`-status workflow, per-`FillConfirmation`-status workflow, idempotency rules for `intent_id`, the things-you-must-NEVER list). The LLM is the only layer that sees the whole picture; the skills are deterministic building blocks.

## L3 vs the agent brain

| L3 Skills                            | Agent Brain                     |
| ------------------------------------ | ------------------------------- |
| "Here's an idea given these candles" | "Should I act on this idea?"    |
| Pure analysis → intent               | Intent → decision → execution   |
| Stateless or reads portfolio         | Has memory, planning, tool-use  |
| Deterministic, testable              | Reasons about conflicting ideas |
| One strategy per skill               | Orchestrates multiple L3s       |

Both exist. L3s are composable strategy building blocks. The agent
brain orchestrates them. This repo doesn't own the agent brain; it
provides the skills it calls.

## Domain boundaries

| Domain     | Input                                                   | Output                        | State                                                                                | Status                                                                                        |
| ---------- | ------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Analysis   | OHLC candles                                            | `MarketVerdict` per pair      | Stateless                                                                            | **Built** (L1 + L2)                                                                           |
| Macro      | F&G + VIX/DXY/US10Y (yfinance) + total mcap (CoinGecko) | `RegimeSignal`                | TTL cache + ring buffer (`$XDG_DATA_HOME/market-skills/macro_history.json`, 200-cap) | **Built** (6 inputs, 3-axis regime, narrate-only — `run-all-l3` attaches `macro` to envelope) |
| Valuation  | Shiller CAPE (multpl.com) + SP500 spot (yfinance)       | `ValuationSignal`             | TTL cache + ring buffer (`$XDG_DATA_HOME/market-skills/valuation_history.json`, 200-cap) | **Built** (CAPE z-score + 5-band regime, narrate-only — `strategy-mean-reversion` reads for soft `veto_reasons` tag) |
| Strategy   | `MarketVerdict[]` + `RegimeSignal` + `ValuationSignal` + Config | `TradeIdea[]`         | Optional                                                                             | **Built** (L3 + run-all-l3)                                                                   |
| Risk       | `Intent` + Portfolio + Watchlist + Recent trades        | `RiskVerdict`                 | Reads Portfolio                                                                      | **Built** — advisory, not a hard gate                                                         |
| Execution  | `Intent`                                                | `FillConfirmation`            | Connection                                                                           | **Built** for Kraken (spot + perps)                                                           |
| Portfolio  | `FillConfirmation`                                      | Balance, P&L, history         | **SQLite**                                                                           | **Built** (multi-portfolio, FIFO, reconcile)                                                  |
| Config     | Watchlist JSON                                          | Baskets of tickers + metadata | File on disk                                                                         | **Built** (market-watchlist + run-watchlist)                                                  |
| Notes      | Notes JSON                                              | Per-pair thesis notes         | File on disk                                                                         | **Built** (market-notes)                                                                      |
| Monitoring | Watches JSON + Portfolio                                | Alert events                  | Per-watch state                                                                      | **Built** (position-watchdog, manual confirm only)                                            |
| Backtest  | OHLC candles + L3 ideas                                | Metrics + benchmark          | Stateless (windowed folds)                                                           | **Built** (backtest-engine: FillSimulator + `compute` + `WalkForwardRunner`; analytics-only, never execution) |

## Key design choices

Dated decisions are tracked as ADRs ([`docs/adr/`](./docs/adr/README.md)).
The bullets below are descriptive of how the system is built, not
"we chose X" — they read as the current state of the codebase.

- **Risk is advisory.** `risk-engine` returns a `RiskVerdict` the LLM
  narrates; the execution skill's interactive confirm is the safety
  layer that is never bypassed silently.
- **Portfolio is SQLite.** One file, zero infra, but queryable via
  SQL — multi-portfolio, FIFO cost basis, P&L, replay, reconcile.
- **Providers are protocol-based.** `DataProvider` and
  `ExecutionProvider` Protocols in `analysis/providers/` — add a venue
  by implementing one and registering it. Same pattern for data and
  execution.
- **L1/L2/L3 are venue-agnostic.** Indicators (RSI, EMA, MACD, squeeze,
  etc.) and patterns (breakout, accumulation, exhaustion, sweep,
  trend-quality) work the same on spot and perps OHLC. Perps-specific
  data — funding rate, spot-perp basis, open interest, liquidations —
  is a different signal class (venue-state, not price-derived) and
  lives in the sidecar skill `market-basis` rather than a layer in the
  L1/L2/L3 taxonomy. L3 strategies are not auto-integrated with
  `market-basis`; the LLM runs it separately during narration so the
  user can weigh funding drag / basis against the setup conviction.
- **Perps risk-vet context is auto-fetched by `risk-engine`.** When the
  intent's `venue` ends in `-perps` and `--perps-account` is set,
  `risk-engine.build_context` shells out to `kraken futures` for open
  positions and current funding rate; MM rate comes from the static
  `MM_RATES` table in the perps provider. The LLM doesn't gather
  perps state itself — the same shape as `--portfolio` for spot
  context. CLI override flags (`--funding-rate-per-8h`,
  `--maintenance-margin-rate`, `--open-perps-positions`) exist for
  testing and for callers that source state elsewhere.

## Extensibility

- **New indicator** → function in `analysis/indicators/`, optionally wrap as L1 skill.
- **New pattern** → L2 skill in `skills/market-{name}/`. Compose L1s via the cached skill loader. Return `{pattern, signals, input_scores, narrative}`.
- **New strategy** → L3 skill in `skills/strategy-{name}/`. Compose L2s. Return `{ideas, narrative}`.
- **New read-side signal** → pure-function module in `analysis/` like `analysis/track_record.py`. No I/O, no registration required. Callers pass parsed data in; the function returns a TypedDict.
- **New exchange data** → implement `DataProvider`, register in the data registry.
- **New exchange execution** → implement `ExecutionProvider`, register in the execution registry.
- **New risk policy** → add function to `analysis/risk/spot.py` (spot
  policies) or `analysis/risk/perps.py` (perps policies, must
  short-circuit on spot intents), then include in the corresponding
  `SPOT_POLICIES` or `PERPS_POLICIES` list. `vet()` picks the right
  set per intent venue automatically.

No file outside the new skill/module needs to change (except the relevant registry).

## Build status

All domains in the table above are **Built**. The LLM-orchestration
failure-mode contract is documented in [`LLM-ORCHESTRATION.md`](./LLM-ORCHESTRATION.md).
