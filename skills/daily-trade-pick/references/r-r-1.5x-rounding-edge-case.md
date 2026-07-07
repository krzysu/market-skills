# R:R 1.5x Construction Edge Case — RESOLVED

**Status: RESOLVED 2026-06-25** by two paired changes:

1. **Library fix** — commit `d99f05d fix: expose unrounded TP1 construction as take_profit_ideal for precise R:R checks` in `market-skills`. Adds `take_profit_ideal` field (unrounded `[entry ± risk * {1.5, 2.5, 4}]`) to all L3 idea blocks. Test fixture included (per the per-fix fixture rule).
2. **Consumer fix** — the cron prompt for job_id `092ace8cd3ff` (bar criterion 3 in the inlined prompt) now uses the formula check variant instead of the strict ratio check.

Both layers close the artifact at the source (library) and at the consumer (cron). This document is preserved as the historical archive — the worked examples, decision rules, and counter-examples are useful background if the artifact ever recurs (regression test material).

---

**Verified 2026-06-24 daily-trade-pick tick on tier 1+2 universe. Re-verified 2026-06-25 (2nd consecutive tick on ETH 1d SHORT trend-follow).**

The L3 `strategy-trend-follow` builds TP1 as `entry ± 1.5 × stop_distance`. With 2-decimal-place rounding on TP1, the computed R:R can slip to 1.499964 — just below the 1.5 floor — even though the strategy's underlying intent is exactly 1.5:1. This document captures the worked example, the SOL clean-fraction counter-example, and the decision rule for borderline calls.

## The construction

For a SHORT idea:
- `stop_distance = stop_loss - entry_price`
- `TP1 = entry_price - 1.5 × stop_distance`
- `TP2 = entry_price - 2.5 × stop_distance`
- `TP3 = entry_price - 4.0 × stop_distance`

For a LONG idea, the signs flip. By construction, R:R to TP1 is exactly 1.5:1.

The bar in the daily-trade-pick spec is `R:R to TP1 ≥ 1.5:1`. The strict check uses the JSON-serialized `take_profit[0]` value, which has been rounded to 2dp by the L3 batch runner. If the unrounded TP1 ideal (e.g. 1435.005) rounds to a value that doesn't match the formula exactly (1435.01), the strict R:R calculation drops below 1.5.

## Worked example: ETH 1d SHORT (fails by 0.0004%)

From the 2026-06-24 14:00 UTC tick (the first daily-trade-pick run):

```json
{
  "pair": "ETH-USD",
  "direction": "short",
  "entry_price": 1644.0,
  "stop_loss": 1783.33,
  "take_profit": [1435.01, 1295.68, 1086.69]
}
```

Computed:
- `stop_distance = 1783.33 - 1644.0 = 139.33`
- `TP1_distance = 1644.0 - 1435.01 = 208.99`
- `R:R = 208.99 / 139.33 = 1.499964`

The underlying TP1 ideal: `1644.0 - 1.5 × 139.33 = 1644.0 - 208.995 = 1435.005`. Rounded to 2dp: `1435.01` (up) or `1435.00` (down). The L3 batch serializes `1435.01` (rounds half-up). With `tp1 = 1435.01`:
- `208.99 / 139.33 = 1.499964` ← just below 1.5

**Verdict:** `met_bar: false`, rejection_reason `"rr_to_tp1 1.499964 < 1.5 (tp1_dist=208.9900 stop_dist=139.3300)"`.

The other bar criteria passed cleanly:
- conviction 3 = 3 (floor)
- tp1_pct 12.71% ≥ 5% (well above)
- macro 2/3 (BTC 4h SHORT + ETH 4h SHORT aligned; F&G 17 EXTREME FEAR dissents)
- narrative consistent

This is "double-floor" territory: conviction exactly at the floor (3) AND R:R at the floor (1.5) AND F&G dissent. The spec's explicit warning applies: "A borderline setup (TP1 = 5.1%, R:R = 1.51) is probably not good enough. The 5% is the FLOOR, not the target."

**Decision:** call it as `met_bar: false`. Don't round-up the math to let it pass. The `rejection_reasons` should note the rounding artifact for future analyst reference (so a code-fix candidate is visible if it ever becomes a recurring pest).

## Counter-example: SOL 1d SHORT (fails by float precision)

Same tick, SOL 1d SHORT:

```json
{
  "pair": "SOL-USD",
  "direction": "short",
  "entry_price": 68.77,
  "stop_loss": 76.01,
  "take_profit": [57.91, 50.67, 39.81]
}
```

Computed:
- `stop_distance = 76.01 - 68.77 = 7.24`
- `TP1_distance = 68.77 - 57.91 = 10.86`
- `R:R = 10.86 / 7.24 = 1.500000` (formatted) / `1.49999999...` (raw float)

`10.86 / 7.24 = 1.5` exactly in decimal. But `10.86` in IEEE 754 float is `10.8600000000000006...` and `7.24` is `7.2400000000000002...`. The division rounds to `1.5000000000000002` or `1.4999999999999998` depending on rounding direction.

