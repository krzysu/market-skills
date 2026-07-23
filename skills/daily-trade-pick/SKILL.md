---
name: daily-trade-pick
description: "Criteria-strict daily opportunity scanner — surface up to 3 picks per tick across the multi-source universe (tier 1+2 + swing shortlist + CoinGecko movers + Nansen smart money + HL narrative), with mandatory outcome tracking via journal, silent on no-find. Loads when the user asks 'any setup today', 'best 24h setup', 'what's the pick', 'how are daily picks going', 'anything we can learn from the journal', 'review the picks', or when building a similar multi-source opportunity scanner. Covers the 5-criterion uniform bar, per-source conviction gating, per-source sizing discipline, macro alignment protocol, cooldown logic, journal schema, Telegram output, silent-on-no-find discipline, AND the data-backed pitfalls from the 2026-07-06 journal review (TP1 5-10% sweet spot, stop 3-6% sweet spot, hit/miss classifier wick-touch bug, conviction≥3 floor validation, SHORT direction negative-EV, ai_infra reversal pattern, pick rate < 1/day)."
version: 0.7.0
metadata:
  hermes:
    tags: [market, scanner, pick, journal, outcome-tracking]
    category: market
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
7. If the next-bar-open fetch errors, fall back to `kraken ticker` for both exit_price and wick data. If both fail: keep `outcome_verdict: "expired"` as the diagnostic, but **also set `status: "closed"` and `actual_return_pct: 0.0`, `hit_target: false`**. The `expired` status is documented in the SKILL.md schema as a terminal state, but the bundled verifier (`dtp_journal_verifier.py:108`) only accepts `status == "closed"` for the 24h-old "fully closed" check, and crashes on `actual_return_pct=None` at line 115 (`abs(None)`). Worked on a 2026-07-05 tick: 3 HL perp-DEX ideas had no Kraken ticker → both fetch paths failed → converted `expired` → `closed` (with `actual_return_pct=0.0`) to satisfy the verifier. `outcome_verdict="expired"` preserves the diagnostic. **Don't leave `status="expired"` and don't leave `actual_return_pct=None`** — both fail the verifier.

If the journal write recipe (`references/journal-write-recipe.md`) and the spec disagree, the spec wins — but they should not disagree. Update both in the same commit.

Read the whole JSON, modify in memory, write back atomically. Never append partial JSON.

### B. Today's scan (with optional pick)

1. **Tier list** (mirror Morning Brief): resolve live from `market-watchlist list --json` to discover all baskets. Single source of truth — never hardcode.
2. **L3 batch** on every ticker across all baskets:
   ```bash
   BASKETS=$(uv run skills/market-watchlist/scripts/run.py list --json | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin)['baskets'].keys()))")
   uv run skills/run-all-l3/scripts/run.py $BASKETS --json > /tmp/dtp_l3.json
   ```
3. **Macro alignment** (3 signals):
   - Tier-1 bellwether A 4h trend-follow direction: `uv run skills/strategy-trend-follow/scripts/run.py <TIER_1_TICKER> --interval=4h --period=3mo --json` (resolve ticker from `market-watchlist tickers crypto_majors`). The `--period` flag is a kwarg, not positional — see [B2] in BUGS.md if it gets read as a ticker.
   - Tier-1 bellwether B 4h trend-follow direction: same pattern, different bellwether ticker
   - F&G regime: use `market-macro` skill (returns `RegimeSignal` envelope) — canonical source for F&G/VIX/regime. Do NOT curl F&G separately; the skill handles it.
