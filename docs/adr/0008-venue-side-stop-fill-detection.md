# 0008. Venue-side stop-fill detection in the watchdog

- **Status**: accepted
- **Date**: 2026-09-21

## Context

A resting stop-loss that the venue executes never passes through an
execution skill — the venue fills it against its own trigger, so no
`FillConfirmation` exists, no ledger row is written, and no notification
fires. Worked case (2026-09-20): a spot position was stopped out overnight
(order filled 03:39 UTC) and the position watchdog ran twice afterwards,
silent both times. The user learned of the exit only the next day from a
manual balance read.

Two gaps compounded: the exit was invisible to the notification path, and
the watchdog could not tell "position closed" from "price moved" — it kept
evaluating a phantom position against the levels in the held file. The
existing ledger-write gate (ADR on venue-status normalisation) does not
help: it covers orders that pass through the execution skill, and a
venue-side stop fill has no such call site at all.

Two detection paths were weighed:

1. **Venue closed-orders history** — the venue's own order record carries
   the order type, side, executed volume, average fill price, fee and close
   time; attribution is exact.
2. **Balance-vs-held-file diff** — infer closure from a balance drop. This
   is a false-positive generator by construction: a balance delta cannot
   name an order type (manual sell, on-chain transfer, fee taken in base
   asset, staking accrual all look identical), and the ledger records
   *decisions* while the venue records *reality*, so the two are expected
   to diverge (`portfolio-mgmt`'s "book INVESTMENTS, never mirror a venue
   balance" rule).

Where to put the detection was also weighed: a separate reconciliation
step, or inside the watchdog — which already owns the tick cadence, the
per-watch state, and the decision of what gets evaluated.

## Decision

Detection lives inside `position-watchdog` and reads the venue's
closed-orders history on its own tick (`--venue-stops`, enabled for the
scheduled tick; `--status` stays read-only).

- **Attribution** comes from the venue order record: a reportable fill is a
  closed order in the stop order-type family, sell-side, executed volume >
  0, on the watch's base asset (quote-insensitive pair match). Manual
  sells are deliberately not reported.
- **P&L is computed only same-quote.** The pair match is quote-insensitive,
  so the fill's quote is carried on the event and the realised-P&L figure is
  computed only when it equals the monitor's quote (`entry_price` is
  denominated in the monitor quote). A cross-quote fill reports the P&L as
  unavailable and is rendered in the fill's own quote — never with the
  monitor's currency symbol.
- **Closure** requires the executed quantity to cover the held
  `position_size` within a 1% tolerance (venue/ledger rounding, fee-in-base
  drift). Below that the fill is reported as a partial exit and monitoring
  continues. A closure sets a `position_closed` marker in per-watch state:
  the watch's levels/signals are no longer evaluated and the marker is
  visible in `--status`.
- **No balance read** anywhere in the detection path. No ledger write,
  no order placement — the skill stays monitor-and-alert; the alert tells
  the reader to record the exit in the ledger so the next sync prunes the
  held entry.
- **Coverage limits are stated, not papered over.** Order history is read,
  so a wick that fills and recovers between samples is still detected;
  but latency is up to one tick, only the orders inside the fetched
  closed-orders page are seen, and a tick whose price fetch or venue read
  fails reports nothing.
- The provider gains a read-only closed-orders method on the Kraken spot
  adapter (not on the shared execution protocol — the perps adapter does
  not implement it). Any monitor that can read its venue's order history
  can host the same detector; this is a capability of the class
  "tick-driven monitors", not a one-off hook.

## Consequences

- (+) A venue stop-out is surfaced with position, fill price, quantity and
  realised P&L, and the phantom position stops generating level
  evaluations.
- (+) Attribution is exact — the venue's own order record, never a
  balance heuristic.
- (-) Detection latency is up to one tick, and a failed tick (price fetch
  or venue read) reports nothing that tick.
- (-) The ledger still has no row until the exit is recorded; the alert
  says so and names the reconciliation step.
- (-) A burst of closed orders larger than the fetched page between two
  ticks can push a fill out of the detector's view.
