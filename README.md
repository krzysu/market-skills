# Market Skills

Portable, composable technical analysis skills for trading agents. Pure-math indicators (L1) stacked into pattern-detection skills (L2) and trade-idea strategies (L3). Cross-asset macro context (the **Macro** domain) is the environment the per-ticker L1/L2/L3 stack runs inside. No API keys required.

## Architecture

**L1 — Indicator skills** — pure math, no I/O. Each exposes `analyze(candles)` returning structured dicts.

**L2 — Pattern skills** — compose L1 indicators into higher-level patterns (breakout, accumulation, exhaustion, etc.). Each exposes `analyze(candles)` returning `{pattern, signals, input_scores, narrative}`.

**L3 — Strategy skills** — compose L2s into trade ideas with entry/stop/target. Each exposes `analyze(candles)` returning `{ideas, narrative}`.

**Risk layer** — `analysis/risk.py:vet()` is an *advisory* function the LLM calls before asking the user to confirm an order. Composable policy registry (position size, drawdown, per-tier exposure, daily budget, insufficient funds, per-pair cooldown) returns a `RiskVerdict` (APPROVED / CONCERN / SCALE / REJECT). Not a hard gate.

**Execution layer** — `ExecutionProvider` Protocol in `analysis/providers/execution_base.py` mirrors the data-provider split. Kraken spot/perps adapters implemented; fills wire to `portfolio-mgmt` on success and record a structured decision trace (L3 idea, macro regime, risk verdict) in the `decisions` table.

**Decision tracing** — `analysis/decision.py` provides the `DecisionContext` TypedDict schema, a pure-function builder, and a validator. Every executed trade records a decision trace (one row per `intent_id`) in the `decisions` table — the system of record for "what did we think and why." Non-executed decisions (risk-rejected, user-skipped) can also be recorded.

