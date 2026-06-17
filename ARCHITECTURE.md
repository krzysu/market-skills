# Market Skills — Architecture

## Status

This project is an **analysis layer** — L1 indicator skills and L2 pattern/composite verdict skills, plus a data-fetching layer. It does not execute trades, manage positions, or make decisions. It provides the building blocks an agent (or human, or cron) calls for market context.

The architecture below describes the **long-term domain design**, with each layer's current status marked.

## Seven domains

```
┌─────────────────────────────────────────────────────────────┐
│                        CONFIG                               │
│          Schema-validated settings (tiers, budget,           │
│          venue keys, strategy params)                       │
│          ─── consumed by every other domain ───             │
│          ▶ NOT BUILT — planned as analysis/config.py             │
└─────────────────────────────────────────────────────────────┘
       │                         │                         │
       ▼                         ▼                         │
┌──────────────┐      ┌──────────────────┐                 │
│   ANALYSIS   │      │      MACRO       │                 │
│  per-pair    │      │  cross-asset     │                 │
│  indicators  │      │  regime context  │                 │
│  patterns    │      │  F&G, VIX, DXY   │                 │
│  verdicts    │      │  divergence      │                 │
│  ▶ L1 + L2   │      │  ▶ NOT BUILT     │                 │
│    BUILT     │      └────────┬─────────┘                 │
└──────┬───────┘               │                           │
       │                       │                           │
       └───────────┬───────────┘                           │
                   ▼                                       │
          ┌────────────────────┐                           │
          │  ▶ L3 STRATEGY     │                            │
          │  turns verdicts +  │                            │
          │  regime into ideas │                            │
          │  ▶ NOT BUILT       │                            │
          │  (next priority)   │                            │
          └────────┬───────────┘                            │
                   ▼                                       │
          ┌────────────────────┐                            │
          │      RISK          │                            │
          │  sizing, policies, │                            │
          │  budget, exposure  │                            │
          │  → APPROVED /      │                            │
          │    SCALED / REJECT │                            │
          │  ▶ NOT BUILT       │                            │
          └────────┬───────────┘                            │
                   ▼                                       │
          ┌────────────────────┐                  ┌─────────┴──────┐
          │    EXECUTION       │                  │  PORTFOLIO     │
          │  place orders on   │                  │  state, P&L,   │
          │  venue (Kraken,    │◄────fills───────│  history,      │
          │  Hyperliquid, etc) │                  │  stats         │
          │  ▶ NOT BUILT       │                  │  ▶ NOT BUILT   │
          └────────────────────┘                  └────────────────┘
```

### What's built (Analysis + Strategies)

```
┌──────────────────────────────────────────────────┐
│  AGENT (external — Hermes, cron, human, etc.)    │
│  Calls skills as tools. Decides what to analyze. │
└───────┬──────────────────────────────────┬────────┘
        │ calls                            │ calls
        ▼                                  ▼
┌──────────────────┐    ┌──────────────────────────────┐
│   L3 Strategies  │    │   Batch runners              │
│  strategy-*      │    │  run-all-l2  run-all-l3      │
│  {ideas,         │    │  fetch once → run all skills │
│   narrative}     │    └──────────────┬───────────────┘
└────────┬─────────┘                   │
         │                             │
         └──────────┬──────────────────┘
                    │
┌───────────────────▼──────────────────┐
│   L2 Skills                          │
│  market-*  {pattern, signals, ...}   │
└────────┬─────────────────────────────┘
         │
┌────────▼─────────────────────────────┐
│   L1 Skills                          │
│  market-*  {score, signal, zone}     │
└────────┬─────────────────────────────┘
         │
┌────────▼─────────────────────────────┐
│   analysis/indicators.py  pure math       │
│   analysis/contracts.py   TypedDicts      │
│   analysis/data.py        data fetch      │
│   analysis/providers/*    exchanges       │
└──────────────────────────────────────┘
```

## Core principle: Event flow, not import chains

Every domain produces a standardised **event** consumed by the next domain.
They never import each others' code.

```
Analysis   →  MarketVerdict{pair, rsi, trend, pattern, score, …}
Macro      →  RegimeSignal{fng, vix, dxy, divergence, regime_note}
Strategy   →  TradeIdea{pair, direction, entry_zone, stop, target, conviction, strategy_name}
Risk       →  ApprovedIntent{pair, side, size, order_type, stop, target, max_slippage}
Execution  →  FillConfirmation{txid, fill_price, volume, fee, venue, timestamp, intent_id}
```

Portfolio subscribes to FillConfirmation. It never calls Execution.
Strategy subscribes to MarketVerdict + RegimeSignal. It never calls Analysis.
Risk reads from Portfolio (current positions + drawdown) and Config (budget, limits).

## L3 — Strategy Skills (built)

L3 strategy skills follow the same pattern as L2 but output trade ideas:

```python
# skills/strategy-trend-follow/lib.py

from analysis.skill_loader import load_skill

def analyze(candles, *, ticker, interval="1d", period="1y"):
    # Compose L2 verdicts (load_skill is @functools.cache'd internally)
    trend_q = load_skill("market-trend-quality").analyze(candles, interval=interval, period=period)
    accum = load_skill("market-accumulation").analyze(candles, interval=interval, period=period)

    # Apply entry/exit rules
    if trend_q["pattern"]["present"] and accum["pattern"]["present"]:
        return {
            "ideas": [
                {
                    "pair": ticker,
                    "direction": "long",
                    "conviction": min(trend_q["pattern"]["confidence"], accum["pattern"]["confidence"]),
                    "entry_price": ...,
                    "entry_range": [...],
                    "stop_loss": ...,
                    "take_profit": [...],
                    "reasoning": "high-quality trend + accumulation",
                    "source_skills": ["market-trend-quality", "market-accumulation"],
                }
            ],
            "narrative": "High-quality uptrend with accumulation — long bias.",
        }
    return {"ideas": [], "narrative": "No setup triggered."}
```