A naive strict check `if rr_raw < 1.5: reject` can return True at this exact value due to float representation. The SOL idea also failed conviction (conv=2), so the R:R rejection was secondary in practice, but the float quirk is real.

**Fix pattern:**
```python
# Strict check with tolerance
if rr_raw < 1.5 - 1e-6:
    rejections.append(f'rr_to_tp1 {rr_raw:.6f} < 1.5')

# Or compare directly to construction
expected_tp1_dist = 1.5 * stop_dist
if abs(tp1_dist - expected_tp1_dist) > 1e-3:
    rejections.append(f'tp1_dist {tp1_dist} != 1.5 × stop_dist {expected_tp1_dist}')
```

## Decision rule for borderline calls

When `met_bar: true` candidates have ALL criteria at exactly the floor (conviction=3, R:R=1.5, TP1≈5%), the spec's "borderline is probably not good enough" warning applies. With the formula check installed (2026-06-25) and `take_profit_ideal` exposed, the rules are:

1. If R:R is exactly 1.5 by clean math (no rounding artifact) and TP1 has substantial headroom (≥10%) → call as `met_bar: true`. Pick it.
2. If R:R reads as < 1.5 due to 2dp rounding (1.499964) → under the installed formula check, **pass** (gap ≤ 0.1% of stop_dist). The artifact is now a true positive — it's the strategy's intended 1.5 by construction. Apply the spec's "double-floor" warning instead: if conviction is also at floor (3) AND F&G dissents, this is borderline-territory — the cron will log it as `met_bar: true` but the operator should scrutinize before manually promoting to `picked`.
3. If R:R is exactly 1.5 by float (1.500000 formatted but 1.4999... raw) → formula check passes regardless. No special handling needed.

The principle: **the formula check validates against the construction, not the recomputed ratio — pass when the strategy intent is 1.5, regardless of TP precision.** The bar is still the floor, and a setup at the floor is not the target — the `met_bar` flag is the bar's verdict, not a "go" signal.

## Code-fix candidate (Kanban ticket) — RESOLVED

**Resolved 2026-06-25** by commit `d99f05d fix: expose unrounded TP1 construction as take_profit_ideal for precise R:R checks`. The chosen approach was option (3) from below — compute R:R from the unrounded strategy formula rather than from the serialized TP1 value. The field `take_profit_ideal` carries the construction values directly; downstream consumers (including the cron via the formula check) can validate by construction.

For the historical archive, the original three candidate fixes were:

1. ~~Loosen `validate_l3_tp_ladder` to accept R:R ≥ 1.5 - 1e-3 (allows the rounding artifact through)~~ — not chosen; loosens the bar at the validator layer, doesn't expose construction.
2. ~~Switch the L3 batch serializer from 2dp to 3dp on TP values for the strategy-trend-follow ladder~~ — not chosen; 3dp still has the same artifact class, just smaller.
3. ~~Compute R:R from the unrounded strategy formula (entry ± 1.5 × stop_distance) rather than from the serialized TP1 value~~ — **chosen**. Field name: `take_profit_ideal`. Schema change in `analysis/contracts.py`.

Original scope estimate: ~20 lines. Final implementation: ~15 lines in `skills/strategy-trend-follow/lib.py` (3 idea blocks) + ~5 lines in `analysis/contracts.py` (TypedDict field) + 216-line test fixture in `tests/test_strategy_trend_follow.py` (per the per-fix fixture rule).

## Cross-references

- market-skills pitfalls → "Standard L3 TP ladder mathematically incompatible with most swing-mode bucket R:R floors" — documents the 1.5x/2.5x/4x construction but doesn't note the 2dp rounding slippage.
- market-skills pitfalls → "Pattern B can self-heal between ticks" — same class of "transient state masquerading as classifier bug". The R:R rounding is construction-driven, not state-driven.
- daily-trade-pick SKILL.md → "Hard rules" → "Don't trust your own conviction on borderlines" — the umbrella rule that the worked ETH example instantiates.

## Re-verification 2026-06-25 (2nd consecutive tick)

ETH 1d SHORT trend-follow fired again on the 2026-06-25 08:00 UTC daily-trade-pick tick with the same float-rounding artifact:

```json
{
  "pair": "ETH-USD",
  "direction": "short",
  "conviction": 3,
  "entry_price": 1656.8,
  "stop": 1804.89,
  "tp1": 1434.67,
  "tp2": 1286.59,
  "tp3": 1064.46
}
```

Computed:
- `stop_distance = 1804.89 - 1656.8 = 148.09`
- `TP1_distance = 1656.8 - 1434.67 = 222.13`
- `R:R = 222.13 / 148.09 = 1.499966`

Underlying TP1 ideal: `1656.8 - 1.5 × 148.09 = 1656.8 - 222.135 = 1434.665`. Rounded to 2dp: `1434.67` (rounds up half). With `tp1 = 1434.67`:
- `222.13 / 148.09 = 1.499966` ← just below 1.5 (same artifact class as 2026-06-24)