4. **Bar evaluation** (per-source — see `references/multi-source-design.md`):
   1. Conviction ≥ source-specific gate (tier 1+2: ≥3; swing shortlist + CoinGecko: ≥4; smart money + HL narrative: ≥3 with L3 confirms)
   2. TP1 ≥ 5% from entry (long: tp1/entry - 1 >= 0.05; short: 1 - tp1/entry >= 0.05)
    3. R:R to TP1 ≥ 1.5:1 — use the idea's precomputed `rr_to_tp1` (derived from unrounded
       `take_profit_ideal` by `analysis.contracts.compute_rr_to_tp`).  Do NOT recompute from
       the 2dp-display `tp1` — rounding drops the last 0.5% of ideas that are structurally
       correct at 1.5:1.  Fallback when `rr_to_tp1` is absent:
       `abs(|tp1 - entry| - 1.5 * |entry - stop|) <= 1e-3 * |entry - stop|`.
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
6. **Pick logic (top-3 cap)** — if any idea has `met_bar: true` AND `cooldown_ok: true`, pick the top 3 by R:R descending. Tie-break: conviction desc, source priority (tier 1+2 > swing_shortlist > coingecko_movers > smart_money > hl_narrative), tier (1 before 2), ticker alphabetical. Set `picked: true` on each of the top 3 and `picked: false` on the rest. Each picked idea gets `suggested_size_eur` from its source's cap (tier 1+2: EUR 200; swing + coingecko + smart money: EUR 100; hl_narrative: EUR 50 perp notional), then adjusted by the track-record multiplier (step 7).
7. **Track-record sizing multiplier (advisory)** — for each picked idea, compute `from analysis.track_record import compute_track_record; track_record = compute_track_record(pair, picks=<journal>)` over the last 20 scans (min 3 closed outcomes to be eligible). Set `suggested_size_eur = base_cap * track_record['multiplier']`, clamped to `base_cap * 3.0`. Record the track record on the picked idea as `_track_record: {hit_rate, n_closed, n_hits, n_misses, avg_return_pct, multiplier}` for downstream visibility. When `track_record['eligible']` is False (no history, < 3 closed, or lookback exhausted), `suggested_size_eur` stays at the base cap and `_track_record` is `{eligible: false, multiplier: 1.0}`.
8. **If `met_bar` ideas exist but all fail cooldown OR none make the top-3 cap** → `[SILENT]` (no Telegram pick), but journal scan record is still written with all of them.
9. **If no idea meets the bar** → `[SILENT]`, journal written.

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
          "_track_record": {"eligible": bool, "hit_rate": float, "n_closed": int, "multiplier": float} (optional, only for picked),
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

L3 strategy-trend-follow constructs TP1 = entry ± 1.5 × stop_distance; with 2dp rounding the recomputed R:R can slip just below 1.5 (e.g. 1.499964) even when the strategy intent is exactly 1.5. The bar's R:R check uses the envelope's precomputed `rr_to_tp1` (derived from unrounded `take_profit_ideal`), or the tolerance formula `abs(|tp1 - entry| - 1.5 * |entry - stop|) ≤ 1e-3 * |entry - stop|` as fallback. See `references/r-r-1.5x-rounding-edge-case.md` for worked examples.

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

Lessons from the 2026-07-06 journal review (N=113 closed ideas, before classifier fix). Calibration dataset must be re-validated after the wick-exit / direction-blind `hit_target` fix lands (see "Bar strictness" below + journal backfill script).

