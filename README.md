# Market Skills

Portable, composable technical analysis skills for trading agents. Pure-math indicators (L1) stacked into pattern-detection skills (L2) and trade-idea strategies (L3). No API keys required.

## Architecture

**L1 — Indicator skills** — pure math, no I/O. Each exposes `analyze(candles)` returning structured dicts.

**L2 — Pattern skills** — compose L1 indicators into higher-level patterns (breakout, accumulation, exhaustion, etc.). Each exposes `analyze(candles)` returning `{pattern, signals, input_scores, narrative}`.

**L3 — Strategy skills** — compose L2s into trade ideas with entry/stop/target. Each exposes `analyze(candles)` returning `{ideas, narrative}`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full domain-driven design.

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
| [market-trend-analysis](./skills/market-trend-analysis/SKILL.md) | Trend, RSI, Squeeze, Volume | Composite trend verdict (BUY/WATCH/AVOID) |
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
| [run-all-l3](./skills/run-all-l3/SKILL.md) | All 6 L3 strategies | Aggregated trade ideas across strategies |

### Utilities

| Skill | Purpose |
|-------|---------|
| [portfolio-mgmt](./skills/portfolio-mgmt/SKILL.md) | SQLite-backed portfolio tracking with FIFO cost basis, multi-portfolio support, live price fetching, P&L, replay, and external reconciliation |


## Quick Start

```bash
uv sync

# L1: single indicator
uv run skills/market-rsi/scripts/run.py AAPL --json

# L2: pattern detection or composite verdict
uv run skills/market-breakout/scripts/run.py BTC-USD --json
uv run skills/market-trend-analysis/scripts/run.py AAPL --json

# L3: trade ideas
uv run skills/strategy-trend-follow/scripts/run.py SPY --json
uv run skills/strategy-liquidity-sweep/scripts/run.py BTC-USD --json

# Batch runners (fetch once, run all)
uv run skills/run-all-l2/scripts/run.py SPY BTC-USD AAPL --json
uv run skills/run-all-l3/scripts/run.py SPY BTC-USD --json

# Portfolio tracking
uv run skills/portfolio-mgmt/scripts/run.py init
uv run skills/portfolio-mgmt/scripts/run.py portfolio create --name spot
uv run skills/portfolio-mgmt/scripts/run.py add --portfolio spot --asset=kraken:BTCUSD --side buy --qty 0.01 --price 45000
uv run skills/portfolio-mgmt/scripts/run.py positions

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
  market-liquidity-sweep  market-trend-analysis  market-trend-quality
        │                    │
        └───────┬────────────┘
                │
L1 Indicator Skills
  market-ema  market-rsi  market-squeeze  market-trend
  market-volume  market-volatility  market-macd
  market-fibonacci  market-s-r
                │
          analysis/indicators.py (pure math)
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

## Conventions

- All scripts accept `--json` for machine-readable output
- All scripts accept a ticker as first positional argument (default: `SPY`)
- All accept `--source=<provider>` (default: auto-detect)
- `analysis/` functions are pure math — no I/O, no side effects
- Each skill follows the Agent Skills spec: `SKILL.md` + `lib.py` + `scripts/`
- Data providers in `analysis/providers/` implement the `Provider` protocol
- Skill return shapes are typed in `analysis/contracts.py` (`L1Result`, `L2Result`, `L2Pattern`, `L3Result`, `L3Idea`)
