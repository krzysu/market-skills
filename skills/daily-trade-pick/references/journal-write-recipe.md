# Journal Write Recipe — picks.json

Atomic read-modify-write pattern for the `picks.json` journal file
at `$MARKET_SKILLS_DAILY_TRADE_PICK_PATH`.

## File shape

```json
[
  {
    "type": "scan",
    "id": "YYYY-MM-DD-001",
    "created_ts": "ISO-8601 UTC",
    "ideas": [
      { /* full TradeIdea + bar state, see SKILL.md */ }
    ]
  }
]
```

Top-level is a **JSON array** of scan records. Append-only — never overwrite, never skip. The `ideas` array contains EVERY idea the L3 batch produced (passed the bar OR not). `met_bar` flags whether it met the bar. `picked` flags whether the cron selected it for Telegram. `status` lifecycle applies to every idea, not just picked ones.

### Idea schema (2026-06-29 update — multi-source design)

Every idea carries a `source` tag identifying which of the six sources produced it:

| `source` value | Meaning |
|---|---|
| `tier1` | Thesis universe tier 1 (BTC, ETH, HYPE, NEAR, ZEC, hl:LIT) |
| `tier2` | Thesis universe tier 2 (SOL, XMR, PAXG) |
| `swing_shortlist` | Tier 3 swing-scan shortlist (AERO, TAO, VVV, ALGO) |
| `coingecko_movers` | External CoinGecko gainers discovery (surf-mcp) |
| `smart_money` | Nansen smart-money accumulation (paid MCP) |
| `hl_narrative` | Hyperliquid-universe narrative (perp-only) |
| `unknown` | Legacy backfill — ideas written before 2026-06-29 |

The cron sets `source` to the originating tag on every new write. Pre-2026-06-29 entries lack `source` and get backfilled to `"unknown"` on the first read after this update — see the **Backfill legacy ideas** section below. The verifier (`scripts/verify_journal.py`) accepts `unknown` indefinitely as a legacy tag.

## First-run initialization

When the file doesn't exist or contains `[]` (first tick ever, or fresh state after a manual prune):

```python
import json
from pathlib import Path

journal_path = Path(os.environ['MARKET_SKILLS_DAILY_TRADE_PICK_PATH']).expanduser()

# If file doesn't exist or is empty, initialize
if not journal_path.exists() or journal_path.stat().st_size == 0:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text('[]\n')

picks = json.loads(journal_path.read_text())  # safe: file is valid JSON
```

### Backfill legacy ideas (run once on first read after 2026-06-29)

Older journal entries (pre-multi-source design) lack the `source` field. The verifier accepts the absence as a legacy condition but the cron should backfill `source = "unknown"` so the journal converges to a fully-tagged state. Do this on every read (idempotent — only fills missing values):

```python
LEGACY_SOURCES_BACKFILLED = False  # set True once we've backfilled on this read
for scan in picks:
    for idea in scan.get('ideas', []):
        if 'source' not in idea:
            idea['source'] = 'unknown'
            LEGACY_SOURCES_BACKFILLED = True

# If any backfill happened, write the file back even before today's tick runs —
# the file would otherwise stay half-tagged until the next append.
if LEGACY_SOURCES_BACKFILLED:
    tmp_path = journal_path.with_suffix('.json.tmp')
    tmp_path.write_text(json.dumps(picks, indent=2))
    os.replace(tmp_path, journal_path)
```

This block runs **after** `picks = json.loads(...)` and **before** the outcome-check loop. After the first cron tick post-update, all ideas carry a `source` tag (either a real one or `"unknown"`), and subsequent ticks write new ideas with the real source tag from the L3 batch.

The spec's "Outcome check is NOT optional" rule applies even on the first tick — but if `picks = []`, there are no open scans to close, so step A is a no-op.

## Outcome check (step A)

For each `scan` record and each `idea` inside it where `status: "open"` AND `created_ts` is ≥ 20h ago (24h ± 4h tolerance for missed runs):