### L3 vs agent brain

| L3 Skills | Agent Brain |
|-----------|-------------|
| "Here's an idea given these candles" | "Should I act on this idea?" |
| Pure analysis → intent | Intent → decision → execution |
| Stateless or reads portfolio | Has memory, planning, tool-use |
| Deterministic, testable | Reasons about conflicting ideas |
| One strategy per skill | Orchestrates multiple L3s |

Both exist. L3s are composable strategy building blocks. The agent brain (Hermes, cron, human with `--json`, custom loop) orchestrates them. This repo doesn't own the agent brain; it provides the skills it calls.

## Provider Protocol (two interfaces)

```python
class DataProvider(Protocol):
    """OHLC data. Used by Analysis + Macro. ▶ BUILT for HL, Kraken, YFinance, CCXT."""
    def fetch_ohlc(self, ticker: str, interval: str, period: str) -> list[list]: ...
    def supports(self, ticker: str) -> bool: ...

class ExecutionProvider(Protocol):
    """Order placement. ▶ NOT BUILT — planned."""
    def place_order(self, intent: ApprovedIntent) -> FillConfirmation: ...
    def get_balance(self) -> dict[str, float]: ...
    def get_open_orders(self) -> list: ...
    def cancel_order(self, order_id: str) -> bool: ...
```

## Domain boundaries

| Domain | Input | Output | State | Status |
|--------|-------|--------|-------|--------|
| Analysis | OHLC candles (`DataProvider`) | `MarketVerdict` per pair | None (stateless) | **BUILT** (L1 + L2) |
| Macro | F&G API + yfinance | `RegimeSignal` | None | Not built |
| Strategy | `MarketVerdict[]` + `RegimeSignal` + Config | `TradeIdea[]` | Optional: last decision per pair | **BUILT** (L3 + run-all-l3) |
| Risk | `TradeIdea` + Portfolio state + Config | `ApprovedIntent / SCALED / REJECT` | None (reads Portfolio) | Not built |
| Execution | `ApprovedIntent` | `FillConfirmation` | Connection state | Not built |
| Portfolio | `FillConfirmation` (any provider) | Balance, P&L, history | **SQLite** | Not built |
| Config | YAML env files | pydantic-validated dict | File on disk | Not built |

## Why SQLite for Portfolio

- One file, zero infra. Flat file, no server, but still queryable via SQL.
- `SELECT ticker, SUM(CASE WHEN side='buy' THEN cost ELSE 0 END) - SUM(CASE WHEN side='sell' THEN cost ELSE 0 END) AS net_deployed FROM fills WHERE timestamp > $baseline GROUP BY ticker` — replaces 200 lines of FIFO logic in `portfolio.py`.
- Easy to export, easy to inspect, survives restarts.
- Can add a `strategy_name` column and ask "what's my per-strategy P&L?" without restructuring.

## Hermes integration (planned)

Each domain becomes one or more skills callable by a Hermes agent:

| Skill | Domain | Cron potential |
|-------|--------|----------------|
| `market-*` (L1/L2) | Analysis | Nightly scan, deltas |
| `market-macro` | Macro | Pre-market context |
| `strategy-dca` | Strategy | Run after analysis |
| `strategy-perps` | Strategy | Run after analysis |
| `strategy-trim` | Strategy | Run after analysis |
| `risk-engine` | Risk | Run before execution |
| `execution-kraken` | Execution | On demand (user confirm) |
| `execution-hl` | Execution | On demand (user confirm) |
| `portfolio-mgmt` | Portfolio | On demand (user query) |
| `trading-config` | Config | Read-only from other skills |

## Extensibility model

- **New indicator**: add function in `analysis/indicators.py`, optionally wrap as L1 skill.
- **New pattern**: create L2 skill in `skills/market-{name}/`. Compose L1s via `_load_l1_skill()`. Return `{pattern, signals, input_scores, narrative}`.
- **New strategy**: create L3 strategy in `skills/strategy-{name}/`. Compose L2s via `_load_l2_skill()`. Return `{ideas, narrative}`.
- **New exchange data**: implement `DataProvider`, register in `_REGISTRY` and `_PREFIX_MAP`.
- **New exchange execution**: implement `ExecutionProvider`, add to execution registry.
- **New risk policy**: add function to `analysis/risk.py`, compose in `vet()`.

No file outside the new skill/module needs to change (except registries for providers).

## Build order

- [x] **L3 strategy skills** (6 built: trend-follow, mean-reversion, breakout-confirm, accumulation-swing, exhaustion-fade, liquidity-sweep)
- [x] **Batch runners** (`run-all-l2`, `run-all-l3`) — fetch once, run all skills in-process
- [ ] **Portfolio** (`analysis/portfolio.py`) — standalone SQLite module. Skills and the agent brain query it for position context.
- [ ] **Execution provider protocol** + paper mode — same pattern as data providers.
- [ ] **Risk** (`analysis/risk.py`) — validate ideas against portfolio + config.
- [ ] **Wire up** — L3 → risk → execution → portfolio loop the agent brain calls.

Each step is independently usable. You can use L3 for analysis today, add portfolio tracking next quarter, and never touch execution.
