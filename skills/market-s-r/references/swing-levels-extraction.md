# Swing Levels Extraction — Pattern + Examples

Companion to `market-s-r` SKILL.md "Extracting multiple structural levels" section. Shows the full workflow for going from raw skill output to a usable list of price goals (TPs, stops, zones).

## Why this exists

`market-s-r` text output prints only `nearest_support` and `nearest_resistance`. For goal-price work — TP ladders in position-watchdog, stop clusters, zone identification — you need **all** levels above/below current price with structural strength (touch counts).

The `--json` output has this in `indicators.clustered_levels: [{price, touches}, ...]` but the schema isn't obvious from the text output. This reference shows the exact path + jq/regex patterns + the worked case from the 2026-07-01 legacy position recovery setup.

## Schema reminder

```json
{
  "indicators": {
    "current_price": 1380.75,
    "nearest_support": 1353.2,
    "nearest_resistance": 1455.91,
    "support_distance_pct": 2.04,
    "resistance_distance_pct": 5.4,
    "support_touches": 2,
    "resistance_touches": 1,
    "clustered_levels": [
      {"price": 1532.67, "touches": 3},
      {"price": 1603.25, "touches": 3},
      {"price": 1902.36, "touches": 6},
      ...
    ]
  }
}
```

`clustered_levels` is **unsorted** — sort by `price` ascending or descending depending on use. Touch count is the structural strength signal.

## Touch-count thresholds (from practice)

| Touches | Strength           | Use                                                                  |
| ------- | ------------------ | -------------------------------------------------------------------- |
| 1       | Noise              | Ignore                                                               |
| 2       | Real level         | Worth watching, lower-priority TP/stop                               |
| 3       | Significant        | Strong S/R, primary TP/stop candidates                               |
| 4-5     | Major              | Historical structural level, zone anchor                             |
| 6+      | Cycle / multi-year | Cycle top/bottom, multi-year consolidation; rare but very meaningful |

**Default filter:** `touches >= 2` for "watch list", `touches >= 3` for TP placement. Higher thresholds only when building zone anchors.

## Workflows

### Workflow A — Save JSON, read with read_file (preferred)

Per Kraken CLI pitfall: never pipe market-skills JSON to `python3 -c`. Save to disk first:

```bash
uv run skills/market-s-r/scripts/run.py ETHEUR --json > /tmp/sr.json 2>/tmp/sr_err.txt
```

Then `read_file /tmp/sr.json` and grep `clustered_levels`. The JSON is human-readable enough that you can pick the levels visually — no jq required for one-off extraction.

### Workflow B — jq filter for resistance-only with strength threshold

```bash
# All levels above current price, touches >= 3, sorted by price
uv run skills/market-s-r/scripts/run.py ETHEUR --json \
  | jq '[.indicators.clustered_levels[] | select(.touches >= 3)] | sort_by(.price)'
```

### Workflow C — Build a TP ladder for position-watchdog

Goal: 2 TPs from structural resistance, ladder toward breakeven for a recovery play.

1. Get clustered levels above current price (Workflow A or B).
2. Filter for `touches >= 3`.
3. Pick the **first two** levels above current price as TP1 and TP2.
4. Verify they're structurally meaningful (multiple touches, not just noise).
5. Set `exit_pct: 50, 50` for balanced exit (or 33/33/34 if 3 TPs).

Worked example (2026-07-01, ETH legacy recovery):

```
current_price: €1,381
avg_cost (BE): €1,973
levels with touches >= 3:
  €1,533 (3 touches)  ← TP1
  €1,603 (3 touches)
  €1,789 (4 touches)  ← TP2 (closer to BE)
  €1,940 (5 touches)  ← TP3 (near BE)
```

Selected TPs: €1,533 / €1,789. Both structurally strong, TP1 is +11% upside (achievable recovery), TP2 at +30% is near breakeven exit zone.

### Workflow D — Identify stop-loss anchors from support levels

```bash
# All levels below current price, touches >= 3, sorted by price desc
uv run skills/market-s-r/scripts/run.py ETHEUR --json \
  | jq '[.indicators.clustered_levels[] | select(.touches >= 3)] | sort_by(.price) | reverse'
```

Pick the **highest** level below current price as stop anchor. Avoids noise from weak levels; structural stops are more likely to hold on retest.

### Workflow E — Zone identification

Consecutive levels within 2% of each other form a zone. Build manually after sorting:

```
Levels above price: 1486, 1532, 1555, 1603
  → Zone A: 1486-1555 (4 levels, 4.6% width) — consolidation zone
  → Zone B: 1603 single level
```

Use zones as wider stop/TP bands when single-level price isn't achievable.

## Pitfalls

1. **Don't trust single-touch levels.** A level with `touches: 1` is one swing point, may not have real S/R significance. Always filter `touches >= 2` minimum, `>= 3` preferred.

2. **Touch counts don't measure recency.** A level with 6 touches might be 3 years old and irrelevant to current structure. Cross-check with market-trend (EMA alignment) to confirm level is still in play.

3. **clustered_levels can be stale on extreme moves.** A 30%+ single-direction move invalidates most historical levels. The skill still reports them; trust-touch filter doesn't help. If `market-trend` says FULL_BULL and price just broke above resistance, all clustered_levels above price are now support candidates — re-classify manually.

4. **period=1y default misses longer-term structure.** Cycle tops/bottoms need `--period 5y` or `--period max`. Default is fine for swing trading (TPs/stops 10-30% out), but for legacy position recovery play, use longer period to find cycle-level structure.

5. **Don't sort by touches — sort by price.** Touches is the strength filter, price is the placement filter. Pick the closest strong level above price, not the strongest level above price.
