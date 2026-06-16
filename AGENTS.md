# Market Skills — Agent Guide

## Setup & verification

```bash
uv sync                          # install deps + dev
uv run ruff check                # lint (py312, line-length 120, rules E/F/I/N/W/UP)
uv run ruff format               # auto-format (double quotes)
uv run pytest tests/              # all tests
uv run pytest tests/test_X.py -v  # single file
```

## Architecture

- **L1 skills** (`skills/market-{ema,rsi,squeeze,trend,volume,volatility,macd,fibonacci,s-r}/`): pure indicator math, no I/O. Each exposes `analyze(candles, interval, period)` returning a structured dict.
- **L2 skills** (`skills/market-{accumulation,breakout,exhaustion,liquidity-sweep,trend-analysis,trend-quality}/`): compose L1 via `_load_l1_skill()` (cached with `@functools.cache`) → `l1.analyze(candles)`. Return `{pattern, signals, input_scores, narrative}`.
- **L3 strategies** (`skills/strategy-{trend-follow,mean-reversion,breakout-confirm,accumulation-swing,exhaustion-fade,liquidity-sweep}/`): compose L2s (and some L1s directly) via `_load_l2_skill()`. Return `{ideas, narrative}` where each idea has direction, conviction, entry/stop/target, reasoning, and source skills.
- **Batch runners** (`skills/run-all-l2/`, `skills/run-all-l3/`): fetch candles once per ticker, then run all L2 or L3 skills in-process. Use these from cron jobs / morning briefs to avoid N×M fetches.
- **Prefer L3 strategies for trade ideas** — they synthesize L2 verdicts into actionable trade setups with entry/stop/target. Use L2 for pattern context, L1 only for debugging or building new L2+ skills.
- **`lib/indicators.py`**: all pure math — EMA, RSI, squeeze, MACD, ATR, OBV, Fibonacci, swing points, etc.
- **`lib/data.py`**: data-fetching layer with prefix routing (`hl:LIT`, `yf:AAPL`, `kraken:BTC-USD`). Providers implement `Provider` protocol from `lib/providers/base.py`.
- **`lib/` and `portfolio/` are importable packages** (`pyproject.toml` → `packages.find = {include = ["lib*", "portfolio*"]}`). Skills are loaded dynamically via `importlib`.
- **`lib/contracts.py`** defines TypedDict return shapes (`L1Result`, `L2Result`, `L2Pattern`, `L3Result`, `L3Idea`) — type-check against these in CI.
- Every skill follows Agent Skills spec: `SKILL.md` + `lib.py` + `scripts/run.py`.

## Conventions

- All `scripts/run.py` accept `--json` for machine output, first positional arg as ticker (default `SPY`), and `--source=<provider>`.
- Ruff exceptions: `skills/*/scripts/run.py` have E402 (sys.path trick before lib import) and E501 (long display f-strings) ignored.
- Provider notation: `provider:ticker` (e.g., `hl:LIT`, `yf:AAPL`, `kraken:BTC-USD`). Auto-detect tries Hyperliquid → CCXT(binance) → Kraken → YFinance.
- Never use `l` as a variable name — ambiguous with `1`, triggers E741.

## What to avoid

- No hardcoded paths to private repos.
- Don't create `__pycache__/`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`, `*.egg-info/`, `dist/` — already in `.gitignore`.
