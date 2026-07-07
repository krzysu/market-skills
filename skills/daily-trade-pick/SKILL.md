---
name: daily-trade-pick
description: "Criteria-strict daily opportunity scanner — surface up to 3 picks per tick across the multi-source universe (tier 1+2 + swing shortlist + CoinGecko movers + Nansen smart money + HL narrative), with mandatory outcome tracking via journal, silent on no-find. Loads when the user asks 'any setup today', 'best 24h setup', 'what's the pick', 'how are daily picks going', 'anything we can learn from the journal', 'review the picks', or when building a similar multi-source opportunity scanner. Covers the 5-criterion uniform bar, per-source conviction gating, per-source sizing discipline, macro alignment protocol, cooldown logic, journal schema, Telegram output, silent-on-no-find discipline, AND the data-backed pitfalls from the 2026-07-06 journal review (TP1 5-10% sweet spot, stop 3-6% sweet spot, hit/miss classifier wick-touch bug, conviction≥3 floor validation, SHORT direction negative-EV, ai_infra reversal pattern, pick rate < 1/day)."
version: 0.7.0
metadata:
  hermes:
    tags: [market, scanner, pick, journal, outcome-tracking]
    category: markets
compatibility: "Writes to the path in $MARKET_SKILLS_DAILY_TRADE_PICK_PATH. Reads market-skills run-all-l3 output + BTC 4h + ETH 4h + F&G. Reads current prices via the `kraken` CLI."
---

# Daily Trade Pick