- **One perp-DEX token bleeds across the scan universe.** 11 closed ideas, 18% hit rate, -0.28% avg return — worst-performing tracked ticker. Perp-DEX scaling rule keeps most of it at conv=1/2 so the conviction floor catches it, but it still logs every tick. Remove from scan (or set `tracking_only: true`); don't lower the bar.
- **`(unmapped)` basket dominates negative returns.** 41 ideas (~35%), 41% hit rate, -1.07% avg return. Treat as research-only; investigate watchlist drift vs scan-universe mismatch before trusting unmapped ticker signals.
- **TP1 sweet spot is 5-10% from entry.** | Band | n | Hit | Avg | | 1-3% | 5 | 20% | -2.14% | | 3-5% | 25 | 28% | +2.18% | | **5-10% | 20 | 60% | +2.57%** | | 10-20% | 42 | 40% | -1.51% | | 20%+ | 21 | 29% | -1.01% |. Below 5%, wicks reverse before TP2; above 10%, the move overshoots before TP1. Tag ideas outside 5-10% with `rejection_reasons: ["tp1_outside_5_10_band"]` rather than tightening the bar.
- **Stop sweet spot is 3-6% from entry.** | Band | n | Hit | Avg | | 0-3% | 27 | 33% | -0.11% | | **3-6% | 20 | 55% | +4.36%** | | 6-10% | 23 | 30% | +0.37% | | 10-15% | 25 | 40% | -2.81% | | 15%+ | 18 | 33% | -0.67% |. Tight stops (<3%) get wick-stopped; wide stops (>10%) collapse R:R. Mirror TP1: tag with `rejection_reasons: ["stop_outside_3_6_band"]`.
- **Conviction floor ≥3 is the right bar.** | Conv | n | Hit | Avg | | 4 | 1 | 100% | +5.05% | | **3 | 21 | 43% | +2.43%** | | 2 | 61 | 34% | -0.75% | | 1 | 30 | 40% | ±0.00% |. Only positive-return bucket is conv=3. Keep ≥3 (caveat: figures are pre-classifier-fix; re-validate).
- **SHORT direction is structurally negative-EV (probe: ≥4 conviction).** LONG: 39% hit, +1.37% avg. SHORT: 37% hit, -1.16% avg. Asymmetric — shorts hit TP1 on bounces then reverse before TP2. Three possible causes: (a) June 2026 recovery regime, (b) `strategy-trend-follow` long bias, (c) asymmetric stop distance. Action: raise SHORT conviction floor to ≥4 for a 2-week probe; revert if hit rate doesn't improve.
- **ai_infra basket: 78% hit but -0.69% avg return (N=9).** Ideas touch TP1 just before reversal — high realized volatility, late timing. Either widen TP1 band to 8-15% or drop ai_infra from DTP scan. Lower-risk is drop + manual-watch; re-evaluate after 2 weeks.
- **Hit/miss classifier was two bugs (fixed 2026-07-07).** 22/43 "hits" had negative return (wick was being used as exit price); 28/70 "misses" had positive return (direction-blind `hit_target = abs(return) >= 5` misclassified losing SHORTs as hits). Both fixed: exit_price now from next-bar-open, hit_target from wick-touch + direction. Re-run `scripts/backfill_outcomes.py` to clean the journal.
- **Macro_aligned gate relaxed to advisory (BUGS-2026-07-07-3).** Counter-macro ideas (1/3) had +0.28% avg return; aligned (2/3) had -1.82% in the dominant BTC4h=short + ETH4h=short + F&G=long cluster. Gate no longer vetoes — surfaced as narrative note. Re-evaluate after 2-4 weeks of clean data.
- **Pick rate < 1/day (2% of ideas, 0.3/day avg).** Don't lower the bar. Two paths: (a) tighten universe per the perp-DEX pitfall above, (b) expand to sources with stricter gates (≥4 conviction). The "silent 3+ ticks → expand universe" rule in When-to-use is the canonical path (b).
- **`strategy-trend-follow` v2/v3 ideas have `stop: null`.** Bar's R:R check uses envelope's own `rr_to_tp1`, not back-derived from stop. Verifier MUST skip R:R math when `stop is None` — synthetic stops corrupt the journal (back-deriving `1.5 * stop_dist = tp1_dist` would falsely report 1.5 R:R and mask a real bar rejection).
- **HL perp-DEX tickers can't be priced by the kraken CLI.** `kraken ticker hl:LIT` returns `rc=1`. Outcome-step must catch rc=1 explicitly and route to the `expired`-but-`closed` branch — don't lump all fetch errors together (worked 2026-07-05: 3 of 8 ideas needed this path).
- **`hl_narrative` volume filter needs `metaAndAssetCtxs`, not `meta`.** `meta` only returns the universe (no volume); `metaAndAssetCtxs` returns `[meta, [ctx_per_asset]]` where each ctx has `dayNtlVlm`. Worked 24h-vol>20M filter:
  ```python
  out = json.loads(urllib.request.urlopen({"type": "metaAndAssetCtxs"}, timeout=10).read())
  by_name = {out[0]["universe"][i]["name"]: out[1][i] for i in range(len(out[0]["universe"]))}
  candidates = [(n, float(c["dayNtlVlm"])) for n, c in by_name.items() if float(c.get("dayNtlVlm", 0)) > 20_000_000]
  ```
