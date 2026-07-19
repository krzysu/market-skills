---
name: market-snapshot
description: "Single-call chart-visual sanity check (Supertrend 10/3 + RSI 14 + MA alignment). Use to cross-check a higher-TF L3 idea against the lower-TF chart structure before entry. Audit 2026-06-21 #5b #5."
version: 0.1.0
metadata:
  hermes:
    tags: [market, snapshot, sanity, supertrend, rsi, alignment]
    category: market
compatibility: "Requires Python 3.12+ and uv. Sources: same providers as other market-skills."
---

# market-snapshot

Lean chart-visual cross-check designed for batch evaluation. Returns the three
signals a chart-watcher checks before pulling the trigger: **Supertrend direction**,
**RSI position**, and **EMA alignment**. The `agrees_with_idea` field summarizes
whether all three agree on direction — the LLM agent brain uses this to
downgrade 1d L3 ideas that contradict the 4h chart structure.

## When to use

- After a 1d L3 idea fires (swing-scan / batch), before flagging `[OPPORTUNITY] ENTRY`
- When cross-TF disagreement (Pattern D) is detected — fetch this on the lower TF
  to determine whether the higher-TF idea is real or noise

## When NOT to use

- For full L2/L3 analysis — use `run-all-l2` / `run-all-l3` instead. This skill
  returns only three visual signals, not the full pattern detection suite.
- For 220+ candle trend analysis (EMA200, deep HH/HL) — use `market-trend`.

## Usage

```bash
# Sanity-check a 1d idea on 4h
uv run skills/market-snapshot/scripts/run.py VVVUSD --interval=4h --period=6mo --json

# Quick visual read on 1h
uv run skills/market-snapshot/scripts/run.py HYPEUSD --interval=1h --period=1mo
```

## Output schema

```json
{
  "ticker": "VVVUSD",
  "interval": "4h",
  "current_price": 14.41,
  "supertrend": {"value": 14.92, "direction": "down", "period": 10, "multiplier": 3.0},
  "rsi":        {"value": 40, "signal": "NEUTRAL"},
  "ma_alignment": "TANGLED",
  "agrees_with_idea": false
}
```

## Cross-check interpretation

| supertrend | rsi | ma_alignment | agrees_with_idea |
|------------|-----|--------------|------------------|
| up | not OVERBOUGHT | FULL_BULL / PARTIAL_BULL | true (bullish consensus) |
| down | not OVERSOLD | FULL_BEAR / PARTIAL_BEAR | false (bearish consensus) |
| mixed | — | TANGLED | null (inconclusive) |

## Layer rules

- Lives between L0 (providers) and L1 (single-skill indicators). Calls `market-rsi`
  and `market-trend` via the skill loader so signals stay consistent with the rest
  of the pipeline.
- Output is **read-only** — never gates trades on its own. Always pair with a full
  L2/L3 verdict from `run-all-l2` / `run-all-l3`.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
