---
name: market-breakout
description: "Detects whether a breakout is real by composing L1 indicators: structure break, volume confirmation, OBV confirmation, squeeze release, and retest holding. Classifications: FRESH, CONFIRMED, STALE, FAILED."
version: 0.1.0
metadata:
  hermes:
    tags: [market, pattern, breakout, momentum]
    category: market
compatibility: "Requires Python 3.12+ and uv"
---

# market-breakout

L2 pattern detection skill that composes L1 indicators to determine whether a breakout is genuine or likely to fail.

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `TICKER` (positional) | — | Required. Supports `provider:ticker` (e.g. `hl:LIT`, `yf:AAPL`). |
| `--json` | human | Emit JSON to stdout. |
| `--source=PROVIDER` | auto-detect | Force a data provider (see [README](../../README.md#data-providers)). |
| `--interval=INTERVAL` | `1d` | `1m`/`2m`/`5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`8h`/`12h`/`1d`/`3d`/`1wk`/`1M`. |
| `--period=PERIOD` | `1y` | `1d`/`5d`/`1mo`/`3mo`/`6mo`/`1y`/`2y`/`5y`/`10y`/`ytd`/`max`. |

Both timeframe flags are validated — bad values exit 2 with a friendly error. For intraday (`--interval=1h`), bump `--period` to `6mo` or `1y`; yfinance caps hourly at ~2y and anything sub-hour at ~60d.

## Classifications

| Classification | Meaning |
|---|---|
| FRESH | Breakout just initiated (confidence >= 3, no retest yet) |
| CONFIRMED | Breakout confirmed by successful retest of the breakout level |
| STALE | Breakout has been present >10 bars without continuation |
| FAILED | Structure break reversed, price returned to consolidation |

## Sub-signals

| Sub-signal | Weight | Source L1 | Logic |
|---|---|---|---|
| Structure break | 0.35 | market-trend | alignment is FULL_BULL or FULL_BEAR, or signal is STRONG_UPTREND/STRONG_DOWNTREND |
| Volume confirmation | 0.25 | market-volume | volume_ratio > 1.5 |
| OBV confirmation | 0.15 | market-volume | obv_trend == "rising" with bullish, or "falling" with bearish |
| Squeeze release | 0.15 | market-squeeze | squeeze_on is False (released) and direction == "increasing" with bullish momentum |
| Retest holding | 0.10 | market-s-r | sits_on_level is True after break (lagging) |

## Trigger

`pattern.present` is True when at least 2 sub-signals are present AND their
combined weight exceeds 0.30. The previous `weighted_sum / total_weight >= 0.40`
threshold silently dropped 2-sub combinations at `weighted_sum` in
(0.30, 0.40) — e.g. `volume_confirmation + retest_holding` at 0.35 — into
`present=False` while the sub-signals were populated.

`confidence` is `round(weighted_sum * 5)`, clamped to `[1, 5]`.

## Recognized sub-shapes

When the primary trigger isn't met (specifically: fewer than 2 sub-signals
present, or `weighted_sum <= 0.30`) but a known combination of sub-signals is
firing, the classifier falls back to a recognized sub-shape. This catches
2-sub combos below the primary trigger's 0.30 threshold — e.g.
`squeeze_release + retest_holding` (wsum 0.25) where two corroborating L1s
(squeeze + S/R) plus the implied direction from squeeze are meaningful.

| Sub-shape | Sub-signals required | Classification |
|---|---|---|
| Post-squeeze retest holding | squeeze_release + retest_holding | CONFIRMED |

## Output

- `pattern.present` (bool)
- `pattern.confidence` (1–5)
- `pattern.classification` (FRESH / CONFIRMED / STALE / FAILED)
- `pattern.type` always `"BREAKOUT"`
- `signals` — per-signal `{"present": bool, "weight": float}`
- `input_scores` — raw L1 outputs
- `narrative` — one-sentence explanation

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.
