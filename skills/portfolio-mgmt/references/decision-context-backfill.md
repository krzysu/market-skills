# Backfilling `decision_context` for historical trades

The `decision_context` field (see SKILL.md §"decision_context — structured decision trace") is auto-populated for new Kraken trades via `execution-kraken-spot` and `execution-kraken-perps`. For trades recorded before the field was added, the trace lives in:

- The market-notes entry for that ticker (`market-notes/data/notes.json`)
- The thesis / stop / target fields already in the trade's notes JSON
- The agent's session history (chat messages, cron output) — queryable via `session_search`
- The `learnings/` directory for structured post-mortems

## Decision tree per missing field

| Field | Where to find it |
|-------|------------------|
| `intent_id` | `notes.intent_id` or `notes.ref` (already there for execution-kraken auto-logs); else use `<strategy>-<PAIR>-<date>` |
| `source_skill` | `notes.strategy` (already in notes for execution-kraken trades); else infer from cron output referenced in `notes.run_reference` |
| `l3_idea.direction` / `conviction` | `notes.strategy` + the thesis text; for trend-follow / breakout-confirm trades, parse `run_reference` file |
| `l3_idea.summary` | Pull from the linked `run_reference` cron output file (e.g. `cron/output/research/journal.md Run #N`) |
| `l3_idea.entry_price` / `stop` / `tp1-3` | `notes.entry_plan_eur` / `notes.stop_loss_eur` / `notes.tp1` / `tp2` / `tp3` |
| `l3_idea.rr_to_tp2` | Compute with direction-aware formula: long = `(tp2 - entry) / (entry - stop)`, short = `(entry - tp2) / (stop - entry)`. Or use `analysis.decision.compute_rr_to_tp2(direction, entry, stop, tp2)`. Skip if entry/stop missing |
| `regime.label` | Look at the linked thesis note for the macro context line; map to one of `fear_recovery`, `risk_on`, `risk_off`, `neutral`. If unclear, leave as `null` |
| `regime.fng` / `btc_dominance` / `divergence` | Not stored historically — leave as `null` for backfilled rows. New rows capture these at decision time |
| `macro_signals` | Parse from thesis note text (e.g. "F&G extreme fear" → `["fng_extreme_fear"]`). Be conservative — only add signals explicitly mentioned |
| `risk_verdict.status` / `position_size_pct` / `concerns` | Not stored historically. Use `"UNKNOWN"` for status, leave the rest as `null`. The risk layer was advisory and the auto-log started recording these in 2026-06 |
| `override` | **The highest-value backfill field.** Search `session_search` for the trade's date + ticker; look for any "I went with X instead of suggested Y" pattern. If found, populate `from_suggestion: true`, `field: <stop|volume|conviction|...>`, `reason: <one-line>` |
| `captured_at` | Trade timestamp from the `add` call — convert to ISO UTC |

## Workflow

For each trade missing `decision_context`:

1. Get the trade row: `list --portfolio <name> --since <date> --limit N --json`
2. Identify the linked market-note (look at `notes.note` reference or thesis text → grep `market-notes/data/notes.json`)
3. For the override field, run `session_search` for the trade date + pair; check chat for deviation patterns
4. Build the JSON blob (see schema in SKILL.md)
5. Update via: `edit <tx_id> --field notes --value "$(jq -c '.decision_context = <blob>' <<<existing_notes)"`
6. Verify: `view --portfolio <name> --no-refresh` shows the new field

## Example: backfilling a tier-1 trade entry

Source data already in notes:
```json
{
  "strategy": "trend-follow",
  "conviction": 4,
  "thesis": "EMA21 reclaim + 4h thrust; bullish retest at ascending trendline",
  "entry_plan_eur": 60.15,
  "stop_loss_eur": 49.71,
  "tp1": 88.21, "tp2": 100.58, "tp3": 119.14,
  "deployed_eur": 99.85
}
```

Add decision_context:
```json
{
  "decision_context": {
    "intent_id": "trend-follow-<TICKER>USD-2026-06-22-001",
    "source_skill": "strategy-trend-follow",
    "l3_idea": {
      "direction": "long",
      "conviction": 4,
      "summary": "EMA21 reclaim + 4h thrust; bullish retest at ascending trendline",
      "entry_price": 60.15,
      "stop": 49.71,
      "tp1": 88.21, "tp2": 100.58, "tp3": 119.14,
      "rr_to_tp2": 3.3
    },
    "regime": {"label": "fear_recovery"},
    "macro_signals": ["fng_extreme_fear", "btc_fair_value_narrowing"],
    "risk_verdict": {"status": "UNKNOWN", "concerns": []},
    "override": {"from_suggestion": false, "field": null, "reason": null},
    "captured_at": "2026-06-22T15:00:00Z"
  }
}
```

## What NOT to backfill

Don't synthesize fields you can't ground-truth. Leave as `null` rather than guess:

- `regime.fng` — only F&G value at the exact decision time matters; a "close enough" reading is misleading
- `risk_verdict.concerns` — backfilling from memory is unreliable; mark `"UNKNOWN"` and move on
- `conviction` — if not in the notes already, leave `null` rather than guess
- `override.from_suggestion: false` for clean system-driven trades is fine; setting `true` without evidence is not

The point is *traceable signal*, not a complete picture. Missing fields honestly marked null are more useful than fabricated completeness.

## When to do this backfill

Low priority. Do it incrementally as you review historical trades for other reasons (post-mortems, sizing reviews). Don't batch-backfill just to fill the field — most value comes from new trades going forward.