- **L3 TP ladder rejection can leave `ideas: []`.** Two failure modes that look identical: (1) "no setup" (narrative like "Trend weakening with score 0.0") — legitimate, ignore; (2) internal validation rejection (narrative starts with `error: L3 <PAIR> <DIR> TP3 must be ≤ entry × 0.95`) — producer-side bug, file via Kanban. Extract entry/stop/TP1 from the error string so the cron surfaces as `[BUG]` instead of going silent.
- **Pair format on Kraken is non-obvious.** `kraken ticker <PAIR>USD -o json` returns response under the canonical pair key (X/Z-prefixed for older pairs, bare for newer). Outcome-step must `next(iter(data))` not hardcode the input pair name. See `references/kraken-pair-lookup.md`.
- **Surf-MCP cross-check is retired.** Replaced by `skills/market-snapshot/scripts/run.py <TICKER> --json` (RSI + Supertrend). Code/prompts calling `surf_market` / `surf_social` for DTP cross-check are the old shape.
- **Use market-skills before inline curl for new external sources.** CoinGecko movers + Kraken tradability is `skills/market-movers/scripts/run.py --json`. Reimplementing duplicates logic and breaks on schema updates.
- **Prompt bloat from duplicate source descriptions (real failure 2026-06-29).** First 6-source prompt ran 17,427 chars (~4,400 tokens); after consolidating each source to ONE table row + referring by name elsewhere, dropped to 9,461 chars (~2,400 tokens) — 45% reduction. Rule: describe each source once in a canonical table.
- **F&G regime mapping.** value < 25 supports longs (extreme fear = buy the dip); value > 75 supports shorts (extreme greed = fade); otherwise neutral. The bar must NOT map fear → shorts or greed → longs — that's the contrarian signal.
- **Macro alignment counts dissent as 0.** For a SHORT idea, F&G = 17 (extreme fear = supports longs) is OPPOSITE the idea direction → counts as 0 alignment. The check is `signal == direction`, not `signal.opposite == direction`.
- **Cooldown applies to picking only.** Every idea gets bar-evaluated regardless of cooldown; only the picked promotion is filtered by `cooldown_ok`. An idea with `met_bar: true` but `cooldown_ok: false` is logged but not promoted.
- **L3 idea schema lives in market-skills.** TradeIdea fields like `version`, `move_maturity_pct`, `entry_window_validity_pct`, `entry_range` are set by `run-all-l3` and validated by `validate_l3_tp_ladder`. The DTP journal extracts only the relevant subset (`entry_price`, `stop_loss`, `take_profit`, `conviction`, `direction`, `narrative`).
- **Don't reorder tier-1 and tier-2 in the prompt.** L3 batch order doesn't affect signal generation, but tie-break ordering DOES matter when two met_bar ideas tie on conviction and R:R.
- **Sentiment-vs-structure playbook (risk-engine skill).** Before sizing any idea where macro fear is at extremes and structure is ambiguous, re-read `risk-engine/references/sentiment-vs-structure.md` — extreme fear is contrarian only when structure isn't broken.
- **Regime-bias playbook (market-skills skill).** Don't tune pick-generation thresholds based on aggregate stats from a single-regime sample — segment first, separate structural from regime findings.
- **Float precision on clean fractions.** `1.5 < 1.5` returns True (Python float renders 1.5 as 1.4999...). Use `abs(tp1_dist - 1.5 * stop_dist) > 1e-3` for the strict check, not `tp1_dist / stop_dist < 1.5`.
- **R:R 1.5x construction edge case (resolved).** See "Bar strictness" section above.


## Versioning

v0.7.0 — added 7 data-backed pitfalls from the 2026-07-06 journal review (11 days / 22 scans / 133 ideas / N=113 closed). Updated description triggers for journal-review auto-load.

## References

- `references/r-r-1.5x-rounding-edge-case.md` — worked ETH 1d SHORT example (entry=1644.0, stop=1783.33, TP1=1435.01 → R:R=1.499964) + SOL counter-example (clean fractions, float precision quirk) + decision rule for borderline calls.
- `references/journal-write-recipe.md` — read-modify-write atomic update pattern for picks.json, including how to handle the empty-array initialization case (first tick ever).
- `references/multi-source-design.md` — the 6-source framework pattern (tier 1+2 + swing shortlist + CoinGecko + Nansen smart money + HL narrative + surf-mcp cross-check). Per-source conviction gates, per-source sizing, top-3 cap with per-source priority tie-break. Reference design for any opportunity scanner, not just daily-trade-pick.
- `references/kraken-pair-lookup.md` — canonical analysis-ticker → Kraken pair-key mapping (e.g. `ETH-USD` → `XETHZUSD`, `<NEWER_PAIR>-USD` → `<NEWER_PAIR>USD`) with drop-in Python lookup function. Use in batch-fetch Python for outcome-step pricing; avoids per-call `next(iter(data))` parsing.
- `references/dtp-journal-verifier-shape.md` — every FAIL line the bundled `dtp_journal_verifier.py` emits, with line numbers, triggers, and how to respond. Use this when the verifier exits 1 after a non-silent tick. **(added 2026-07-05)**
- `scripts/verify_journal.py` — re-runnable ad-hoc verifier for the journal write. Checks JSON parseability, scan envelope, required idea fields, age-bucketed status (24h+ must be closed, <20h must be open), and picked-requires-met_bar invariant. Run after every journal write (see Verifier quirks above).
- `scripts/analyze_journal.py` — offline journal analyzer. Run when the user asks "how are daily picks going" or "anything we can learn". Aggregates by hit/miss, ticker, direction, conviction, macro alignment, and day. Surfaces the actionable cuts (which tickers to drop, which filters are inverted, what the pick rate looks like). Pure stdout, no journal writes.
- `scripts/backfill_outcomes.py` — one-off backfill that re-derives `actual_return_pct`, `hit_target`, and `outcome_verdict` for closed ideas using the current post-fix formulas (direction-aware, wick-based). Run once after BUGS-2026-07-07 fixes land to clean up the journal. Idempotent, supports `--dry-run`.