1. Get current price via `kraken ticker <PAIR>USD -o json`. **Pair format gotcha:** Kraken prepends X and Z for canonical pairs (`XETHZUSD` for ETH, `XXBTZUSD` for BTC, `XSOLZUSD` for SOL). Newer pairs like `HYPEUSD`, `ZECUSD` don't get the prefix. Extract the first key from the response, don't hardcode.
2. Compute `actual_return_pct` per the idea direction.
3. Set `hit_target`, `outcome_verdict`.
4. Mutate the idea dict in-place: `status`, `closed_at`, `exit_price`, `actual_return_pct`, `hit_target`, `outcome_verdict`. **Don't touch `met_bar` or `picked`** — those are from the original scan and must remain immutable for calibration analysis.

```python
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)
for scan in picks:
    scan_ts = datetime.fromisoformat(scan['created_ts'])
    if (now - scan_ts) < timedelta(hours=20):
        continue  # too young to close
    for idea in scan['ideas']:
        if idea['status'] != 'open':
            continue
        pair = idea['pair']
        # Fetch current price (Kraken pair format gotcha handled here)
        result = subprocess.run(
            ['kraken', 'ticker', f'{pair}USD', '-o', 'json'],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        kraken_key = next(iter(data))  # first (only) key
        current_price = float(data[kraken_key]['c'][0])
        # Compute return
        if idea['direction'] == 'long':
            ret = (current_price - idea['entry_price']) / idea['entry_price'] * 100
        else:
            ret = (idea['entry_price'] - current_price) / idea['entry_price'] * 100
        idea['exit_price'] = current_price
        idea['actual_return_pct'] = round(ret, 4)
        # Direction-aware hit_target (avoids the direction-blind bug
        # where a SHORT that loses ≥ 5% was classified as a hit).
        # See SKILL.md §Pitfalls → "R3 — classifier is two bugs, not one".
        idea['hit_target'] = (
            ret >= 5.0 if idea['direction'] == 'long' else ret <= -5.0
        )
        idea['outcome_verdict'] = 'hit' if idea['hit_target'] else 'miss'
        idea['status'] = 'closed'
        idea['closed_at'] = now.isoformat()
```

## Today's scan (step B)

Build the new `scan` record with all ideas (passed bar OR not). Append to the array.

```python
import uuid

scan_id = f"{now.strftime('%Y-%m-%d')}-{len(picks) + 1:03d}"
scan_record = {
    'type': 'scan',
    'id': scan_id,
    'created_ts': now.isoformat(),
    'ideas': ideas_list,  # every L3 idea, with met_bar/picked/rejection_reasons populated
}
picks.append(scan_record)
```

The ID pattern `YYYY-MM-DD-NNN` increments N per tick on the same date. The 3-digit padding (`001`, `002`, ...) makes the IDs sortable and prevents collision if multiple ticks land in the same date (cron retries, manual runs).

## Atomic write

Read the whole file, modify in memory, write the whole file. **Never append partial JSON** — if the script crashes mid-write, the file is corrupted.

```python
# Atomic write pattern (write to .tmp, then rename)
import os
tmp_path = journal_path.with_suffix('.json.tmp')
tmp_path.write_text(json.dumps(picks, indent=2))
os.replace(tmp_path, journal_path)  # atomic on POSIX
```

The `os.replace` is atomic on POSIX filesystems (macOS APFS, Linux ext4). For crash-safety, this is enough — if the script dies before `os.replace`, the original file is untouched. If it dies during the write to `.tmp`, the `.tmp` may be partial but the original `picks.json` is still intact.

If `os.replace` isn't available (older Python), use `shutil.move` instead — it's not strictly atomic but close enough for a daily cron.

## Verify after write

```python
import json
verified = json.loads(journal_path.read_text())
assert len(verified) == len(picks), f"Round-trip mismatch: wrote {len(picks)}, read back {len(verified)}"
assert verified[-1]['id'] == scan_id, f"Last scan ID mismatch: expected {scan_id}, got {verified[-1]['id']}"
```

