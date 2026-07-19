---
name: bug-scan
description: "Classifier-anomaly detector for swing-scan, morning-brief, and external-scanner workflows. Detects Pattern B shapes (absent-with-subs, silent, ghost), sub-signal weight drift, L3 calibration skew, and cross-TF classification contradictions. Audit 2026-06-23 #2."
version: 0.1.0
metadata:
  hermes:
    tags: [market, anomaly, classifier, bug, drift, scan]
    category: market
compatibility: "Requires Python 3.12+ and uv. Reads from analysis.contracts helpers (l2_fired, l2_classification). `--from-state` runs offline against a pre-computed state file."
---

# bug-scan

Single source of truth for the classifier-anomaly rules the swing-scan
workflow has been hunting since 2026-06-21. Designed for both
automated-tick surfacing and LLM-agent surface so morning briefs and
external scanners stop missing the same anomalies that swing-scan catches.

## When to use

- After running `run-all-l2` or `run-all-l3` — pipe the envelope through
  `--from-json` to surface bugs the brief would otherwise miss. An L3-only
  envelope now also surfaces cross-TF *direction* conflicts (a `long`
  dominant idea on one TF vs a `short` on another for the same strategy),
  not just the L2 healthy-vs-weakening contradictions — so a merged
  multi-timeframe `run-all-l3` envelope no longer reports zero findings.
  Set `BUG_SCAN_FROM_JSON_DEBUG=1` to print a `bug-scan from-json:
  l2_keys=<n>, l3_keys=<n>` line to stderr before the scan runs, which
  makes future "empty findings" regressions surface immediately.
- Offline (no network): `--from-state` reads the existing swing-scan
  state tracker and translates its open_findings into the bug-scan
  envelope.
- Standalone for a fresh scan on ad-hoc tickers (does the fetch + run +
  detect all in one call).

## When NOT to use

- For full L2/L3 analysis — use `run-all-l2` / `run-all-l3` directly. This
  skill is a *detector* on top of their output, not a replacement.
- For live trade signals — bug-scan is purely diagnostic. Never pair its
  output with execution.

## Detection rules

| Shape | Tag | Trigger | Severity |
|-------|-----|---------|----------|
| `pattern_b_1` (absent-with-subs) | `[BUG]` | `l2_fired=False` AND ≥2 sub-signals present AND wsum > 0.30 | medium / high (wsum ≥ 0.5) |
| `pattern_b_2` (silent) | `[BUG]` | `pattern.present=True` AND `classification=None` AND any sub-signal present | high |
| `pattern_b_3` (ghost) | `[BUG]` | `pattern.present=False` AND `classification` populated | high |
| `weight_drift` | `[DRIFT]` | sub-signal weights sum outside 1.0 ± 0.05 | medium |
| `l3_calibration_skew` | `[INFO]` | ≥6 ideas, zero with conviction ≥ 4 | low (regime signal) |
| `cross_tf_contradiction` | `[INFO]` | same ticker + L2 skill, healthy vs weakening across TFs | medium |
| `cross_tf_direction_conflict` | `[INFO]` | same ticker + L3 strategy, long dominant idea on one TF vs short on another (both ideas conviction ≥ 2) | medium |

The first three shapes are the recurring `Pattern B` family: classifier
anomaly + L1 absence + non-null classification (ghost-classifier
shape). Weight drift catches the `market-exhaustion` 0.900 regression.
The L3 and cross-TF checks detect calibration skew and inter-timeframe
contradictions.

## Usage

```bash
# Fresh fetch — positional tickers, comma-separated interval/period
uv run skills/bug-scan/scripts/run.py HYPEUSD SOLUSD AEROUSD \
    --interval=1h,4h --period=1mo,6mo --json

# Read the swing-scan state tracker (no network)
uv run skills/bug-scan/scripts/run.py --from-state --json

# Pipe a pre-fetched run-all-l2 envelope
uv run skills/bug-scan/scripts/run.py --from-json /path/to/l2_envelope.json --json
```

The `--interval` / `--period` flags accept comma-separated lists paired
1:1. A single `--period` value is reused for every interval.

## Output schema

```json
{
  "ok": true,
  "findings": [
    {
      "tag": "[BUG]",
      "shape": "pattern_b_1",
      "ticker": "HYPEUSD",
      "tf": "1h",
      "skill": "market-trend-quality",
      "summary": "Pattern B Shape #1: 3 subs (w=0.60) but pattern absent",
      "wsum": 0.60,
      "present_sub_signals": ["ema_alignment", "pullback_depth", "volume_confirmation"],
      "severity": "medium"
    }
  ]
}
```

Findings are sorted high → medium → low severity for terminal output.
Use `--json` to get the full list unsorted.

## State-resolved paths

- `--from-state` (bare) reads from `$XDG_DATA_HOME/market-skills/swing_scan_state.json`.
  Pass `--from-state=PATH` to override. The env var must be set — the
  runner does not fall back to a host-specific default.
- The fresh-fetch mode uses the same data provider stack as
  `run-all-l2` / `run-all-l3` (Hyperliquid → CCXT → Kraken → YFinance).

## Layer rules

- Sits between L2/L3 and the LLM narrative layer. Reads L2/L3 output,
  emits diagnostic findings. Does not gate trades.
- Detection rules use `analysis.contracts.l2_fired` and
  `l2_classification` (the single source of truth for "did the L2
  actually fire?") — never raw `pattern.get("classification")`.

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count (findings for `bug-scan`, ranked ideas for `l3-conviction-scan`, total journal entries for `daily-trade-pick`), `help[]` is contextual next-step command templates.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
