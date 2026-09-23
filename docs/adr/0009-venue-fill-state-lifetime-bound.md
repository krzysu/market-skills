# 0009. Venue-fill candidates live within the watch state's lifetime

- **Status**: accepted
- **Date**: 2026-09-23

## Context

On 2026-09-23 the watchlist monitor re-reported a venue stop fill that
was already three days old. The pair's watch had been created fresh that
same morning by swing-scan — the previous watch state (and its
`venue_stop_fills` dedupe ledger) was gone, so the fresh state started
with an empty ledger. The venue closed-orders read has no time window,
so the venue's history still listed the 2026-09-20 stop; the detector
found a stop-family SELL with executed volume on the watch's base asset,
saw an `order_id` that was not in the empty ledger, and reported it as a
new fill. The fill had already been reported once, under the previous
watch state, on 2026-09-21.

The detector's docstring promised "a venue fill is reported once ever
(exact `order_id` dedupe)". In reality the dedupe was once per
watch-state lifetime: any watch recreation — swing-scan adding a watch
for a pair that previously had one, a manual re-add, a state-file reset
— re-reported every historical stop fill on that pair.

The same event carried a second defect: the renderer asserted
"Partial exit — position still open; monitoring continues", but the fresh
watch carried no `position_size`, so `closed_position` was `False` by
construction (`position_size is not None and …`) and the "still open"
claim had no basis.

Alternatives weighed: (a) per-watch per-venue ledger persisted outside
the watch state (adds a second state store to keep in sync — the state
file already carries the ledger); (b) bounding the venue read itself by
a time window (the provider method is shared and the read is unchanged
by design; the window belongs to the consumer); (c) doing nothing — the
false alarm self-heals after one tick, but that one tick is exactly when
a user is paying attention to a newly created watch.

## Decision

Venue stop fills are candidates only within the watch state's lifetime:

- `run.py` resolves a creation anchor per watch on every tick — the
  persisted `watch_state_created_at` first (frozen at the first tick
  that resolved it), then `_updated_at` for state files written before
  this key existed, else the current tick — and passes it into
  `evaluate_venue_stop_fills`; every state the process writes carries it,
  so the anchor is frozen and cannot advance tick over tick.
- The detector skips any datable order whose `closed_at` is at or before
  the anchor minus `VENUE_FILL_FRESHNESS_GRACE_SECONDS` (one 30-minute
  monitor cadence plus 30 minutes of grace, so a fill that landed just
  before the watch's first tick — or whose first tick had a failed venue
  read — is still reported). A skipped order is not ledgered and does not
  set the closure marker. An order with no `closed_at` cannot be dated
  and is not filtered; the order-id ledger still dedupes it. An absent or
  unparseable anchor disables the bound.
- The renderer stops asserting what it cannot know: when
  `position_size` is unknown, the render says the held size is unknown
  and that whether the whole position exited cannot be determined — it
  never claims the position is still open and never calls the event a
  partial exit.

## Consequences

- (+) A recreated watch (swing-scan re-add, manual re-add, state-file
  reset) never re-reports a previous lifetime's fills: the suppressed
  order is not ledgered, so the suppression is deterministic on every
  later tick, not a one-shot self-heal.
- (+) The renderer no longer fabricates a live-position claim from an
  unknown size.
- (-) A fill older than the bound that was never reported — e.g. two
  consecutive failed venue reads spanning the entire grace — is not
  reported at all: it silently falls outside the candidate window.
- (-) The bound applies to the candidate window, not to the venue page:
  the closed-orders read itself is unchanged, so the fetched page still
  carries the full history and the filtering happens in the detector.