The round-trip read catches silent serialization bugs (datetime objects that don't serialize, NaN values that become `null`, etc.).

## Edge cases

- **Empty journal on first run**: `picks = []`, no outcome checks to do, just append the new scan. The initialization step above handles the case where the file doesn't exist or is empty (`[]`).
- **File write permission denied**: `PermissionError` → surface to the cron, don't silently fail. The user needs to know their journal isn't being recorded.
- **JSON corruption from a previous crashed run**: `json.loads` raises `JSONDecodeError`. Surface to the cron and stop. Don't try to repair — manual intervention required (the user can inspect and recover from a backup).
- **Concurrent cron runs**: theoretically possible if the previous tick is still running. Use a file lock (`fcntl.flock`) on the `.tmp` file before the rename. For a daily 15:00 CEST cron, this is vanishingly rare; only worth adding if the user reports duplicate scans in the journal.
- **Pair delisted between open and close**: `kraken ticker` returns an error or unknown pair. Set `idea['status'] = 'expired'`, `idea['outcome_verdict'] = 'expired'`. Don't crash the outcome check loop on a single bad pair — wrap the price fetch in try/except and continue.

## Recovery from a bad journal write

The most common failure mode is computing the bar evaluation against the wrong field (e.g. the 2dp-rounded `take_profit` instead of the unrounded `take_profit_ideal`), writing the resulting scan record, then realizing the R:R math is off. The pattern that actually works:

1. **Stop and re-derive.** Don't try to "patch" the just-written record in place. If the source-extraction was wrong, every idea's `met_bar` is wrong, and an in-place patch will miss the cross-cutting issues.
2. **Drop the last scan record** by filtering on the `id` you just wrote. The pattern that worked in practice (2026-06-30 10:00 CEST tick):
   ```python
   journal = [s for s in journal if not (s.get('type') == 'scan' and s.get('id') == '2026-06-30-001')]
   ```
3. **Re-run the L3 batch + bar evaluation in a single mktemp script.** The fix is the source extraction, not the journal format — keep the journal write, fix the upstream computation. Use the mktemp + `terminal` + `python3` pattern from the SKILL.md → Operational pattern.
4. **Re-append with the same `id` if it's still the same calendar date, OR with a new `id` if a fresh tick is needed.** A re-write under the same id keeps the journal clean (no duplicate scan ids in the same day). A new id is fine if the previous one was a debugging artifact.
5. **Verify with `scripts/verify_journal.py`** (NOT the orchestrator-side `dtp_journal_verifier.py` — that one is over-strict for non-silent runs). See "Which verifier to use" below.

**Pitfall — first-write committed before R:R math was double-checked.** This happens when the L3 envelope is read with `tp1 = (i.get('take_profit') or [None])[0]` (rounded) instead of `tp1 = (i.get('take_profit_ideal') or i.get('take_profit') or [None])[0]` (unrounded). The R:R formula check passes with `take_profit_ideal` (exact by construction) but fails with rounded `take_profit` on small-cap coins where the stop is sub-cent. If you see 3+ ideas failing the formula check with a diff ~5e-4 and the published `rr_to_tp[0]` is exactly 1.5, you used the wrong field — re-extract from `take_profit_ideal` and re-run. The cron prompt's R:R check is tolerant (1e-3 * stop_dist), so a few idea rejections is normal, but a cluster of identical-pattern failures is a sign of upstream extraction error, not bad ideas.

## Which verifier to use

After every journal write, run **`scripts/verify_journal.py`** (this skill's bundled verifier, in the same skill directory). It checks:
- JSON parseability
- Scan envelope (type/id/created_ts/ideas)
- Required idea fields
- `source` tag validity (multi-source design)
- Age-bucketed status (24h+ must be closed, <20h must be open)
- `picked: true` requires `met_bar: true` (the reverse — `picked: false` with `met_bar: true` — is allowed for ideas that pass the bar but fail cooldown)
- JSON round-trip

**Do NOT use the orchestrator-side `dtp_journal_verifier.py`** for daily-trade-pick verification. That verifier is hardcoded to reject any `picked: true` or `met_bar: true` in the new scan (`fail(f"new idea {i['ticker']} met_bar=true (verify picking logic)")`). It assumes the cron is always silent. The daily-trade-pick cron produces real picks (1-3 per tick), so the orchestrator-side verifier will always fail on non-silent runs. The orchestrator-side verifier is owned by the `market-skills-orchestration` skill; do not edit it from here. Use `scripts/verify_journal.py` in this skill instead.

## State tracking for cooldown

Cooldown is "no `picked: true` on same ticker in last 24h". Compute it inline during step B:

```python
from datetime import timedelta

def is_cooldown_ok(ticker: str, picks: list, now: datetime) -> bool:
    """True if no picked idea on this ticker in the last 24h."""
    cutoff = now - timedelta(hours=24)
    for scan in picks:
        scan_ts = datetime.fromisoformat(scan['created_ts'])
        if scan_ts < cutoff:
            continue
        for idea in scan.get('ideas', []):
            if idea.get('ticker') == ticker and idea.get('picked'):
                return False
    return True
```

**Cooldown applies ONLY to picking**, not to bar-evaluation. Every idea gets evaluated; only the picked one is filtered by cooldown. An idea with `met_bar: true` but `cooldown_ok: false` is logged in the journal but not promoted to Telegram — output `[SILENT]` instead.

## Picking logic (step B)

```python
tier_map = {
    'BTC-USD': 1, 'ETH-USD': 1, 'HYPE-USD': 1, 'NEAR-USD': 1, 'ZEC-USD': 1, 'hl:LIT': 1,
    'SOL-USD': 2, 'XMR-USD': 2, 'PAXG-USD': 2,
}
source_priority = {  # multi-source design (2026-06-29) — see references/multi-source-design.md
    'tier1': 0, 'tier2': 0,  # thesis universe (highest priority)
    'swing_shortlist': 1,
    'coingecko_movers': 2,
    'smart_money': 3,
    'hl_narrative': 4,
    'unknown': 5,  # legacy backfill — lowest priority
}
PICK_CAP = 3
met_bar_ideas = [i for i in ideas_list if i['met_bar'] and i['cooldown_ok']]
if met_bar_ideas:
    met_bar_ideas.sort(key=lambda x: (
        -x['rr_to_tp1'],     # R:R descending (primary)
        -x['conviction'],    # conviction descending (secondary)
        source_priority.get(x.get('source', 'unknown'), 5),  # source priority (tertiary)
        tier_map.get(x['ticker'], 9),  # tier 1 before tier 2 (quaternary)
        x['ticker'],         # alphabetical tie-break
    ))
    picked_set = met_bar_ideas[:PICK_CAP]
    for idea in picked_set:
        idea['picked'] = True
        # suggested_size_eur comes from per-source cap:
        #   tier1/tier2: 200, swing_shortlist/coingecko/smart_money: 100, hl_narrative: 50 (perp)
        idea['suggested_size_eur'] = SIZE_BY_SOURCE.get(idea.get('source', 'unknown'))
    for idea in met_bar_ideas[PICK_CAP:]:
        idea['picked'] = False
```

Mark up to the top **PICK_CAP=3** as `picked: true`. All other `met_bar: true` candidates (and `met_bar: false` ideas from the L3 batch) get `picked: false`. The journal is the source of truth — even over-the-cap `met_bar: true` ideas stay visible for calibration analysis. Per-source sizing is documented in `references/multi-source-design.md` — `SIZE_BY_SOURCE` is `{tier1: 200, tier2: 200, swing_shortlist: 100, coingecko_movers: 100, smart_money: 100, hl_narrative: 50, unknown: 100}`.