**Verdict:** `met_bar: false`, rejection_reason `"rr_to_tp1 1.499966 < 1.5 (tp1_dist=222.1300 stop_dist=148.0900)"`.

This is the **same artifact as 2026-06-24**: TP1 ideal (1434.665) rounds to 1434.67 at 2dp, but the rounded value's distance from entry (222.13) doesn't exactly equal 1.5 × stop_distance (222.135) — the rounding drops 0.005 off the ideal, computed R:R drops from 1.5 to 1.499966. Different specific numbers, same failure mode.

**Recurrence count: 2 consecutive ticks on ETH 1d SHORT trend-follow.** One more tick at the same artifact → file a Kanban ticket per the "Code-fix candidate" section above.

Other bar criteria for this tick:
- conviction 3 (floor)
- tp1_pct 13.41% (well above 5%)
- macro 2/3 (BTC 4h SHORT + ETH 4h SHORT align; F&G 12 EXTREME FEAR dissents)
- narrative consistent

Same "double-floor" pattern as 2026-06-24: conviction at floor (3) AND RR at floor (1.5) AND F&G dissent. The cron's [SILENT] result is correct.

**Cross-check note:** the ETH 4h trend-follow (used for macro alignment, separately fetched with `--interval=4h`) returned different values (entry=1656.2, stop=1727.06, TP1=1549.91, R:R=1.5 by clean math). The artifact is **TF-specific**: the 1d L3 batch fires the float-rounding slippage, the 4h variant doesn't. This is because the 1d version constructs TP1 from a longer trend envelope, producing a stop_distance (148.09) that, when multiplied by 1.5 and subtracted from entry, yields a value with 3 decimal places (1434.665) that doesn't survive 2dp rounding exactly. The 4h version's stop_distance (70.86) yields TP1 ideal 1549.91 exactly, no rounding slip.

**Updated recurrence count for tracking:** 2026-06-24 (tick 1) → 2026-06-25 (tick 2). Threshold for Kanban ticket: 3 consecutive ticks. If 2026-06-26 also fails, file a ticket.

## Prompt-edit alternative — INSTALLED

**Status 2026-06-25: formula variant is now the installed default**, not operator choice. The strict ratio check was replaced on the same day the library fix landed. Kept here as a reference for what the prompt now says and what the rejected variants looked like.

**File:** the host's `cron/jobs.json` → job `092ace8cd3ff` → prompt bar criterion 3 (line 81 of the inlined prompt).

**Installed text:**
```
3. R:R to TP1 ≥ 1.5:1 — pass if `abs(|tp1 - entry| - 1.5 * |entry - stop|) <= 1e-3 * |entry - stop|` (formula check, tolerant to TP ladder rounding precision; matches the trend-follow construction `TP1 = entry ± 1.5*stop_dist`)
```

**Original (strict, now removed):**
```
3. R:R to TP1 ≥ 1.5:1 (|tp1 - entry| / |entry - stop|)
```

**Tolerance variant (rejected):**
```
3. R:R to TP1 ≥ 1.5:1 — pass if `|tp1 - entry| / |entry - stop| ≥ 1.5 - 1e-6` (tolerate 2dp-rounding drift)
```

The formula variant won because it validates against the construction, not the recomputed ratio — exact by construction when `take_profit_ideal` is exposed, and still tolerant to 2dp rounding within a 0.1% relative band. Future agents restoring the prompt after a regression should keep this variant.

**Today's bar evaluation under the formula variant (re-confirmation):** ETH 1d SHORT entry=1656.8 stop=1804.89 TP1=1434.67 → `abs(222.13 - 1.5*148.09) = abs(222.13 - 222.135) = 0.005`. Threshold: `1e-3 * 148.09 = 0.14809`. `0.005 ≤ 0.14809` → **PASS**. Same idea flips from `met_bar: false` to `met_bar: true` under the formula variant. With `take_profit_ideal` exposed, the formula check has access to `[1656.8 - 1.5*148.09, ...] = [1434.665, ...]` and computes a gap of exactly 0 → PASS with zero tolerance floor.

## Postscript — `rr_to_tp` precomputed (2026-06-25, `f97079a`)

Same day, follow-up library commit `f97079a feat: precompute rr_to_tp on L3Idea for consumer-agnostic R:R access` goes one step further: every L3 idea now carries `rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]` directly (`analysis.contracts.compute_rr_to_tp()`). Source of truth: `take_profit_ideal` first (exact construction), `take_profit` fallback (2dp-rounded). Now any consumer — cron, swing-scan, position-watchdog, agent brain — can read `idea["rr_to_tp"][0]` without reimplementing the direction-asymmetric formula or doing the `|tp1 - entry| - 1.5*|entry - stop|` subtraction. This obsoletes the formula variant: the cron bar at jobs.json line 81 could migrate to `rr_to_tp[0] >= 1.5` exact. Pending user sign-off; current formula check stays as the shipped default.