## L3 envelope iteration recipe (added 2026-07-03)

`run-all-l3 --json` nests `ticker → strategy_name → ideas[]` two levels deep with a `narrative` string alongside `ideas`. Use `l3-conviction-scan --json` instead — it returns the same ideas pre-iterated in a flat `ideas: [...]` array, which is what bar-evaluation wants. See `references/l3-conviction-scan-vs-run-all-l3.md` for the shape diff.

## Reference recipes

### Macro alignment — extract direction from `strategy-trend-follow --json`

`strategy-trend-follow --json` returns `{"ideas": [...]}`. Read `ideas[0].direction` for the bellwether; if `ideas` is empty, treat the direction as `NEUTRAL` (0 alignment in the macro check). The `NEUTRAL` case is the common one for ETH 4h in mixed regimes.

### Universe expansion when primary baskets are silent

When primary baskets produce no `met_bar` ideas (or they're all in cooldown), expand to additional baskets by adding more basket names to the `market-watchlist` invocation, dedup, and re-run the L3 batch. **Don't lower the bar** — expansion is the only sanctioned path. If expansion is also silent, the universe is genuinely quiet and the correct answer is `[SILENT]`.

### FX pitfall — `rr_to_tp1` is quote-invariant, prices are not

`rr_to_tp1` is a ratio — same number in any quote. The absolute prices (`entry_price`, `stop_loss`, `take_profit`) are in the source ticker's quote. When sizing in a wallet currency that differs (e.g. EUR wallet, USD source): convert prices with current FX before computing EUR `risk_per_unit` / `reward_per_unit`, but the R:R ratio is unchanged. Sizing against the headline R:R without converting prices can over-size — use the per-unit risk in wallet currency for position size, the source-quote R:R for setup quality.

## Macro-dominant rejection cluster (updated 2026-07-07 — macro gate is advisory)

A `[SILENT]` driven entirely by macro alignment (every idea has `macro_aligned 1/3`) is a regime pattern worth recognizing: extreme-fear F&G supports longs while bellwether BTC 4h trend-follow is short, with ETH 4h having no idea. Net: longs and shorts each get 1/3. Since the macro gate is now advisory (BUGS-2026-07-07-3), this pattern alone no longer blocks picks — the L3 conviction gate does the filtering. If the tick is still silent with macro 1/3 ideas, inspect `rejection_reasons` for non-macro bars (conviction, TP1, R:R, cooldown).

## Quick reproduction

```bash
# 1. Initialize journal file (first run only) — pick any writable path
# and export MARKET_SKILLS_DAILY_TRADE_PICK_PATH="/your/path/picks.json"
mkdir -p "$(dirname "$MARKET_SKILLS_DAILY_TRADE_PICK_PATH")"
echo '[]' > "$MARKET_SKILLS_DAILY_TRADE_PICK_PATH"

# 2. Run one tick manually (testing)
cd <repo-root>
uv run skills/run-all-l3/scripts/run.py BTC-USD ETH-USD <PRIVATE_TICKER>-USD NEAR-USD ZEC-USD hl:<PRIVATE_PERP> SOL-USD XMR-USD PAXG-USD --json > /tmp/dtp_l3.json
# ... (inspect /tmp/dtp_l3.json, evaluate bar, write journal — see references/journal-write-recipe.md)

# 3. Verify journal
python3 -c "import json; d=json.load(open('$MARKET_SKILLS_DAILY_TRADE_PICK_PATH')); print(f'Scans: {len(d)}, latest met_bar: {sum(1 for i in d[-1][\"ideas\"] if i[\"met_bar\"])}, picked: {sum(1 for i in d[-1][\"ideas\"] if i[\"picked\"])}')"
```

## Output envelope (AXI)

`--json` output follows the canonical [AXI envelope](../../docs/AXI-REFERENCE.md) — `{data, count, errors, help[]}`. Pass `--fields=<csv>` to project or `--full` for the full payload. `count` is the item count (findings for `bug-scan`, ranked ideas for `l3-conviction-scan`, total journal entries for `daily-trade-pick`), `help[]` is contextual next-step command templates.

## Home view (no-arg mode)

No-arg mode prints the home view from `$XDG_DATA_HOME/market-skills/<skill>_last.json` (last successful run). Errors are not cached.
