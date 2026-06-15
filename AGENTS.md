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
- **Prefer L2 skills for decisions** — they synthesize multiple L1s into actionable verdicts. Only use L1/L0 for debugging or building new L2+ skills.
- **`lib/indicators.py`**: all pure math — EMA, RSI, squeeze, MACD, ATR, OBV, Fibonacci, swing points, etc.
- **`lib/data.py`**: data-fetching layer with prefix routing (`hl:LIT`, `yf:AAPL`, `kraken:BTC-USD`). Providers implement `Provider` protocol from `lib/providers/base.py`.
- **`lib/` is the only importable package** (`pyproject.toml` → `packages.find = {include = ["lib*"]}`). Skills are loaded dynamically via `importlib`.
- **`lib/contracts.py`** defines TypedDict return shapes (`L1Result`, `L2Result`, `L2Pattern`) — type-check against these in CI.
- Every skill follows Agent Skills spec: `SKILL.md` + `lib.py` + `scripts/run.py`.

## Conventions

- All `scripts/run.py` accept `--json` for machine output, first positional arg as ticker (default `SPY`), and `--source=<provider>`.
- Ruff exceptions: `skills/*/scripts/run.py` have E402 (sys.path trick before lib import) and E501 (long display f-strings) ignored.
- Provider notation: `provider:ticker` (e.g., `hl:LIT`, `yf:AAPL`, `kraken:BTC-USD`). Auto-detect tries Hyperliquid → CCXT(binance) → Kraken → YFinance.
- Never use `l` as a variable name — ambiguous with `1`, triggers E741.

## What to avoid

- No hardcoded paths to private repos (`kraken-cli`, `/Users/bulka/agents/`).
- Don't make skills importable as regular packages — the `lib/` package is the only one registered in `pyproject.toml`.
- Don't create `__pycache__/`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`, `*.egg-info/`, `dist/` — already in `.gitignore`.
- Don't make skills importable as regular packages — the `lib/` package is the only one registered in `pyproject.toml`.
