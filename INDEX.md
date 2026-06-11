# Market Skills — Skills Index

Agent skills for trading and market analysis. Each skill is a folder with a `SKILL.md` file following the [Agent Skills](https://agentskills.io) specification. Skills use Yahoo Finance data (free, no API key required).

## Low-Level Indicators

Single-indicator skills. Run independently to get one specific metric.

| Skill | Description |
|-------|-------------|
| [market-ema](./skills/market-ema/SKILL.md) | Moving averages (EMA 21/50/100/200), trend alignment, golden/death cross detection |
| [market-rsi](./skills/market-rsi/SKILL.md) | RSI(14) momentum oscillator with oversold/overbought zones |
| [market-squeeze](./skills/market-squeeze/SKILL.md) | Bollinger Band / Keltner Channel squeeze momentum for breakout timing |

## Mid-Level Analysis

Composite skills that combine multiple indicators into a single verdict.

| Skill | Description |
|-------|-------------|
| [market-trend-analysis](./skills/market-trend-analysis/SKILL.md) | Weighted trend verdict from EMA + RSI + squeeze + volume. Conviction scoring with conflict detection. |

## High-Level Overview

Full market scans across multiple tickers.

| Skill | Description |
|-------|-------------|
| [market-overview](./skills/market-overview/SKILL.md) | Unified market scan with 0-100 scoring and ranked BUY/WATCH/AVOID actions |

## Recipes

Multi-step workflows combining multiple analysis layers.

| Skill | Description |
|-------|-------------|
| [recipe-scanner](./skills/recipe-scanner/SKILL.md) | Multi-ticker momentum/breakout sweep. Scans a watchlist, filters actionable setups, ranks by conviction. |

## Installation

```bash
# Install Python 3.12+ and uv, then:
uv sync
```

## Usage Pattern

All skills follow the same CLI convention:

```bash
uv run skills/<skill-name>/scripts/run.py TICKER --json
```

- Pass `--json` for machine-readable output (recommended for agents)
- Omit `--json` for human-readable terminal output
- Default ticker is `SPY` if none provided

### Example: Full agent workflow

```bash
# 1. Scan the market for actionable setups
uv run skills/recipe-scanner/scripts/run.py --action BUY --top 3 --json

# 2. Deep-dive on the top candidate
uv run skills/market-trend-analysis/scripts/run.py AAPL --json

# 3. Check individual components that matter
uv run skills/market-rsi/scripts/run.py AAPL --json
uv run skills/market-squeeze/scripts/run.py AAPL --json
```

## Shared Library

All skills share a common `lib/` package with pure indicator functions:

- `lib/indicators.py` — EMA, RSI, squeeze, MACD, ATR, OBV, Fibonacci, swing points, etc.
- `lib/data.py` — Yahoo Finance data fetcher
- `lib/formatting.py` — JSON output and CLI helpers

Import from `lib/` to build your own skills or use the functions directly:

```python
from lib.indicators import compute_rsi, compute_ema
from lib.data import fetch_ohlc
```
