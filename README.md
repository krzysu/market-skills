# Market Skills

Agent Skills for trading and market analysis. Portable, composable technical analysis skills following the [Agent Skills](https://agentskills.io) specification.

Uses Yahoo Finance (free, no API keys) and the Kraken Spot API via a local `kraken` CLI. Auto-routes crypto pairs to Kraken, equities to Yahoo Finance.

## License

MIT — see [LICENSE](LICENSE).

## Why Market Skills?

AI agents that do trading or market analysis need more than just price data — they need structured technical analysis they can trust. These skills give agents:

- **Single-indicator primitives**: RSI, moving averages, squeeze momentum — run one, get clean JSON
- **Composite analysis**: Trend verdict from multiple weighted indicators with conviction scoring
- **Screening workflows**: Scan watchlists, filter by action, rank by score
- **Composability**: Higher-level skills import the same `lib/` functions — no subprocess overhead

## Indicator Library

Pure math, no I/O, no external deps beyond stdlib:

EMA — ATR — RSI — MACD — OBV — Fibonacci — Pearson correlation — standard deviation — linear regression — log returns — realized volatility — swing highs/lows — support/resistance clustering — golden/death cross detection — squeeze momentum (BB/KC) — OBV divergence — percentile rank

## Skills

| Skill | Layer | What it does |
|-------|-------|--------------|
| [market-ema](./skills/market-ema/SKILL.md) | Low-level | EMA 21/50/100/200, trend alignment, golden/death crosses |
| [market-rsi](./skills/market-rsi/SKILL.md) | Low-level | RSI(14) oscillator with oversold/overbought zones |
| [market-squeeze](./skills/market-squeeze/SKILL.md) | Low-level | BB/KC squeeze momentum for breakout timing |
| [market-trend-analysis](./skills/market-trend-analysis/SKILL.md) | Mid-level | Weighted composite: EMA + RSI + squeeze + volume → conviction-scored verdict |
| [market-overview](./skills/market-overview/SKILL.md) | High-level | Multi-ticker parallel scan, 0-100 unified score, BUY/WATCH/AVOID |
| [recipe-scanner](./skills/recipe-scanner/SKILL.md) | Recipe | Watchlist momentum sweep, filters actionable setups, ranks by conviction |

### How they compose

```
recipe-scanner ──→ market-overview ──→ market-trend-analysis
                                           │
                         ┌─────────────────┼─────────────────┐
                    market-ema         market-rsi       market-squeeze
```

## Quick Start

```bash
# Install
uv sync

# Run a skill
uv run skills/market-rsi/scripts/run.py AAPL --json

# Run tests
uv run pytest
```

## Data Providers

Skills accept `--source` to pick a data provider. Omit it for auto-detection.

| Provider | Covers | `--source=` |
|----------|--------|-------------|
| Kraken | Crypto pairs (BTC-USD, ETH-USD, SOL-USD, ...) | `kraken` |
| Yahoo Finance | Stocks, ETFs (AAPL, SPY, GLD, QQQ, ...) | `yfinance` |

```bash
# Auto-detect: crypto → Kraken, equities → Yahoo Finance
uv run skills/market-ema/scripts/run.py BTC-USD

# Explicit provider
uv run skills/market-ema/scripts/run.py BTC-USD --source=kraken
uv run skills/market-ema/scripts/run.py AAPL --source=yfinance
```

**How auto-routing works:** Each provider has a `supports(ticker)` method. `KrakenProvider` queries `kraken pairs --pair <PAIR>` to check if the pair is actually listed. `YFinanceProvider` accepts everything as fallback. The registry tries providers in priority order and uses the first match.

## Conventions

- All scripts accept `--json` for machine-readable output
- All scripts accept a ticker as first positional argument (default: `SPY`)
- All scripts accept `--source=kraken|yfinance` (default: auto-detect)
- `lib/` functions are pure math — no I/O, no side effects
- Each skill folder follows the Agent Skills spec: `SKILL.md` + optional `scripts/`, `references/`
- Data providers live in `lib/providers/` — add new ones by implementing the `Provider` protocol
