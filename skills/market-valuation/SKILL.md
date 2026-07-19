---
name: market-valuation
description: "SP500 Shiller CAPE z-score (single asset). Ticker-agnostic cross-asset context, identical in shape to market-macro. Returns a ValuationSignal with regime (OVEREXTENDED/ELEVATED/FAIR/DEPRESSED/OVERSOLD) and the regime_note for narration. Use as a soft tag for L3 ideas or to set the morning brief's structural context. NOT for entry/exit decisions alone."
version: 0.1.0
metadata:
  hermes:
    tags: [market, valuation, cape, regime, sp500, l1]
    category: market
compatibility: "Requires Python 3.12+ and uv; network access to multpl.com and Yahoo Finance"
---

# market-valuation

SP500 fair-value context via Shiller CAPE z-score. The single asset
class with academically validated valuation framing.

## When NOT to use

- For a single-ticker trade decision — CAPE is SP500-only cross-asset context, not a directional call on an individual asset. Use market-* / strategy-* skills for the ticker.
- As a standalone trade signal — it is narrate-only by design; no L3 strategy hard-vetoes on CAPE (at most a soft `veto_reasons` tag). Treat the regime as background, not a trigger.
- For non-SP500 valuation (BTC/oil/DXY) — deliberately out of scope; there is no defensible model shipped.

## Quick Start

```bash
uv run skills/market-valuation/scripts/run.py --json
```

No positional ticker — same ticker-agnostic shape as
[`market-macro`](../market-macro/SKILL.md). Default TTL is 3600s
(CAPE updates monthly, much slower-moving than price); running it
back-to-back is free.

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--json` | human | Emit the ValuationSignal as JSON to stdout. |
| `--ttl=N` | `3600` | In-process cache lifetime in seconds. `0` disables the cache. |
| `--no-cache` | off | Alias for `--ttl=0`. |
| `--no-history` | off | Skip the ring-buffer append to `$XDG_DATA_HOME/market-skills/valuation_history.json`. |

## Sources

- **Shiller CAPE** — multpl.com meta-tag scrape (single HTML fetch)
- **SP500 spot** — yfinance `^GSPC` via `fast_info`

Either source failure lands in `errors[]` and the corresponding input
is `None`; the call does not crash.

## What it returns

```json
{
  "timestamp": "2026-07-07T08:27:46+00:00",
  "inputs": {
    "sp500": 7537.43,
    "cape": 41.97,
    "cape_mean_50y": 21.0,
    "cape_std_50y": 9.0
  },
  "regime": {
    "cape_zscore": 2.33,
    "regime": "OVEREXTENDED"
  },
  "errors": [],
  "incomplete": false,
  "regime_note": "Valuation: CAPE 42.0 (+2.33σ) — overextended."
}
```

### Regime bands

`cape_zscore = (cape − 21.0) / 9.0`. Bands:

| z-score | regime |
|---|---|
| ≥ 2.0 | `OVEREXTENDED` |
| ≥ 1.0 | `ELEVATED` |
| ≥ −1.0 | `FAIR` |
| ≥ −2.0 | `DEPRESSED` |
| < −2.0 | `OVERSOLD` |
| n/a | `UNKNOWN` (any source failed) |

The 50y constants are hardcoded in `analysis/valuation.py`. Spot-check
against Yale's monthly Shiller CSV before bumping.

## How L3 strategies consume it

`strategy-mean-reversion` reads the z-score and attaches a soft
`veto_reasons` tag — see
[`strategy-mean-reversion/SKILL.md`](../strategy-mean-reversion/SKILL.md).
The tag is informational; the LLM agent brain decides whether to act
on it (ADR-0002). No L3 strategy hard-vetoes on CAPE.

## Edge cases

- multpl down → `cape=None`, `cape_zscore=None`, `regime="UNKNOWN"`,
  error recorded. The skill never crashes on a single bad source.
- All sources down → `inputs` all `None`, `regime.regime="UNKNOWN"`,
  `errors[]` lists every failure.
- The signal is **narrate-only**. There is no `conviction_modifier()`
  or `directional_filter()` in `analysis.valuation` — by design.

## Cron / pre-market context

The `regime_note` is the one-liner for the morning brief. The full
ring buffer is at `$XDG_DATA_HOME/market-skills/valuation_history.json`
(200-entry cap) and lets the agent brain answer "what was the CAPE
z-score 12h ago?" without re-fetching.

## Scope

CAPE is the only fair-value model the skill ships. BTC/oil/DXY
regression models are deliberately out of scope — their out-of-sample
R² on rolling fits is too low to trust, and a placeholder signal that
the LLM agent brain would treat as authoritative is worse than no
signal. Reassess if a future analysis demonstrates a defensible
statistical case.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Default schema is the per-skill minimal fields (3-6 essentials); pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count, `help[]` is contextual next-step command templates. Lib.py return shapes (`L1Result` / `L2Result` / `L3Result` / `L3Idea` / `RegimeSignal`) are unchanged — the envelope wraps them at the `scripts/run.py` boundary.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