**The LLM is the agent brain** (2026-06-22 pivot). This repo does NOT own a Python orchestrator — the LLM reads `SKILL.md` files, calls skills as tools, narrates results, asks the user to confirm, and (with explicit approval) calls `execution-kraken-spot` whose interactive confirm is the actual safety layer. Cron usage is analytics-only (`run-all-l3`, `position-watchdog`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the full domain-driven design.

## Skills

### L1 — Base Indicators

| Skill | Returns |
|-------|---------|
| [market-ema](./skills/market-ema/SKILL.md) | EMA 21/50/100/200, alignment, golden/death crosses |
| [market-rsi](./skills/market-rsi/SKILL.md) | RSI(14), oversold/overbought zones, 7d delta |
| [market-squeeze](./skills/market-squeeze/SKILL.md) | BB/KC squeeze, momentum histogram, release signal |
| [market-trend](./skills/market-trend/SKILL.md) | Trend score (-4 to +4), HH/HL structure, EMA alignment |
| [market-volume](./skills/market-volume/SKILL.md) | Volume ratio, OBV trend, OBV divergence, regime |
| [market-volatility](./skills/market-volatility/SKILL.md) | Realized vol 7d/30d, percentile rank, regime |
| [market-macd](./skills/market-macd/SKILL.md) | MACD line, signal, histogram, flips, crossovers |
| [market-fibonacci](./skills/market-fibonacci/SKILL.md) | Retracement/ext levels, nearest fib distance |
| [market-s-r](./skills/market-s-r/SKILL.md) | Support/resistance clusters, nearest levels, sit-on |

### L2 — Pattern Detection & Composite Verdicts

| Skill | Composes | Detects |
|-------|----------|---------|
| [market-accumulation](./skills/market-accumulation/SKILL.md) | S/R, Volume, Volatility, Trend | Wyckoff accumulation, spring, reaccumulation, UTAD |
| [market-breakout](./skills/market-breakout/SKILL.md) | Trend, Volume, S/R, Squeeze | Fresh/stale/confirmed breakouts |
| [market-exhaustion](./skills/market-exhaustion/SKILL.md) | Volume, Volatility, MACD, RSI | Capitulation, blowoff, impulse exhaustion |
| [market-liquidity-sweep](./skills/market-liquidity-sweep/SKILL.md) | S/R, Trend, Volume | Support/resistance sweeps, double tests |
| [market-trend-quality](./skills/market-trend-quality/SKILL.md) | Trend, Fibonacci, Volume, EMA | Uptrend/downtrend quality, weakening, degrading |

### L3 — Strategy Skills (Trade Ideas)

| Skill | Composes | Entry Logic |
|-------|----------|-------------|
| [strategy-trend-follow](./skills/strategy-trend-follow/SKILL.md) | market-trend-quality, market-breakout | Long/short in healthy trends, pullback or breakout |
| [strategy-mean-reversion](./skills/strategy-mean-reversion/SKILL.md) | market-rsi, market-s-r, market-volatility | Fade oversold/overbought at S/R levels |
| [strategy-breakout-confirm](./skills/strategy-breakout-confirm/SKILL.md) | market-breakout, market-volume, market-squeeze | Enter only on confirmed breakout with volume + squeeze |
| [strategy-accumulation-swing](./skills/strategy-accumulation-swing/SKILL.md) | market-accumulation, market-trend-quality | Wyckoff spring/reaccumulation within healthy trend |
| [strategy-exhaustion-fade](./skills/strategy-exhaustion-fade/SKILL.md) | market-exhaustion, market-s-r, market-trend | Fade blowoff/capitulation at S/R in extended trend |
| [strategy-liquidity-sweep](./skills/strategy-liquidity-sweep/SKILL.md) | market-liquidity-sweep, market-accumulation, market-volume | Enter after sweep with accumulation + volume confirmation |

### Batch Runners

Fetch candles once per ticker, run all skills in-process. Use for cron jobs / morning briefs to avoid N×M fetches.

| Skill | Runs | Use case |
|-------|------|----------|
| [run-all-l2](./skills/run-all-l2/SKILL.md) | All 6 L2 pattern skills | Pattern context for briefing |
| [run-all-l3](./skills/run-all-l3/SKILL.md) | All 6 L3 strategies (with `--track-ideas` for stale-idea detection) | Aggregated trade ideas across strategies |
| [run-watchlist](./skills/run-watchlist/SKILL.md) | L2 + L3 + notes across a basket | Bulk scan driven by `market-watchlist` |

### Cross-cutting helpers

Live alongside the indicator / data layer. Used by L3 strategies and the cron pipeline.

| Module | Purpose |
|--------|---------|
| [analysis/contracts.py](./analysis/contracts.py) | TypedDict shapes (`L1Result`, `L2Result`, `L2Pattern`, `L3Result`, `L3Idea`, `RegimeSignal`) + sanity helpers `l2_fired()`, `l2_classification()`, `validate_l3_tp_ladder()`, `conviction_version()` |
| [analysis/chop.py](./analysis/chop.py) | L3 idea history + `chop_score` conviction-calibration indicator (fraction of recent ideas at conviction ≤ 2). JSON store at `$XDG_DATA_HOME/market-skills/l3_idea_history.json`. Consumed by `bug-scan` to surface the "transition zone" signal |
| [analysis/macro.py](./analysis/macro.py) | Cross-asset macro fetcher + classifier. `fetch_regime()` returns a `RegimeSignal` (F&G, VIX, DXY, US10Y, BTC.D, total mcap → 3-axis `regime` labels + `regime_note`). In-process TTL cache (300s) and ring buffer at `$XDG_DATA_HOME/market-skills/macro_history.json` (200-entry cap). Best-effort + error-isolated: a single source failure records into `errors[]` and the rest of the signal still returns. |
| [analysis/valuation.py](./analysis/valuation.py) | SP500 Shiller CAPE z-score fetcher. `fetch_valuation()` returns a `ValuationSignal` (Shiller CAPE + z-score → 5-band `regime` + `regime_note`). In-process TTL cache (3600s) and ring buffer at `$XDG_DATA_HOME/market-skills/valuation_history.json` (200-entry cap). Same best-effort + error-isolated contract as macro. Narrate-only; consumed as a soft `veto_reasons` tag by `strategy-mean-reversion`. |
| [analysis/decision.py](./analysis/decision.py) | Decision tracing — `DecisionContext` TypedDict (L3 idea, regime, risk verdict, override) + pure-function builder + validator. Records one decision per `intent_id` in the `decisions` table (system of record). |

### Specialised analysis skills

Skills that compose L1/L2 indicators into higher-level readouts (chart sanity, multi-ticker screening, perp market structure).

| Skill | Returns |
|-------|---------|
| [market-snapshot](./skills/market-snapshot/SKILL.md) | Supertrend(10,3) + RSI(14) + MA alignment + `agrees_with_idea` consensus — designed for cross-TF chart sanity (e.g. validating a 1d L3 idea against the 4h chart structure) |
| [market-overview](./skills/market-overview/SKILL.md) | Unified market scan: runs trend + squeeze + RSI on multiple tickers in parallel, scores 0-100, ranks with actions (BUY/SELL/WATCH). For screening / daily brief. Supports any yfinance tickers |
| [market-basis](./skills/market-basis/SKILL.md) | Perpetual swap market structure: funding rate (current + 30-period average + annualised APR), spot-perp basis, squeeze/RSI divergence between spot and perp. CCXT-driven (`--source ccxt:binance` / `ccxt:bybit` / etc.) |

### Macro domain (cross-asset regime context)

Ticker-agnostic environment context for the LLM agent brain — answers "is the market supportive of risk-on exposure right now?" across equities, rates, USD, and crypto. Fetches six inputs (F&G via Alternative.me, VIX / DXY / US10Y / BTC mcap via yfinance, total crypto mcap via CoinGecko) and derives a three-axis regime label (`risk_appetite` / `liquidity` / `sentiment`) plus a one-line `regime_note` for narration.

`run-all-l3` attaches the latest `RegimeSignal` to the top of its JSON envelope under the `macro` key, so the LLM reads it once and applies the context to the per-ticker ideas. **Narrate-only by design** — the L3 strategy code is unchanged; modulation is the LLM's job (per the 2026-06-22 LLM-first pivot).

| Skill | Purpose |
|-------|---------|
| [market-macro](./skills/market-macro/SKILL.md) | Cross-asset macro regime. Ticker-agnostic CLI: `uv run skills/market-macro/scripts/run.py --json`. Returns a `RegimeSignal` with raw inputs (F&G, VIX, DXY, US10Y, BTC.D, total mcap) and derived labels. `--ttl=N` overrides the in-process cache (default 300s); `--no-history` skips the ring-buffer append. |
| [market-valuation](./skills/market-valuation/SKILL.md) | SP500 Shiller CAPE z-score. Ticker-agnostic CLI: `uv run skills/market-valuation/scripts/run.py --json`. Returns a `ValuationSignal` with raw inputs (SP500, Shiller CAPE, 50y mean/std) and a 5-band `regime` (OVEREXTENDED / ELEVATED / FAIR / DEPRESSED / OVERSOLD). `--ttl=N` overrides the in-process cache (default 3600s — slower-moving than price). Narrate-only; `strategy-mean-reversion` reads it and attaches a soft `veto_reasons` tag when CAPE disagrees with the trade direction. |

### Diagnostics

A detector that sits between L2/L3 output and the LLM narrative layer. Diagnostic only — never pairs with execution.

| Skill | Returns |
|-------|---------|
| [bug-scan](./skills/bug-scan/SKILL.md) | Classifier-anomaly detector: Pattern B shapes (absent-with-subs / silent / ghost), sub-signal weight drift, L3 calibration skew, cross-TF classification contradictions. Reads from `run-all-l2` / `run-all-l3` envelopes, the swing-scan state tracker, or fresh fetch. Cron-friendly (`--from-state` runs offline). |

### Per-user data

JSON-backed, gitignored, user-specific. Each skill stores its data under `skills/<name>/data/` and ships a checked-in sample under `skills/<name>/examples/`. CLI overrides via `--config` (or env var for `analysis/` accessors).

| Skill | Purpose |
|-------|---------|
| [market-watchlist](./skills/market-watchlist/SKILL.md) | Asset registry — named baskets of tickers with metadata (tier, source, hl_proxy, tracking_only). Drives `run-watchlist` and exposes alias resolution (`btc` → `BTCUSD`) |
| [market-notes](./skills/market-notes/SKILL.md) | Per-pair thesis notes with timestamps and optional expiration. Surfaced alongside verdicts via `--include-notes` on `run-all-l2` / `run-all-l3` / `run-watchlist` |

### Risk & Execution

Skills the LLM calls between analytics and order placement. Both consume the same `Intent` shape (defined in `analysis/providers/execution_base.py`). Risk is *advisory*; execution's interactive confirm is the actual safety layer.

| Skill | Purpose |
|-------|---------|
| [risk-engine](./skills/risk-engine/SKILL.md) | Advisory risk vet for an Intent. Composable policies (position size / portfolio drawdown / per-tier exposure / daily budget / insufficient funds / per-pair cooldown) return a `RiskVerdict` (APPROVED / CONCERN / SCALE / REJECT) with a `narrative_hint` for the LLM. Builds `RiskContext` from `portfolio-mgmt` + `market-watchlist`. The LLM narrates the verdict; the execution skill confirm is the actual gate. |
| [execution-kraken-spot](./skills/execution-kraken-spot/SKILL.md) | Place Kraken spot orders via the `kraken` CLI. Subcommands: `submit` / `balance` / `orders` / `cancel`. `--dry-run` calls `kraken order --validate` (no venue side-effect); live submit prompts for confirm unless `--yes`. Successful fills wire to `portfolio-mgmt` via `portfolio.db.add_transaction` when `--portfolio` is supplied; provenance (strategy/source_skills/thesis/intent_id) round-trips into the row's `notes` blob. Also records a structured `decision_context` trace in the `decisions` table via `analysis.decision.build_decision_context_from_idea()`; the LLM supplies regime/risk/override fields via `Intent.decision_decoration` or the `--decision-decoration` CLI flag. The `decisions` table write is idempotent on `intent_id` (matches the venue-level retry contract). No paper mode by design. |
| [execution-kraken-perps](./skills/execution-kraken-perps/SKILL.md) | Place Kraken perpetual-futures bracket orders (open + stop + take-profit) via `kraken futures ...`. Same `Intent` contract as spot, dispatched on `Intent.venue`. Auto-selects the perps risk-policy set inside `analysis.risk.vet`. Subcommands: `submit` / `balance` / `positions` / `cancel`. Interactive confirm; also records decision trace in the `decisions` table. No paper mode. |

### Utilities

| Skill | Purpose |
|-------|---------|
| [portfolio-mgmt](./skills/portfolio-mgmt/SKILL.md) | SQLite-backed portfolio tracking with FIFO cost basis, multi-portfolio support, live price fetching, P&L, replay, and external reconciliation |
| [position-watchdog](./skills/position-watchdog/SKILL.md) | Unified position monitor — entry/stop/TP ladders, multi-zone entry tracking, and L3 strategy signal evaluation. Per-watch state, alert dedup, per-watch `interval`/`period` (default `4h`/`6mo`), and a single `watches.json` config for any number of assets. Cross-checks bare tickers against `market-watchlist` via `--watchlist`. **Monitoring + manual confirm only; never executes orders.** |


## Quick Start

```bash
uv sync

# L1: single indicator
uv run skills/market-rsi/scripts/run.py AAPL --json

# L2: pattern detection or composite verdict
uv run skills/market-breakout/scripts/run.py BTC-USD --json
uv run skills/market-trend-quality/scripts/run.py AAPL --json

# L3: trade ideas
uv run skills/strategy-trend-follow/scripts/run.py SPY --json
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD --json

# Custom timeframe — every analysis skill accepts --interval and --period
uv run skills/market-ema/scripts/run.py AAPL --interval=4h --period=6mo --json
uv run skills/strategy-mean-reversion/scripts/run.py BTCUSD --interval=1h --period=1mo --json

# Chart-visual sanity (cross-TF check before entry)
uv run skills/market-snapshot/scripts/run.py VVVUSD --interval=4h --period=6mo --json

# Macro context (ticker-agnostic) — singleton RegimeSignal for the whole portfolio
uv run skills/market-macro/scripts/run.py --json

# SP500 valuation context (ticker-agnostic) — singleton ValuationSignal
uv run skills/market-valuation/scripts/run.py --json

# Stale-idea tracking on cron runs
uv run skills/run-all-l3/scripts/run.py HYPEUSD SOLUSD --interval=4h --period=6mo --track-ideas --json

# Batch runners (fetch once, run all)
uv run skills/run-all-l2/scripts/run.py SPY BTC-USD AAPL --json
uv run skills/run-all-l3/scripts/run.py SPY BTC-USD --json

# Bulk scan a watchlist basket (L2 + L3 + notes auto-included)
uv run skills/run-watchlist/scripts/run.py crypto_majors --json

# Per-user data: notes and watchlist (first-time setup)
cp skills/market-watchlist/examples/watchlist.example.json skills/market-watchlist/data/watchlist.json
cp skills/market-notes/examples/notes.example.json skills/market-notes/data/notes.json
uv run skills/market-watchlist/scripts/run.py list
uv run skills/market-notes/scripts/run.py add BTCUSD "thesis note" --expires 14d

# Per-user data: surface notes alongside an L2/L3 run
uv run skills/run-all-l2/scripts/run.py BTCUSD --include-notes --json
uv run skills/run-all-l3/scripts/run.py BTCUSD --include-notes --json

# Portfolio tracking
uv run skills/portfolio-mgmt/scripts/run.py init
uv run skills/portfolio-mgmt/scripts/run.py portfolio create --name spot
uv run skills/portfolio-mgmt/scripts/run.py add --portfolio spot --asset=kraken:BTCUSD --side buy --qty 0.01 --price 45000
uv run skills/portfolio-mgmt/scripts/run.py positions

# Position monitoring (entry/stop/TP alerts + L3 signal evaluation)
uv run skills/position-watchdog/scripts/run.py
uv run skills/position-watchdog/scripts/run.py --dry-run   # show what would alert, no state writes
uv run skills/position-watchdog/scripts/run.py --watchlist skills/market-watchlist/data/watchlist.json   # cross-ref ticks against the registry

# Risk vet (advisory — the LLM calls this before asking the user to confirm execution)
uv run skills/risk-engine/scripts/run.py \
  --intent skills/execution-kraken-spot/examples/intent.example.json \
  --portfolio spot --json

# Execution — dry-run validates with the venue without submitting
uv run skills/execution-kraken-spot/scripts/run.py submit \
  --pair HYPEUSD --side buy --order-type limit --volume 1.5 --limit-price 60.15 --dry-run

# Execution — live submit asks for confirmation; --yes skips the prompt when the LLM has explicit user pre-approval
uv run skills/execution-kraken-spot/scripts/run.py submit \
  --intent skills/execution-kraken-spot/examples/intent.example.json \
  --portfolio spot --yes --json

# Read-only ops
uv run skills/execution-kraken-spot/scripts/run.py balance
uv run skills/execution-kraken-spot/scripts/run.py orders
uv run skills/execution-kraken-spot/scripts/run.py cancel OABCDE-12345-FGHIJ

# Tests
uv run pytest
```

## Composition

```
L3 Strategy Skills
  strategy-trend-follow  strategy-mean-reversion  strategy-breakout-confirm
  strategy-accumulation-swing  strategy-exhaustion-fade  strategy-liquidity-sweep
        │                    │
        └───────┬────────────┘
                │
L2 Pattern Skills
  market-accumulation  market-breakout  market-exhaustion
  market-liquidity-sweep  market-trend-quality
        │                    │
        └───────┬────────────┘
                │
L1 Indicator Skills
  market-ema  market-rsi  market-squeeze  market-trend
  market-volume  market-volatility  market-macd
  market-fibonacci  market-s-r
                │
          analysis/indicators.py (pure math)

Cross-asset environment (singleton context, runs alongside per-ticker stack):
  market-macro              F&G + VIX + DXY + US10Y + BTC.D + total mcap → RegimeSignal
        │
  analysis/macro.py         fetch_regime (TTL-cached) + classify_regime + history store

  market-valuation          SP500 + Shiller CAPE → ValuationSignal (z-score + regime)
        │
  analysis/valuation.py     fetch_valuation (TTL-cached) + classify_regime + history store

Cross-cutting:
  analysis/contracts.py     TypedDicts (L1/L2/L3 + RegimeSignal) + l2_fired / l2_classification / validate_l3_tp_ladder / conviction_version
  analysis/chop.py          chop_score (L3 idea history → conviction-calibration indicator)
  analysis/decision.py      DecisionContext TypedDict + builder + validator (decision trace system of record)
  market-snapshot           Supertrend + RSI + MA alignment (chart-visual sanity)
```

## Data Providers

| Provider | Covers | `--source=` | Prefix |
|----------|--------|-------------|--------|
| Kraken | Crypto spot (BTC-USD, ETH-USD, ...) | `kraken` | `kraken:` |
| Hyperliquid | Perps (LIT, HYPE, BTC, ...) via official SDK | `hyperliquid` | `hl:` |
| Yahoo Finance | Stocks, ETFs (AAPL, SPY, ...) | `yfinance` | `yf:` / `yfinance:` |
| CCXT (binance) | Multi-exchange, funding rates | `ccxt:binance` | — |

For perp-specific data (funding rate, basis, spot-perp divergence), use [market-basis](./skills/market-basis/SKILL.md) — it reads funding rate data from CCXT providers alongside standard OHLC indicators.

Auto-routing: providers are tried in priority order. Use `provider:ticker` notation for explicit routing (e.g. `hl:LIT`, `yf:AAPL`).

```bash
# Auto-detect
uv run skills/market-ema/scripts/run.py BTC-USD

# Explicit provider
uv run skills/market-ema/scripts/run.py hl:LIT --json
uv run skills/market-ema/scripts/run.py yf:AAPL --json
```

## Macro sources (network)

`analysis/macro.py` reads from three external endpoints (no API keys required):

| Source | Used for | Notes |
|--------|----------|-------|
| Alternative.me `/fng/` | F&G value + label | Free, no key. Soft-fails on non-200 / parse error. |
| yfinance `fast_info` (`^VIX`, `DX-Y.NYB`, `^TNX`, `BTC-USD`) | VIX, DXY, US10Y, BTC mcap | Reuses the existing yfinance dep. BTC mcap is sometimes `None` for crypto — the fetcher falls back to CoinGecko's pre-computed `market_cap_percentage.btc`. |
| CoinGecko `/global` | Total crypto mcap + BTC.D fallback | Free, no key, ~10-30 req/min on the public tier. Sent with a `User-Agent` header. |

`analysis/valuation.py` reads two endpoints for SP500 valuation context:

| Source | Used for | Notes |
|--------|----------|-------|
| multpl.com `/shiller-pe` meta tag | Current Shiller CAPE | Free, no key, single HTML scrape (no JS). Regex parses the meta-description tag. Implausibility guard rejects cape > 100 or non-numeric. |
| yfinance `fast_info` (`^GSPC`) | SP500 spot | Reuses the existing yfinance dep. Labels the CAPE reading with a contemporaneous price for narration. |

## Conventions

- All analysis scripts (`market-*`, `strategy-*`, `run-all-*`, `market-basis`, `run-watchlist`) accept the same `--interval=` / `--period=` flags (defaults `1d` / `1y`). `position-watchdog` is config-driven — `interval` / `period` are set per watch in `watches.json` (default `4h` / `6mo`).
- Scripts accept `--json` for machine-readable output, require a ticker as the first positional argument, and accept `--source=<provider>` (auto-detect by default).
- `analysis/` functions are pure math — no I/O, no side effects.
- Each skill follows the Agent Skills spec: `SKILL.md` + `lib.py` + `scripts/`.
- Data providers in `analysis/providers/` implement the `Provider` protocol; execution providers implement the `ExecutionProvider` protocol. Add a new venue by implementing the protocol and registering it.
- Skill return shapes (`L1Result`, `L2Result`, `L3Result`, `L3Idea`) and L2/L3 invariants (present/classification coupling, TP-ladder monotonicity, conviction `version`, soft-veto reasons) are defined in `analysis/contracts.py` and enforced by the strategies at emit time.
- Per-user data (`market-watchlist`, `market-notes`, `position-watchdog`, `portfolio-mgmt`) lives under `skills/<name>/data/` and is gitignored; checked-in samples ship under `skills/<name>/examples/`.

## Timeframes

Default behavior across all analysis skills is daily candles over a 1-year lookback (~250 bars) — enough for EMA(200), BB/KC squeeze, MACD(12,26,9), OBV slope, etc. Override per-call:

```bash
# 4-hour candles over the last month
uv run skills/market-rsi/scripts/run.py AAPL --interval=4h --period=1mo --json

# Weekly candles over 5 years
uv run skills/market-trend/scripts/run.py SPY --interval=1wk --period=5y --json

# Bulk run-all-l2 at 1h over 6mo
uv run skills/run-all-l2/scripts/run.py BTCUSD ETHUSD --interval=1h --period=6mo --json
```

**Supported intervals** (union across providers): `1m`, `2m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `3d`, `1wk`, `1M`.

**Supported periods**: `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`, `10y`, `ytd`, `max`.

**Provider limits** (yfinance caps, the most common provider):

| Interval | Max period |
|----------|------------|
| `1m`–`30m` | ~60 days (script warns to stderr if exceeded) |
| `1h`–`12h` | ~2 years (warns) |
| `1d`+ | no effective limit |

Source of truth: `analysis/intervals.py`. Kraken drops `1M`; Hyperliquid/CCXT drop the sub-hour `2m`. Check `analysis/providers/{kraken,hyperliquid,ccxt,yfinance}.py` if you need a non-standard interval on a specific provider.

`position-watchdog` uses the same per-watch `interval`/`period` fields in `watches.json` (default `4h` / `6mo`). The same pair governs both the live-price tick and the L3 strategy evaluation — there is no split. Trade-off: alerts can lag by up to one full candle.
