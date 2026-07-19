---
name: market-macro
description: "Cross-asset macro regime: Fear & Greed, VIX, DXY, US10Y, BTC dominance, total crypto market cap. Ticker-agnostic — returns a RegimeSignal with derived risk_appetite / liquidity / sentiment labels and a one-line regime_note. Use before sizing a batch, when narrating a watchlist, or as pre-market context for the agent brain."
version: 0.1.0
metadata:
  hermes:
    tags: [market, macro, regime, sentiment, vix, dxy, btc]
    category: market
compatibility: "Requires Python 3.12+ and uv; network access to Alternative.me, CoinGecko, and Yahoo Finance"
---

# market-macro

Cross-asset macro context. One call → one `RegimeSignal` (see
`analysis.contracts.RegimeSignal`). **Ticker-agnostic** — there is
no positional ticker; the same signal covers the whole portfolio.

## When NOT to use

- For a single-ticker trade decision — the signal is ticker-agnostic portfolio context, not a directional call on one asset. Use market-* / strategy-* skills for the ticker.
- As a standalone trade signal — it is narrate-only by design; there is no `conviction_modifier()` or directional filter. Treat the regime as background, not a trigger.
- When you need position-level risk — that is `risk-engine`; market-macro only sets the mood.

## Quick Start

```bash
uv run skills/market-macro/scripts/run.py --json
```

That is the only required invocation. The default TTL is 300s, so
running it back-to-back (e.g. from `run-all-l3`) is free.

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--json` | human | Emit the `RegimeSignal` as JSON to stdout. |
| `--ttl=N` | `300` | In-process cache lifetime in seconds. `0` disables the cache (every call hits the network). |
| `--no-cache` | off | Alias for `--ttl=0`. |
| `--no-history` | off | Skip the ring-buffer append to `$XDG_DATA_HOME/market-skills/macro_history.json`. |

## Sources

- **Fear & Greed** — Alternative.me public API (no key)
- **VIX, DXY, US10Y** — yfinance `^VIX`, `DX-Y.NYB`, `^TNX` via `fast_info`
- **BTC market cap** — yfinance `BTC-USD` via `fast_info`
- **Total crypto market cap, BTC.D** — CoinGecko `/global` (no key, public tier)

A single source failure is recorded in `errors[]` and the
corresponding input is `None` — the call does not crash.

## What it returns

```json
{
  "timestamp": "2026-06-25T12:34:56+00:00",
  "inputs": {
    "fng": 22,
    "fng_label": "Extreme Fear",
    "vix": 28.4,
    "dxy": 104.1,
    "us10y": 4.32,
    "btc_dominance": 53.8,
    "btc_dominance_source": "yf",
    "total_mcap_usd": 2.41e12
  },
  "regime": {
    "risk_appetite": "RISK_OFF",
    "liquidity": "TIGHTENING",
    "sentiment": "EXTREME_FEAR"
  },
  "errors": [],
  "regime_note": "Macro: risk-off, liquidity tightening; sentiment extreme fear. Defensive posture recommended. (VIX 28.4, F&G 22)"
}
```

### `regime` axis definitions

| Axis | Primary driver | Bands |
|---|---|---|
| `risk_appetite` | VIX | `<15` RISK_ON · `15..25` NEUTRAL · `25..35` RISK_OFF · `≥35` CRISIS. Capped to NEUTRAL when DXY > 105 or US10Y > 4.5. |
| `liquidity` | US10Y | `<3.5` EASY · `3.5..4.5` TIGHTENING · `≥4.5` TIGHT. STRESS when both US10Y ≥ 4.5 and VIX > 25. |
| `sentiment` | F&G | `<25` EXTREME_FEAR · `25..45` FEAR · `45..55` NEUTRAL · `55..75` GREED · `≥75` EXTREME_GREED. |

## How L3 strategies consume it

`run-all-l3` attaches the latest `RegimeSignal` to the top of its
JSON envelope (key `macro`). The agent brain reads it once and
applies the regime context to the per-ticker ideas in its narration.
**The L3 strategy code is intentionally unchanged** — modulation
is the LLM's job, not a Python one (ARCHITECTURE.md "LLM-as-agent-brain").

## Edge cases

- One source down → `errors[]` populated, the other inputs still return. The skill never crashes on a single bad source.
- `btc_dominance_source` records which pipeline produced the reading: `"yf"` (derived from yfinance BTC mcap / CoinGecko total mcap) or `"coingecko"` (fallback to CoinGecko's pre-computed BTC.D when yfinance's BTC market cap is missing — common for crypto tickers). `None` when both paths fail. The field is preserved in `macro_history.json`, so backtests reading the ring buffer can distinguish the two pipelines.
- All sources down → `inputs` all `None`, `regime` filled with safe defaults (`NEUTRAL`/`TIGHTENING`/`NEUTRAL`), `errors[]` lists every failure.
- The signal is **narrate-only**. There is no `conviction_modifier()` or `directional_filter()` in `analysis.macro` — by design (2026-06-22 LLM-first pivot).

## Cron / pre-market context

The `regime_note` is the one-liner for the morning brief. The full
ring buffer is at `$XDG_DATA_HOME/market-skills/macro_history.json`
(200-entry cap) and lets the agent brain answer "what was the regime
12h ago?" without re-fetching.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
