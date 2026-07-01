---
name: market-accumulation
description: "Detects whether smart money is positioning by composing L1 indicators: spring/shakeout, absorption, sign of strength, reaccumulation, and low volatility after distribution. Classifications: SPRING, REACCUMULATION, DISTRIBUTION, UTAD."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, accumulation, smart-money]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-accumulation

L2 pattern detection skill that composes L1 indicators to determine whether smart money is accumulating a position.

## Quick Start

```bash
uv run skills/market-accumulation/scripts/run.py SPY
uv run skills/market-accumulation/scripts/run.py SPY --json
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## Sub-Signals

| Sub-signal | Weight | Source L1 |
|---|---|---|
| Spring/shakeout (dip below support + reclaim) | 0.30 | market-s-r + market-volume |
| Absorption (high volume, flat price) | 0.20 | market-volume + market-volatility |
| Sign of strength (high vol up after basing) | 0.20 | market-volume + market-trend |
| Reaccumulation (after initial markup) | 0.15 | market-trend |
| Low volatility after distribution | 0.15 | market-volatility |

## Classifications

- **SPRING**: Spring/shakeout + absorption — price faked below support then reclaimed with volume
- **REACCUMULATION**: Reaccumulation + sign of strength — pullback in uptrend with institutional buying
- **DISTRIBUTION**: Sign of strength + absorption with bearish trend — smart money distributing
- **UTAD**: Low volatility after prior high volatility — upthrust after distribution

## Trigger

`pattern.present` is True when at least 2 sub-signals are present AND their
combined weight exceeds 0.30 — mirrors the bug-scan Shape #1 trigger
(see `skills/bug-scan/SKILL.md`) and the ALGO 4h liquidity-sweep fix
(BUG-2026-06-24-01). The previous `weighted_sum / total_weight >= 0.4`
threshold silently dropped 2-sub combinations at `weighted_sum` in
(0.30, 0.40) — e.g. `absorption + reaccumulation`, `sign_of_strength +
low_vol_after_distribution`, `absorption + low_vol_after_distribution`
(each wsum 0.35) — into `present=False` while the sub-signals were populated.

`confidence` is `round(weighted_sum * 5)`, clamped to `[1, 5]`.

## Recognized sub-shapes

When the primary trigger isn't met (specifically: fewer than 2 sub-signals
present, or `weighted_sum <= 0.30`) but a known combination of sub-signals is
firing, the classifier falls back to a recognized sub-shape. This catches
2-sub combos at the `weighted_sum = 0.30` boundary — e.g.
`reaccumulation + low_vol_after_distribution` (wsum 0.30) where two
corroborating L1s are meaningful.

| Sub-shape | Sub-signals required | Classification |
|---|---|---|
| Reaccumulation + low vol after distribution | reaccumulation + low_vol_after_distribution | REACCUMULATION |

## Output

Returns pattern presence, confidence (1-5), classification, sub-signal states, input scores from each L1, and a one-sentence narrative.
