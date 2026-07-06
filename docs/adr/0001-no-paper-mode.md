# 0001. No paper mode for execution

- **Status**: accepted
- **Date**: 2026-06-22

## Context

We needed a way to test order construction — building the `Intent`,
running it through `risk-engine`, formatting the venue call — without
actually placing orders on the venue. The natural option was a "paper
mode" that simulates fills against a fake order book so the full
intent → fill → portfolio loop could run in CI.

## Decision

No paper mode. `--dry-run` validates the order against the venue
(`kraken order --validate`) without side effects, and live submit
**always** prompts for user confirmation unless `--yes` is passed.

## Consequences

- (+) One fill model — what the venue returned is what we recorded. No
  parallel simulation that could drift from the live venue's actual
  fill semantics (partial fills, slippage, lot rounding).
- (+) Smaller surface area; no paper-fill code path to keep in sync.
- (+) Forces every order through the real risk vet, including the
  perps state fetch — paper testing would silently skip venue-only
  signals (open positions, funding rate).
- (-) Cannot backtest the order-placement layer in isolation against
  historical prices. The agent brain has to test against live or
  not at all.
- (-) `--yes` is the only way to bypass the confirm prompt. Users who
  want fully-automated cron trading need to opt in explicitly.
