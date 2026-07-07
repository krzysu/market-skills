---
name: market-trend-quality
description: "Assesses trend health by composing L1 indicators: EMA alignment, HH/HL integrity, pullback depth, impulse vs retrace ratio, and volume confirmation on impulse bars. Classifications: HEALTHY_UPTREND, HEALTHY_DOWNTREND, WEAKENING, DEGRADING, TANGLED."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, trend, quality, health]
    category: market
    layer: L2
    lens: structural health — the L2 layer L3 strategies key off
compatibility: "Requires Python 3.12+ and uv"
---

# market-trend-quality

L2 pattern detection skill that composes L1 indicators to assess the health and quality of a trend.

## Layering

This is the **only L2 trend skill** after the consolidation:

1. **`market-trend`** (L1) — *raw EMA alignment + HH/HL structure* (closest to candles)
2. **`market-trend-quality`** (this skill, L2) — *structural health* — what L3 strategies key off

`market-trend-analysis` was deprecated in favour of this skill to eliminate the cross-skill conflict that fired Pattern U every tick.

**Schema invariants**:
- `pattern.present` is True iff `pattern.classification` is non-None
- The 4-sub fallback (line 202 of `lib.py`) ensures 4+ present sub-signals always classifies, even when `signed_score` < 0.75 — a deep pullback can drag signed_score below threshold while the directional signal is still strong.
- The 3-sub branch (line 207 of `lib.py`) catches 3 present sub-signals with `weighted_sum > 0.30`.
- HEALTHY_UPTREND requires `trend_score >= 3` AND `hh_intact=True` AND `ema_bullish=True`. If any of those fail, classifier cascades to WEAKENING / DEGRADING / TANGLED.

## Quick Start

```bash
uv run skills/market-trend-quality/scripts/run.py SPY
uv run skills/market-trend-quality/scripts/run.py SPY --json
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

## Sub-signals

| Sub-signal | Weight | Source L1 | Logic |
|---|---|---|---|
| EMA alignment | 0.25 | market-trend | alignment field: FULL_BULL, FULL_BEAR, etc. |
| HH/HL integrity | 0.25 | market-trend | higher_high and higher_low both intact for uptrend, both broken for downtrend |
| Pullback depth (shallow vs deep) | 0.20 | market-trend + market-fibonacci | Compare price distance to fib support levels to gauge retracement depth |
| Impulse vs retrace ratio | 0.15 | derived from candles | 6-bar recent return vs 6-bar prior return, normalized by ATR(14). Bounce (recent > 0, prior < 0) → +0.15; decline-decel (recent > 0, prior > 0, recent < abs(prior)) → −0.075. Requires an actual reversal for the bullish path — continued selling at a slower pace does not count as a bounce. |
| Volume confirmation | 0.15 | market-volume | High volume + OBV rising → +0.15; high volume + OBV falling → −0.15 (distribution); quiet accumulation (vr in [0.7, 1.5] with OBV rising or price > EMA50) → +0.075; low volume + OBV falling → −0.15. |

## Classifications

| Classification | Meaning |
|---|---|
| HEALTHY_PULLBACK_UPTREND | Healthy uptrend in pullback phase — bullish EMA alignment, recent bounce off the lows, price back above EMA50. Entry: bounce confirmation. |
| HEALTHY_UPTREND | Strong uptrend with score >= 3, intact HH/HL, and bullish EMA alignment |
| HEALTHY_DOWNTREND | Strong downtrend with score <= -3, broken HH/HL, and bearish EMA alignment |
| WEAKENING | Trend score 1-2 or -1 to -2 with conflicting sub-signals. Also used as a fallback when the sub-signal sum is strongly directional (≥0.75 / ≤−0.75) but the L1 trend_score didn't reach the HEALTHY_* gate — ensures `pattern.present` is never false when sub-signals are strongly aligned. Also fires for 3 present sub-signals with `weighted_sum > 0.30` (mirrors the bug-scan Shape #1 trigger and the ALGO 4h liquidity-sweep fix). |
| DEGRADING | HH/HL structure breaking down and EMA alignment becoming tangled |
| TANGLED | No clear alignment or directional conviction |

HEALTHY_PULLBACK_UPTREND is checked first (before HEALTHY_UPTREND) — if the conditions are met, it wins regardless of the underlying trend_score.

## Output

- `pattern.present` (bool)
- `pattern.confidence` (1–5)
- `pattern.classification` (HEALTHY_PULLBACK_UPTREND / HEALTHY_UPTREND / HEALTHY_DOWNTREND / WEAKENING / DEGRADING / TANGLED)
- `pattern.type` always `"TREND_QUALITY"`
- `signals` — per-signal `{"present": bool, "weight": float}`
- `input_scores` — raw L1 outputs
- `narrative` — one-sentence explanation

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