Class-level pattern for a **criteria-strict opportunity scanner** that picks up to 3 trades per cycle (top-N by R:R, capped to respect the user's attention budget) with a hard return target and mandatory outcome tracking. The reference implementation runs a daily 10:00/16:00 CEST tick on Kraken with a 24h ≥5% target, fed by a 6-source universe (thesis tiers + swing shortlist + CoinGecko movers + Nansen smart money + Hyperliquid narrative + surf-mcp cross-check). The pattern generalizes: weekly swing pick, intraday momentum pick, sector rotation pick — all use the same skeleton (multi-source fetch → L3 batch → per-source bar eval → top-N cap → journal → Telegram with Source: line → outcome close).

The hard part is **discipline**: most cycles will be silent. The bar is strict on purpose. Track every idea's outcome honestly — the journal records ALL ideas (picked, considered-but-rejected, met-bar-but-not-picked), not just the winner. Don't force-fit weak signals to fill the slot. If the existing universe is genuinely quiet across 3+ ticks, **expand the universe** (per `references/multi-source-design.md`) rather than lowering the existing bar.

## When to use

- **User asks** for a daily/weekly pick with strict criteria
- **Scheduled/automated tick** of the daily-trade-pick workflow
- **Building a similar scanner** with up-to-3-picks-per-cycle output, mandatory outcome tracking, and silent-on-no-find
- **Scan has been silent 3+ ticks and trust decays** — when the user pushes back ("your job is to find actionable trades, not to be silent"), expand the universe per `references/multi-source-design.md` rather than relaxing the existing bar. New sources get stricter gates (≥4 conviction), not looser ones; the proven thesis universe keeps its original bar. Journal every idea, cap output to top 3 by R:R, surface what the existing scan missed without diluting the existing signal.

## When NOT to use

- Live interactive trading session where the user wants to see the full landscape (use `run-watchlist` instead — returns all ideas, no picking)
- Strategy backtesting (use `kraken-backtest` — different metrics, no real-time picks)
- Picking without outcome tracking (that's an alert, not a pick — use `position-watchdog`)

## Workflow contract (per tick)

Two responsibilities, in order:

### A. Outcome check on previous scans

Read `picks.json`. For each `scan` record and each `idea` inside it where `status: "open"` AND `created_ts` is ≥ 20h ago (24h ± 4h tolerance for missed runs):

1. Get the next-bar-open price via `kraken ohlc <PAIR>USD --interval=1h`. Take the open of the first 1h candle whose timestamp is **after** the idea's `closed_at` (or after `created_ts + 24h` if `closed_at` is unset). Also fetch the 24h wick via `kraken ticker <PAIR>USD -o json` — extract `l[0]` (24h low) and `h[0]` (24h high) for hit_target evidence.
2. Compute `actual_return_pct` = (open_next - entry) / entry * 100 for longs, (entry - open_next) / entry * 100 for shorts.
3. Store `exit_price` as the next-bar-open AND record `exit_wick_low` / `exit_wick_high` from the ticker wick. The wick preserves touch-evidence for hit_target; the next-bar-open is the trader's realized fill.
4. `hit_target = (exit_wick_high >= tp1) if direction == "long" else (exit_wick_low <= tp1)` — direction-aware, based on wick touch (did price reach TP1?).
5. `outcome_verdict = "hit" if hit_target else "miss"`.
6. Update: `status: "closed"`, `closed_at`, `exit_price`, `exit_wick_low`, `exit_wick_high`, `actual_return_pct`, `hit_target`, `outcome_verdict`. Don't change `met_bar` or `picked` — those are from the original scan.
7. If the next-bar-open fetch errors, fall back to `kraken ticker` for both exit_price and wick data. If both fail: keep `outcome_verdict: "expired"` as the diagnostic, but **also set `status: "closed"` and `actual_return_pct: 0.0`, `hit_target: false`**. The `expired` status is documented in the SKILL.md schema as a terminal state, but the bundled verifier (`dtp_journal_verifier.py:108`) only accepts `status == "closed"` for the 24h-old "fully closed" check, and crashes on `actual_return_pct=None` at line 115 (`abs(None)`). Verified 2026-07-05 10:00 CEST tick: 3 HL perp-DEX ideas (hl:LIT, hl:XPL, hl:FARTCOIN) in 2026-07-04-001 had no Kraken ticker → both fetch paths failed → converted `expired` → `closed` (with `actual_return_pct=0.0`) to satisfy the verifier. `outcome_verdict="expired"` preserves the diagnostic. **Don't leave `status="expired"` and don't leave `actual_return_pct=None`** — both fail the verifier.

If the journal write recipe (`references/journal-write-recipe.md`) and the spec disagree, the spec wins — but they should not disagree. Update both in the same commit.

Read the whole JSON, modify in memory, write back atomically. Never append partial JSON.

### B. Today's scan (with optional pick)

1. **Tier list** (mirror Morning Brief): resolve live from `market-watchlist tickers tier_1 tier_2 --json`. Single source of truth — never hardcode.
2. **L3 batch** on every tier 1+2 ticker:
   ```bash
   T1=$(uv run skills/market-watchlist/scripts/run.py tickers tier_1 | tr '\n' ' ')
   T2=$(uv run skills/market-watchlist/scripts/run.py tickers tier_2 | tr '\n' ' ')
   TIERS=$(echo "$T1 $T2" | tr ' ' '\n' | sort -u | tr '\n' ' ')
   uv run skills/run-all-l3/scripts/run.py $TIERS --json > /tmp/dtp_l3.json
   ```
3. **Macro alignment** (3 signals):
   - Tier-1 bellwether A 4h trend-follow direction: `uv run skills/strategy-trend-follow/scripts/run.py <TIER_1_TICKER> --interval=4h --period=3mo --json` (resolve ticker from `market-watchlist tickers tier_1`). The `--period` flag is a kwarg, not positional — see [B2] in BUGS.md if it gets read as a ticker.
   - Tier-1 bellwether B 4h trend-follow direction: same pattern, different tier_1 ticker
   - F&G regime: use `market-macro` skill (returns `RegimeSignal` envelope) — canonical source for F&G/VIX/regime. Do NOT curl F&G separately; the skill handles it.
4. **Bar evaluation** (per-source — see `references/multi-source-design.md`):
   1. Conviction ≥ source-specific gate (tier 1+2: ≥3; swing shortlist + CoinGecko: ≥4; smart money + HL narrative: ≥3 with L3 confirms)
   2. TP1 ≥ 5% from entry (long: tp1/entry - 1 >= 0.05; short: 1 - tp1/entry >= 0.05)
   3. R:R to TP1 ≥ 1.5:1 (|tp1 - entry| / |entry - stop|)
    4. (Advisory) Direction aligns with ≥ 2 of 3 macro signals — log the
       `macro_aligned` count in the idea envelope but do NOT use it as a
       veto. The L3 conviction gate (criterion 1) does the filtering; the
       macro context is surfaced as a narrative note, not a hard rule.
       Re-evaluate after 2-4 weeks of cleaned data (post BUGS-2026-07-07-1
       and BUGS-2026-07-07-2 fixes) before deciding whether to drop,
       invert, or reinstate the gate.
   5. L3 narrative doesn't contradict the structure (e.g. "spring" with TP1 below entry)
   6. Surf-MCP cross-check — skip if RSI > 80 OR mindshare 24h change < -30%
5. **Cooldown check** — `cooldown_ok: true` if no `picked: true` on same ticker (any source) in last 24h. Cooldown applies ONLY to picking, not to bar-evaluation. Every idea gets evaluated; only the picked one is filtered by cooldown.
6. **Pick logic (top-3 cap)** — if any idea has `met_bar: true` AND `cooldown_ok: true`, pick the top 3 by R:R descending. Tie-break: conviction desc, source priority (tier 1+2 > swing_shortlist > coingecko_movers > smart_money > hl_narrative), tier (1 before 2), ticker alphabetical. Set `picked: true` on each of the top 3 and `picked: false` on the rest. Each picked idea gets `suggested_size_eur` from its source's cap (tier 1+2: EUR 200; swing + coingecko + smart money: EUR 100; hl_narrative: EUR 50 perp notional).
7. **If `met_bar` ideas exist but all fail cooldown OR none make the top-3 cap** → `[SILENT]` (no Telegram pick), but journal scan record is still written with all of them.
8. **If no idea meets the bar** → `[SILENT]`, journal written.

## Journal schema

State file: the path in `$MARKET_SKILLS_DAILY_TRADE_PICK_PATH`. Top-level is a JSON array of `scan` records, one per tick. Append on every tick — never overwrite, never skip. The `ideas` array contains EVERY idea the L3 batch produced (passed the bar OR not). `met_bar` flags whether it met the bar. `picked` flags whether the tick's selection logic surfaced it. `status` lifecycle applies to every idea, not just picked ones — outcome check closes them all.

```json
[
  {
    "type": "scan",
    "id": "YYYY-MM-DD-001",
    "created_ts": "ISO-8601 UTC",
    "ideas": [
      {
        "ticker": "kraken:ETHUSD",
"pair": "ETHUSD",
         "source": "tier1" | "tier2" | "swing_shortlist" | "coingecko_movers" | "smart_money" | "hl_narrative" | "unknown",
         "direction": "long" | "short",
         "entry_price": float,
         "stop": float,
         "tp1": float, "tp2": float, "tp3": float,
         "tp1_pct": float,
         "rr_to_tp1": float,
         "conviction": int,
         "version": "v1".."v5",
         "strategy": "strategy-trend-follow | ...",
         "narrative": "one-line from L3 narrative",
         "macro_aligned": bool,
         "met_bar": bool,
         "picked": bool,
         "rejection_reasons": ["conviction 2 < 3", "tp1_pct 3.2 < 5", ...] or [],
         "suggested_size_eur": float (only for picked),
         "cooldown_ok": bool,
         "status": "open" | "closed" | "expired",
         "closed_at": "ISO-8601 UTC" or null,
         "exit_price": float or null,
        "actual_return_pct": float or null,
        "hit_target": bool or null,
        "outcome_verdict": "hit" | "miss" | "expired" or null
      }
    ]
  }
]
```

## Telegram output

**If a pick is made:**
```
🎯 DAILY PICK — YYYY-MM-DD HH:MM CEST
Ticker: <PAIR>
Direction: <LONG|SHORT>
Entry: $X.XX (current Kraken ticker)
Stop: $X.XX (-X.X%)
TPs: [TP1 $X.XX (+X.X%), TP2 ..., TP3 ...]
24h target: TP1 +X.X% ✓
Conviction: <N> (v<N>)
Strategy: <strategy-name>
Macro aligned: <2 of 3>
Rationale: <one-line from L3 narrative>
Size: €<X> (1% of €<portfolio> / <stop_pct>% stop)
```

**If no pick:** output exactly `[SILENT]` and nothing else. No preamble. No "no opportunities today". No list of what was checked. The Telegram message IS the entire response.

**Hard format rules:**
- Max 12 lines. Plain text only.
- No `##` headers, no `---` rules, no triple-backtick fences, no tables, no nested lists.
- Pre-send scan for `|`, `##`, ```, `**`, `---` → rewrite. Telegram Web chokes on those.

## Diagnostic flow when the tick is `[SILENT]`

`[SILENT]` is not "no setup today" — it can also mean "every idea got rejected by the bar." Before concluding the market is dead, read `picks.json` and look at the `rejection_reasons`:

```bash
python3 -c "
import json
d = json.load(open('$MARKET_SKILLS_DAILY_TRADE_PICK_PATH'))
last = d[-1]
print(f'last scan ts={last[\"created_ts\"]} ideas={len(last[\"ideas\"])}')
for i in last['ideas']:
    rr = i.get('rejection_reasons', [])
    print(f'  {i[\"pair\"]:10s} {i[\"direction\"]:5s} conv={i[\"conviction\"]} | {rr if rr else \"met_bar\"}')"
```

**Three failure modes to recognize:**

1. **Real-bar failures (don't relax).** `conviction 2 < 3`, `tp1_pct 3.87 < 5`, `rr_to_tp1 < 1.5` — these are honest rejections. Don't loosen the bar to fill the slot. (Note: `macro_aligned 1/3` is no longer a bar rejection per BUGS-2026-07-07-3 — the macro gate was relaxed to advisory.)
2. **Float-precision artifact (operator decision).** `rr_to_tp1 1.499966 < 1.5` — the L3 strategy intent is exactly 1.5 by construction; the 2dp rounding on TP1 drops the recomputed ratio just below the floor. See "Bar strictness: prompt-edit alternative" below. Recurrence threshold for this artifact: 2+ consecutive ticks at the same R:R pattern → operator should consider the prompt fix; 3+ ticks → file a library-side Kanban ticket per `references/r-r-1.5x-rounding-edge-case.md`.
3. **Cooldown-blocked met_bar (silent on cooldown).** All ideas failed the bar OR were met_bar but cooldown_ok=false → log inspection needed. Read `rejection_reasons` for the cooldown case.

## Bar strictness: R:R 1.5x construction edge case (resolved 2026-06-25)

L3 strategy-trend-follow constructs TP1 = entry ± 1.5 × stop_distance.
With 2dp rounding on TP1, the recomputed R:R can slip below 1.5
(e.g. 1.499964) even though the strategy intent is exactly 1.5.
**Resolution:** library commit `d99f05d` exposes `take_profit_ideal`
(unrounded construction values). The bar's R:R check should use
`take_profit_ideal` (exact by construction) or fall back to a
tolerance formula
`abs(|tp1 - entry| - 1.5 * |entry - stop|) ≤ 1e-3 * |entry - stop|`
instead of a strict ratio. See
`references/r-r-1.5x-rounding-edge-case.md` for the full worked
examples (ETH 1d SHORT × 2 ticks, SOL counter-example, TF
specificity). If the formula check regresses, restore from git
history.

## Hard rules

1. **No auto-execution.** The pick is a recommendation. Kris reads it, decides, places via `execution-kraken-spot submit` or `execution-kraken-perps submit` manually.
2. **No fabrication.** If L3 output is empty, errors, or macro context is missing → `[SILENT]`. Do not invent prices, targets, or rationales.
3. **No guarantee language.** "trend-follow SHORT, HEALTHY_DOWNTREND" is fine. "guaranteed moonshot", "easy 5%", "can't lose" — never.
4. **Don't trust your own conviction on borderlines.** A setup where conviction=3 (floor), R:R=1.5 (floor), TP1=5.1% (floor) is probably not good enough. The 5% is the FLOOR, not the target. The R:R float-precision artifact is a separate case from this rule — see "Bar strictness: prompt-edit alternative" above.
5. **picks.json is source of truth.** Read at the start of every tick. Append a new scan record, never overwrite.
6. **Outcome check is NOT optional.** Even on silent ticks, close the open ideas from the previous tick. The calibration dataset depends on this.
7. **Silent = exact `[SILENT]` token.** Nothing else. No prose, no emoji, no whitespace. The delivery layer parses this token literally to suppress delivery.

## Verifier quirks

The bundled `scripts/verify_journal.py` enforces a tight schema on `picks.json`. Documented quirks (workarounds, not bugs in the journal writer):

- **Always write `narrative`, not `rationale` (worked 2026-07-02 DTP tick).** The skill's idea dict uses `narrative` (matching the L3 envelope); the verifier flags `missing fields: {'narrative'}` if you write `rationale`. Don't add a duplicate `rationale` field — it makes the verifier check both and existing scans fail validation.
- **R:R math FAIL on rejected ideas is informational, not an error.** When an idea's recorded `rr_to_tp1` doesn't equal `tp1_dist / (1.5 * stop_dist)` within `1e-3` tolerance, the verifier emits `FAIL: <ticker> R:R math: tp1_dist=X 1.5*stop_dist=Y diff=Z > tol=0.0001`. Since the bar already correctly rejected the idea on R:R < 1.5, the FAIL is consistent with the journal state — the verifier is double-checking that an already-rejected idea doesn't have a quietly inflated R:R. **Don't "fix" the journal by inflating the recorded R:R** — that would silently downgrade a legitimate bar rejection. Move on.
- **EXIT-1 FAIL on `met_bar=true`/`picked=true` in a NEW scan is a known bug for non-silent ticks (worked 2026-07-05 10:00 CEST tick).** `dtp_journal_verifier.py:128-133` `sys.exit(1)`s on any new idea with `met_bar=true` or `picked=true`. The verifier author assumed the only valid new-tick state is silent (all `met_bar=false, picked=false`), but this skill explicitly accommodates picks with those flags both `true`. When a non-silent tick produces a pick, you'll see `FAIL: new idea <PAIR> met_bar=true (verify picking logic)` with exit 1, even though the journal write is correct per the spec. **Do NOT "fix" by flipping `met_bar=false` on the picked idea** — that corrupts the audit trail and silently turns the legitimate bar pass into a fake rejection. Surface the FAIL as an `[INFO]` in the response and move on. The journal is correct.
- **exit-1 on `status="expired"` for ≥20h-old ideas** — covered in the "If the ticker errors" outcome-step bullet above. Always normalize `expired` → `closed` + `actual_return_pct=0.0` before writing, never leave the spec-literal `expired` status on disk.

## Pitfalls

- **HYPE bleeds across the scan universe (worked 2026-07-06 journal review).**
  Across 22 scans / 11 days / 133 ideas, HYPE accounted for 11 closed
  ideas with an **18% hit rate and −0.28% avg return** — the
  worst-performing tracked ticker in the basket. The conviction floor
  is currently keeping most HYPE ideas out of the picked set (most
  HYPE ideas emit at conv=1 or conv=2 due to the perp-DEX scaling rule),
  so the bleed is contained — but HYPE LONG 4h is still being logged
  as `met_bar=false / verdict=miss` every tick. **Recommendation:**
  remove HYPE from the tier_1 scan universe until the structural
  setup clears, or move to a `tracking_only: true` watchlist entry.
  Don't lower the conviction floor to pick up HYPE; the floor is
  correctly rejecting it. (Note: HYPE open position per the existing
  watchlist remains — this is about scan-side noise, not position
  management. Position protection stays on `position-watchdog` as is.)
- **Macro_aligned filter may be reading backwards (relaxed to advisory 2026-07-07,
   original finding 2026-07-06 journal review, N=116 closed).** Counter-macro ideas
   (`macro_aligned 1/3`) produced **+0.28% avg return**; aligned ideas (`2/3`)
   produced **−1.82% avg return** for the dominant cluster (BTC4h=short +
   ETH4h=short + F&G=long). **Resolution:** the macro_aligned gate has been
   relaxed from a hard veto to advisory per BUGS-2026-07-07-3. The L3 conviction
   gate does the filtering; macro context is surfaced as a narrative note.
   Re-evaluate after 2-4 weeks of cleaned data (post BUGS-2026-07-07-1 and
   BUGS-2026-07-07-2 fixes) before deciding whether to drop, invert, or reinstate.
- **`(unmapped)` basket dominates negative returns (2026-07-06 journal
  review).** 41 closed ideas (~35% of total) didn't map to any
  watchlist basket. That bucket has a 41% hit rate and **−1.07% avg
  return** — the largest negative-return cohort in the dataset.
  Either (a) the watchlist is drifting out of sync with the tick's
  universe (a runtime sync bug worth a separate ticket), or (b) the
  tick is scanning a wider universe than configured. Both are
  independent of the classifier issue (R3 below). Until the source is
  identified, treat every idea on a `(unmapped)` ticker as a
  research-only signal — manually map before evaluating.
- **Hit/miss classifier is two bugs, not one (fixed 2026-07-07).**
   22 of 43 "hits" (51%) had **negative actual return**; 28 of 70
   "misses" (40%) had positive actual return. The original 2026-07-06
   framing called this a single "wick-touch" bug. Re-reading the worked
   examples on 2026-07-07 revealed **two independent bugs** which have
   both been fixed in this commit:
   1. **Wick-based exit price (fix: use next-bar-open).** VVVUSD long:
      TP1 14.77, exit 12.32 — exit price was the wick, not the close.
      Fixed by fetching `kraken ohlc` next-bar-open for `exit_price`.
   2. **Direction-blind `hit_target` formula (fix: direction-aware).**
      `hit_target = abs(actual_return_pct) >= 5` misclassified every
      SHORT that lost ≥ 5% as a "hit". Fixed by making `hit_target`
      wick-touch based and direction-aware.
   Re-validate the calibration dataset after this fix. The hit-rate
   and avg-return figures cited in the pitfalls above (TP1 bands, stop
   bands, conviction, direction, basket) were contaminated; treat them
   as **directional hypotheses to re-test post-fix**.
- **Pick rate is thin (2% of ideas, 0.3/day average) — operator
  feedback signal (2026-07-06 journal review).** Across 11 days,
  133 ideas logged but only **3 picks** (2% of total). The bar is
  doing its job (every picked idea has `met_bar=true AND picked=true`
  per the spec) but the gap between "ideas generated" and "ideas
  picked" is too wide for the system to validate itself
  statistically. **Don't lower the bar** — the bar-correctly-rejected
  ideas are still useful for the calibration dataset. Two ways to
  improve signal-to-noise: (a) tighten the universe (per the HYPE
  pitfall above — fewer low-conviction ideas from sources that
  consistently miss), (b) expand the universe with sources that
  carry higher conviction gating (≥4) so the bar pass rate rises
  without relaxing the conviction floor. The "silent 3+ ticks →
  expand universe" rule in the When-to-use section is the canonical
  way to do (b); the HYPE pitfall is the canonical way to do (a).
- **TP1 sweet spot is 5-10% from entry (2026-07-06 journal review,
  N=113 ideas with classified outcome).** Hit rate and avg return by
  TP1 band:
  | TP1 band | n | Hit rate | Avg return |
  |---|---|---|---|
  | 1-3% | 5 | 20% | -2.14% |
  | 3-5% | 25 | 28% | +2.18% |
  | **5-10%** | **20** | **60%** | **+2.57%** |
  | 10-20% | 42 | 40% | -1.51% |
  | 20%+ | 21 | 29% | -1.01% |
  Below 5%, TP1 is too tight — wicks touch and reverse before TP2 is
  hit. Above 10%, the move overshoots before reaching TP1. The 5-10%
  band is the only zone where the L3 strategies deliver actionable
  signals for a multi-day-horizon trader. **Action:** when a tick
  surfaces an idea with TP1 outside 5-10%, either (a) tag it
  `rejection_reasons: ["tp1_outside_5_10_band"]` and let it sit in
  the calibration dataset, or (b) tighten the L3 envelope to
  construct TP1 inside this band (requires library-side change via
  Kanban to coding-worker — `validate_l3_tp_ladder` extension).
  Don't change the bar — the bar is right to reject them.
- **Stop sweet spot is 3-6% from entry (2026-07-06 journal review,
  N=113).** | Stop band | n | Hit rate | Avg return | | 0-3% | 27 |
  33% | -0.11% | | **3-6%** | **20** | **55%** | **+4.36%** | | 6-10%
  | 23 | 30% | +0.37% | | 10-15% | 25 | 40% | -2.81% | | 15%+ | 18 |
  33% | -0.67% | Tight stops (<3%) get wick-stopped before the
  thesis plays out. Wide stops (>10%) make the R:R collapse for the
  available TP1. The 3-6% band aligns with crypto 4h/1d candle noise
  floors — below 3%, intrabar wicks are larger than the stop
  distance. **Action:** mirror the TP1 pitfall — either tag
  `rejection_reasons: ["stop_outside_3_6_band"]` or tighten the L3
  envelope's stop construction.
- **Conviction floor (≥3) is the right bar (2026-07-06 journal
  review, N=113).** | Conv | n | Hit rate | Avg return | | 4 | 1 |
  100% | +5.05% | | **3** | **21** | **43%** | **+2.43%** | | 2 | 61
  34% | -0.75% | | 1 | 30 | 40% | ±0.00% | The +2.43% avg return at
  conv=3 is the only positive-return bucket. Below that, no edge.
  Above that, too few samples to be statistically meaningful (n=1 at
  conv=4). **Keep the conviction floor at ≥3 as-is.** Don't
  downgrade. (Caveat: the +2.43% number is contaminated by R3 below —
  re-validate after the classifier is fixed.)
- **Hit/miss classifier — two bugs (fixed 2026-07-07, see full entry at line 250).**
   Both bugs (wick-based exit price + direction-blind `hit_target`) have been fixed.
   The calibration dataset should be re-validated with clean data post-fix.
- **SHORT direction is structurally negative-EV in the current regime
  (2026-07-06 journal review, N=113).** LONG: 56 picks, 39% hit,
  **+1.37% avg**. SHORT: 57 picks, 37% hit, **-1.16% avg**. The
  hit rates are similar but the return distribution is asymmetric —
  shorts hit TP1 on bounces, then reverse before TP2. Even with
  higher avg conviction (2.0 vs 1.8 for longs), shorts lose money.
  **Three possible causes (need more data to disambiguate):** (a)
  June 2026 has been a recovery rally — shorts get squeezed, regime-
  specific not structural; (b) `strategy-trend-follow` is biased long
  in its trend classifier; (c) asymmetric stop distance — short
  stops trigger faster than long stops.   **Action:** raise the SHORT
  conviction floor to **≥4** for a 2-week probe. If hit rate
  improves and avg return turns positive, keep; if not, drop SHORT
  direction from this skill entirely. Either change is a one-line
  edit to the bar. Don't act on a single data point beyond the
  probe.
- **ai_infra basket has a 78% hit rate but -0.69% avg return
  (2026-07-06 journal review, N=9).** The ideas hit TP1 right before
  a reversal — high realized volatility makes them touch the target
  frequently but the moves are short-lived. Strategy correctly
  identifies the directional signal; timing is consistently at the
  end of the move. **Action:** either (a) widen the TP1 band for
  ai_infra picks (8-15% instead of 5-10%), or (b) drop ai_infra from
  the DTP scan universe and route it to manual-watch only. Option (b)
  is the lower-risk change — confirm with two more weeks of data
  before re-enabling with a widened band.
- **Pick rate < 1/day — pick cap is too tight (2026-07-06 review).**
  0.3 picks/day average is too thin to validate the system
  statistically (the LLM agent brain can't learn from 3 picks in 11
  days). Two paths: (a) **lower the bar** (e.g. drop conviction
  floor to ≥2 for ai_infra basket specifically — has 78% hit but
  bar-rejected); (b) **tighten the scan** to return 3-5 ranked ideas
  per tick instead of 12 unranked (cap on the runner side). Path (b)
  is the cleaner long-term fix because it preserves the
  conviction-floor guarantee. **Don't act until R1+R2+R3 land and
  the calibration data is clean** — the 37% raw hit rate is partly
  an artifact of the broken classifier, not a true signal level.

- **R:R 1.5x construction edge case (resolved 2026-06-25).** L3 strategy-trend-follow constructs TP1 = entry ± 1.5 × stop_distance. With 2dp rounding on TP1, the recomputed R:R can slip below 1.5 (e.g. 1.499964) even though the strategy intent is exactly 1.5. **Resolution:** library commit `d99f05d` exposes `take_profit_ideal` (unrounded construction values). The bar's R:R check should use `take_profit_ideal` (exact by construction) or fall back to a tolerance formula `abs(|tp1 - entry| - 1.5 * |entry - stop|) ≤ 1e-3 * |entry - stop|` instead of a strict ratio. See `references/r-r-1.5x-rounding-edge-case.md` for the full worked examples (ETH 1d SHORT × 2 ticks, SOL counter-example, TF specificity). If the formula check regresses, restore from git history.
- **Float precision on clean fractions.** Some R:R values compute to 1.500000 exactly (e.g. SOL 1d SHORT: tp1_dist=10.86, stop_dist=7.24, 10.86/7.24 = 1.5 by math). But Python's float can render 1.5 as 1.499999999... so `1.5 < 1.5` returns True at the comparator. Use a tolerance: `rr_raw < 1.5 - 1e-6` for the strict check, or compare against `1.5 * stop_dist` instead of `tp1_dist / stop_dist`. The cleanest fix is `abs(tp1_dist - 1.5 * stop_dist) > 1e-3` → fail.
- **`strategy-trend-follow` v2 emits `stop: null` (worked 2026-07-01 10:00 CEST tick).** The L3 idea envelope from `strategy-trend-follow` (versions v2, v3) does NOT include a stop price in the idea dict — only the entry/TP1/TP2/TP3/R:R bracket. The journal's `stop` field for these ideas is legitimately `null`. **Two consequences:**
  1. The bar's R:R check should use the envelope's own `rr_to_tp1` (not a back-derived stop), because the bar is verifying the *strategy's intent* not reconstructing a trader's bracket. A trend-follow idea with `rr_to_tp1 = 1.4885` is correctly rejected by the bar even though there's no explicit stop to recompute against.
  2. Any post-write journal verifier that recomputes R:R from `(entry, stop, tp1)` MUST skip the math check when `stop is None`, or it crashes with `TypeError: bad operand type for abs()`. The bundled `scripts/verify_journal.py` (and any verifier built from the same pattern) needs a `stop is not None` guard on the candidate-filter line. The skill-level R:R tolerance formula uses `1.5 * |entry - stop|`; when `stop` is None that term is undefined — skip the check entirely for trend-follow v2 ideas rather than papering over it with a synthetic stop. Synthetic stops corrupt the journal: the trend-follow envelope's R:R is 1.4885 by construction, and a back-derived stop at `1.5 * stop_dist = tp1_dist` would falsely report a 1.5 R:R and mask the legitimate bar rejection.
- **F&G regime mapping.** value < 25 → supports longs (extreme fear = "buy the dip"). value > 75 → supports shorts (extreme greed = fade). Otherwise neutral (no alignment contribution). The bar must NOT map "fear → shorts" or "greed → longs" — that's the contrarian signal.
- **Macro alignment counts dissent as 0.** For a SHORT idea, F&G = 17 (extreme fear = supports longs) is OPPOSITE the idea direction → counts as 0 alignment for that signal. The check is `signal == direction`, not `signal.opposite == direction`. Same for BTC 4h trend-follow direction vs the L3 idea's direction.
- **Cooldown applies to picking only.** Every idea gets evaluated against the bar regardless of cooldown. The state-tracker scans `picked: true` entries across all open scans in picks.json for the same ticker in the last 24h. An idea with `met_bar: true` but `cooldown_ok: false` is logged but not promoted to `[OPPORTUNITY]`.
- **L3 idea schema is in market-skills.** TradeIdea fields like `version`, `move_maturity_pct`, `entry_window_validity_pct`, `entry_range` are set by `run-all-l3` and validated by `validate_l3_tp_ladder`. The daily-trade-pick journal extracts the relevant fields (`entry_price`, `stop_loss`, `take_profit`, `conviction`, `direction`, `narrative`) — don't try to capture the full schema.
- **Surf-MCP cross-check is retired.** Surf-mcp mindshare + price-indicator was the "every-candidate filter" until 2026-06-29. Replaced by market-skills `market-snapshot` (RSI + Supertrend). If you see code or a prompt that calls `surf_market` / `surf_social` for a daily-trade-pick cross-check, that's the old shape. Update to `skills/market-snapshot/scripts/run.py <TICKER> --json`.
- **For new external sources, use market-skills before inline curl.** When expanding the universe for an opportunity scanner, check first whether `skills/` in market-skills already exposes what you need. CoinGecko movers + Kraken tradability is `skills/market-movers/scripts/run.py --json` (returns `tradable_on.altname`). Sector-rotation data might be in a market-skills strategy. If market-skills has it, use the skill — don't reinvent with curl + manual parsing. The reimplementation duplicates logic, double-exposes rate limits, and breaks when market-skills updates its schema.
- **Prompt bloat from duplicate descriptions (real failure 2026-06-29).** When adding a new source to a prompt, it's tempting to mention the source in (a) the source table, (b) the bar criteria, (c) the rejection reasons table, (d) the step-by-step explanation. Each mention is a token. The first version of the 6-source daily-trade-pick prompt ran 17,427 chars (~4,400 tokens). After consolidating each source's description to ONE table row (source name, tool, tickers, conv gate, sizing cap) and citing it elsewhere, the prompt dropped to 9,461 chars (~2,400 tokens) — **45% reduction, same behavior**. Rule: describe each source ONCE in a canonical table; refer to it elsewhere by source name only.
- **Pair format on Kraken is non-obvious.** `kraken ticker <PAIR>USD -o json` returns the response under the canonical Kraken pair key (which prepends X and Z for some pairs, not for newer ones). The outcome-step code MUST handle this — extract the first key from the response, not hardcode the input pair name. See `references/kraken-pair-lookup.md` for the full mapping table.
- **HL perp-DEX tickers can't be priced by the kraken CLI.** `kraken ticker hl:LIT` returns `rc=1` with empty stderr — the CLI doesn't know the `hl:` prefix. Outcome-step code must catch the rc=1 case explicitly and route to the `expired`-but-`closed` branch in the skill's outcome-step #6, not just blob every fetch error into "no price". Worked 2026-07-05: 3 of 8 ideas needing close (hl:LIT, hl:XPL, hl:FARTCOIN) failed this way; the other 5 (AAVE, AERO, RPL, VVV, ZEC) all priced correctly. Anti-pattern: treating all 8 fetch failures identically and missing the legitimate closes.
- **L3 TP ladder rejection can leave `ideas: []`.** If `run-all-l3` returns zero ideas for a tier-1+2 ticker, the journal records nothing for that ticker — that's fine, it means the strategy saw nothing actionable. Don't fabricate ideas to fill the journal. **Two failure modes that look identical in `ideas: []`:**
  1. *No setup* — strategy legitimately saw nothing tradeable; narrative is `Trend weakening with score 0.0` / `No clear trend direction` etc. Don't act on it.
  2. *Internal validation rejection* — strategy had a setup but `validate_l3_tp_ladder()` rejected it because TP3 fell outside `entry × {0.95, 1.05}`. Narrative starts with `"error: L3 <PAIR> <DIR> TP3 must be ≤ entry × 0.95"` or `">= entry × 1.05"`. This is a producer-side bug worth filing via Kanban to coding-worker (the strategy is computing TP3 outside its own bounds). See `market-skills-orchestration/SKILL.md` §10.4 "Second silent-failure fingerprint" for the full worked 2026-07-03 packet (6 strategies × 6 tickers, including HYPE/NEAR/ETH mean-reversion and BTC/BNB accumulation-swing). Capture the rejected idea's entry/stop/TP1 from the error string itself — `narrative` carries the formula data — so the cron can still surface as a `[BUG]` rather than going silent.
- **`hl_narrative` volume filter needs `metaAndAssetCtxs`, not `meta` (worked 2026-07-02 tick).** The skill says to call `curl -s 'https://api.hyperliquid.xyz/info' -H 'Content-Type: application/json' -d '{"type":"meta"}'` then "filter 24h vol > $20M" — but `meta` only returns the universe (name list, no volume). The canonical fetch for 24h volume is `{"type":"metaAndAssetCtxs"}`, which returns `[meta, [ctx_per_asset]]` where each ctx has `dayNtlVlm` (24h notional volume in USD) and `dayBaseVlm` (24h base volume). Worked filter:
  ```python
  req = {"type": "metaAndAssetCtxs"}
  out = json.loads(urllib.request.urlopen(req, timeout=10).read())
  meta = out[0]["universe"]; ctxs = out[1]
  by_name = {meta[i]["name"]: ctxs[i] for i in range(len(meta))}
  candidates = [(n, float(c["dayNtlVlm"])) for n, c in by_name.items()
                 if float(c.get("dayNtlVlm", 0)) > 20_000_000]
  ```
  Then exclude the tier-1+2 names and run L3 on the rest. This is the producer-side fix; until the HL filter helper lands, callers wrap `meta` + a separate `allMids` lookup for prices, which works but doesn't filter by 24h volume — it returns the full universe and L3 discards most of it.
- **Don't reorder tier-1 and tier-2 in the prompt.** The L3 batch order doesn't affect signal generation, but the tier_map used for tie-break ordering DOES matter when two met_bar ideas have the same conviction and R:R. Tier 1 before tier 2 — the highest-priority assets get the tie-break advantage.
- **Playbook: sentiment-vs-structure (risk-engine skill).** Before sizing any idea where the macro fear reading is at extremes and structure is ambiguous, re-read `risk-engine/references/sentiment-vs-structure.md` — extreme fear is contrarian only when structure isn't broken, and borderline-zone judgment should lean cautious.
- **Playbook: regime-bias (market-skills skill).** Don't tune any pick-generation threshold based on aggregate statistics from a single-regime sample — segment first, separate structural from regime findings. See `market-skills/references/playbooks/regime-bias.md`.

## Versioning

v0.7.0 — added 7 data-backed pitfalls to the Pitfalls section from the 2026-07-06 journal review (11 days, 22 scans, 133 ideas, N=113 closed): TP1 5-10% sweet spot (60% hit / +2.57% avg), stop 3-6% sweet spot (55% hit / +4.36% avg), hit/miss classifier wick-touch bug (51% false hits), conviction≥3 floor validation (+2.43% avg), SHORT direction structural negative-EV (-1.16% avg), ai_infra basket reversal pattern (78% hit but -0.69% avg), pick rate < 1/day. Description updated with "how are daily picks going" / "anything we can learn" / "review the picks" triggers so future sessions auto-load this skill for journal-review questions. Also: HYPE bleed pitfall and Macro_aligned inversion pitfall already present from earlier reviews.

## References

- `references/r-r-1.5x-rounding-edge-case.md` — worked ETH 1d SHORT example (entry=1644.0, stop=1783.33, TP1=1435.01 → R:R=1.499964) + SOL counter-example (clean fractions, float precision quirk) + decision rule for borderline calls.
- `references/journal-write-recipe.md` — read-modify-write atomic update pattern for picks.json, including how to handle the empty-array initialization case (first tick ever).
- `references/multi-source-design.md` — the 6-source framework pattern (tier 1+2 + swing shortlist + CoinGecko + Nansen smart money + HL narrative + surf-mcp cross-check). Per-source conviction gates, per-source sizing, top-3 cap with per-source priority tie-break. Reference design for any opportunity scanner, not just daily-trade-pick.
- `references/kraken-pair-lookup.md` — canonical analysis-ticker → Kraken pair-key mapping (e.g. `ETH-USD` → `XETHZUSD`, `HYPE-USD` → `HYPEUSD`) with drop-in Python lookup function. Use in batch-fetch Python for outcome-step pricing; avoids per-call `next(iter(data))` parsing.
- `references/dtp-journal-verifier-shape.md` — every FAIL line the bundled `dtp_journal_verifier.py` emits, with line numbers, triggers, and how to respond. Use this when the verifier exits 1 after a non-silent tick. **(added 2026-07-05)**
- `scripts/verify_journal.py` — re-runnable ad-hoc verifier for the journal write. Checks JSON parseability, scan envelope, required idea fields, age-bucketed status (24h+ must be closed, <20h must be open), and picked-requires-met_bar invariant. Run after every journal write (see Verifier quirks above).
- `scripts/analyze_journal.py` — offline journal analyzer. Run when the user asks "how are daily picks going" or "anything we can learn". Aggregates by hit/miss, ticker, direction, conviction, macro alignment, and day. Surfaces the actionable cuts (which tickers to drop, which filters are inverted, what the pick rate looks like). Pure stdout, no journal writes.

## L3 envelope iteration recipe (added 2026-07-03)

`run-all-l3 --json` returns this shape — DO NOT assume `tickers[T] = list[idea]`. Verified end-to-end on 2026-07-03:

```json
{
  "interval": "1h",
  "period": "1mo",
  "tickers": {
    "BTCUSD": {
      "ticker": "BTCUSD",
      "strategies": {
        "strategy-trend-follow":      {"ideas": [...], "narrative": "..."},
        "strategy-mean-reversion":    {"ideas": [...], "narrative": "..."},
        "strategy-breakout-confirm":  {"ideas": [...], "narrative": "..."},
        "strategy-accumulation-swing":{"ideas": [...], "narrative": "..."},
        "strategy-exhaustion-fade":   {"ideas": [...], "narrative": "..."},
        "strategy-liquidity-sweep":   {"ideas": [...], "narrative": "..."}
      }
    }
  }
}
```

The correct iteration recipe (drop into eval scripts):

```python
all_ideas = []
for ticker, tdata in d['tickers'].items():
    for strat_name, strat in tdata['strategies'].items():
        for idea in strat.get('ideas', []):
            idea['_ticker'] = ticker
            idea['_strategy_name'] = strat_name
            all_ideas.append(idea)
```

Two failure modes this avoids:
1. **`d[ticker]` is a dict, not a list of ideas.** Naively iterating `for idea in d[ticker]` would surface strategy names as fake "ideas".
2. **`ideas` are nested two levels deep.** `ticker → strategy_name → ideas[]`. The strategy dict also carries a `narrative` string even when `ideas == []`, so a naive flatten would surface that string as an idea. Always check `isinstance(strat, dict)` and `isinstance(idea, dict)` defensively.

The `--json list` shape differs from `tickers <basket>` shape — `tickers` returns a flat JSON array of strings (the actual ticker list), not the per-strategy envelope. Different commands, different shapes; don't conflate.

> **BUGS.md B5:** the SKILL.md used to call `run-all-l3` for the L3 batch and the consumer had to guess the iteration shape. `l3-conviction-scan --json` returns the same ideas pre-iterated in a flat `ideas: [...]` array — easier for batch bar evaluation. See `references/l3-conviction-scan-vs-run-all-l3.md` (or just use `l3-conviction-scan` for this skill's tick).

## Reference recipes

### Macro alignment — extract direction from `strategy-trend-follow --json` (BUGS.md B3)

The bellwether `strategy-trend-follow --json` output is `{"ideas": [...]}`. A fresh direction read looks like:

```bash
uv run skills/strategy-trend-follow/scripts/run.py BTCUSD --interval 4h --period 3mo --json \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
ideas = d.get('ideas', [])
if ideas:
    print(ideas[0]['direction'])
else:
    print('NEUTRAL')
"
```

If the strategy has no idea for the bellwether, the script prints `NEUTRAL` — counts as 0 alignment in the macro check (see `Macro alignment counts dissent as 0` above). The `NEUTRAL` case is the common one for ETH 4h in mixed regimes.

### Universe expansion when tier_1+2 is silent (BUGS.md B8)

When tier_1+2 produces no `met_bar` ideas (or they're all in cooldown), expand to tier_1+2+3:

```bash
T1=$(uv run skills/market-watchlist/scripts/run.py tickers tier_1 | tr '\n' ' ')
T2=$(uv run skills/market-watchlist/scripts/run.py tickers tier_2 | tr '\n' ' ')
T3=$(uv run skills/market-watchlist/scripts/run.py tickers tier_3 | tr '\n' ' ')
TIERS=$(echo "$T1 $T2 $T3" | tr ' ' '\n' | sort -u | tr '\n' ' ')
uv run skills/run-all-l3/scripts/run.py $TIERS --json > /tmp/dtp_l3_expanded.json
```

Then re-run the bar from the L3 envelope. **Don't lower the bar** — expansion is the only sanctioned path. If expansion is also silent, the universe is genuinely quiet and the correct answer is `[SILENT]`.

### FX pitfall (BUGS.md B6) — `rr_to_tp1` is quote-invariant, prices are not

`rr_to_tp1` is a ratio — it's the same number in any quote (USD, EUR, BTC, ...). The **absolute prices** in the idea envelope (`entry_price`, `stop_loss`, `take_profit`) are in the source ticker's quote (USD for `kraken:` providers, USD for `yf:` providers, USD for most `hl:` pairs).

When sizing the position in a wallet currency that differs from the source quote (e.g. EUR wallet, USD source):

1. The bar's `rr_to_tp1 ≥ 1.5` check is unchanged — it's a ratio, not a price.
2. The headline `rr_to_tp1` (e.g. 2.5:1) is correct in source-quote terms and correct in wallet-currency terms as a ratio.
3. The **position-size math** must convert `entry_price` / `stop_loss` / `take_profit` to wallet currency using the current FX rate before computing EUR-denominated `risk_per_unit` and `reward_per_unit`.
4. The R:R ratio of (wallet-denominated reward) / (wallet-denominated risk) is the same number as the source-quote R:R — currencies cancel in the ratio. But the per-unit EUR `risk` and `reward` numbers are not what the envelope shows; they're `envelope_price / FX_rate`.

Worked example (HYPEUSD → EUR wallet at FX 1.1441): entry 71.34 → €62.35, stop 69.48 → €60.73, TP1 76.20 → €66.61. Source-quote R:R = (76.20 - 71.34) / (71.34 - 69.48) = 4.86/1.86 = 2.61:1. EUR-quote R:R = (66.61 - 62.35) / (62.35 - 60.73) = 4.26/1.62 = 2.63:1. Same ratio, different absolute numbers.

**Pitfall:** Sizing against the headline R:R without converting prices can lead to over-sizing. The headline tells you the *quality* of the setup (good, 2.6:1); the per-unit risk in wallet currency tells you the *position size* (use that, not the USD number).

## Macro-dominant rejection cluster (updated 2026-07-07 — macro gate is advisory)

A full-tape `[SILENT]` driven entirely by macro alignment (every idea has `macro_aligned 1/3`) is a recurring regime pattern worth recognizing: extreme-fear F&G supports longs while bellwether BTC 4h trend-follow is short, with ETH 4h having no idea (counts as 0). Net result: longs get 1/3 (F&G only), shorts get 1/3 (BTC only). Since the macro gate is now advisory (BUGS-2026-07-07-3), this pattern alone no longer blocks picks — the L3 conviction gate does the filtering. If the tick is still silent with macro 1/3 ideas, inspect `rejection_reasons` for non-macro bars (conviction, TP1, R:R, cooldown).

Detection: in `picks.json`, if the latest scan has `met_bar: false` for ≥80% of ideas AND the dominant `rejection_reasons` exclude `macro_aligned` entries, the bar is honestly rejecting on non-macro criteria — don't add more sources.

## Quick reproduction

```bash
# 1. Initialize journal file (first run only) — pick any writable path
# and export MARKET_SKILLS_DAILY_TRADE_PICK_PATH="/your/path/picks.json"
mkdir -p "$(dirname "$MARKET_SKILLS_DAILY_TRADE_PICK_PATH")"
echo '[]' > "$MARKET_SKILLS_DAILY_TRADE_PICK_PATH"

# 2. Run one tick manually (testing)
cd <repo-root>
uv run skills/run-all-l3/scripts/run.py BTC-USD ETH-USD HYPE-USD NEAR-USD ZEC-USD hl:LIT SOL-USD XMR-USD PAXG-USD --json > /tmp/dtp_l3.json
# ... (inspect /tmp/dtp_l3.json, evaluate bar, write journal — see references/journal-write-recipe.md)

# 3. Verify journal
python3 -c "import json; d=json.load(open('$MARKET_SKILLS_DAILY_TRADE_PICK_PATH')); print(f'Scans: {len(d)}, latest met_bar: {sum(1 for i in d[-1][\"ideas\"] if i[\"met_bar\"])}, picked: {sum(1 for i in d[-1][\"ideas\"] if i[\"picked\"])}')"
```

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count (findings for `bug-scan`, ranked ideas for `l3-conviction-scan`, total journal entries for `daily-trade-pick`), `help[]` is contextual next-step command templates.

## Home view (no-arg mode)

Running this skill with no args prints the home view (last cached
state from `$XDG_DATA_HOME/market-skills/<skill>_last.json`) instead
of a usage error. `render_home_view()` is the underlying helper;
`cache_run_result(__file__, result)` writes the cache after every
successful run. Errors (`"error"` key in the result) are NOT
cached — the home view always reflects the last healthy run.
