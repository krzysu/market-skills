# 0011. Intent.extras carries two namespaces: venue flags and in-repo risk-layer keys

- **Status**: accepted
- **Date**: 2026-09-24

## Context

`Intent.extras` is a free-form dict shared by two layers that read it
differently. The risk side (`analysis/risk/_common.py::
resolve_intent_notional`) documents `extras.reference_price` (and
`est_notional` / `position_value`) as sanctioned price/notional hints for
market orders; the execution side (`analysis/providers/execution/
kraken_spot.py`) forwarded every extras key to the `kraken` CLI as
`--<key>` (underscore → dash). The halves only disagree when `extras`
carries a risk-layer key rather than a venue kwarg — and the disagreement
was invisible to the pre-flight check: the dry-run branch built its own
argv and never forwarded extras, so `kraken order --validate` passed an
intent whose live submit died with `error: unexpected argument
'--reference-price' found` before the venue was reached, exiting 0 while
the confirmation carried `status: "error"`. A documented-compliant Intent
was silently unexecutable, and the only clue was an argument-parsing hint
inside a `reason` string.

Three options existed: (1) allowlist the forwarded keys, (2) move the
price hint into a dedicated Intent field, (3) reject unknown extras keys
at the boundary. A hand-written deny list of "keys that must not be
forwarded" rots: every new risk-layer extras key would have to be added
to it, and forgetting one silently reintroduces the bug. Option 2 changes
the Intent shape and every producer for one key that is already
well-documented.

## Decision

Keep `extras` as the shared dict and declare the two namespaces in
`analysis/providers/execution/base.py`, the Intent contract module:

- **Venue flags** — `kraken_spot.py::KRAKEN_ORDER_FLAGS`, the dash-spelled
  allowlist of order-level options derived from the real CLI surface
  (`kraken order buy --help` / `kraken order sell --help`), excluding
  process/global options and `--validate`. Only these are forwarded as
  `--key value` (underscore → dash).
- **In-repo keys** — `NON_VENUE_INTENT_EXTRAS` (next to the `Intent`
  TypedDict), one entry per extras key consumed inside this repo
  (`reference_price`, `est_notional`, `position_value`,
  `reference_entry`, `futures_symbol`), each commented with its consuming
  module. A venue adapter keeps these in the Intent and never forwards
  them.

`extras_to_cli_args` classifies every key deterministically: allowlisted →
forward, declared in-repo → keep, anything else →
`UnknownExtrasKeyError` naming the key and the layer boundary — raised
BEFORE any venue call, by the provider and by the CLI's pre-flight gate.

Dry-run and live share one argv builder (`kraken_spot.py::
build_order_args`), so the pre-flight check cannot green-light an intent
the live submit fails on an extras-forwarding error — the agreement holds
by construction, not by keeping two argv builders in sync.

The deny list was rejected in favour of the allowlist + fail-loud combo
precisely because the failure mode of a stale deny list is silent
(execute-time CLI error) while the failure mode of the allowlist is loud
(a pre-venue rejection naming the key and the layer). The execution
side never imports `analysis.risk.*`; the namespace declaration lives in
the execution layer's contract module and the risk layer's reads are
locked to it by a rot-guard test that extracts the extras keys the risk
code actually reads and asserts each is declared.

## Consequences

- (+) A market Intent carrying the documented price hint validates,
  submits, and keeps the hint on the Intent for the risk layer.
- (+) An undeclared extras key fails before the venue call with a message
  naming the key and which layer consumes it — no more rc=2
  argument-parsing errors at submit time, and no silent unexecutable
  intents.
- (+) Dry-run and live forward exactly the same venue flags by
  construction.
- (-) A new risk-layer extras key must be declared in
  `NON_VENUE_INTENT_EXTRAS` in the same change — the rot-guard test fails
  otherwise. Forgetting the declaration is now a visible test failure
  instead of a live-submit break.
- (-) The `KRAKEN_ORDER_FLAGS` allowlist must be re-derived if the
  `kraken` CLI's order surface changes; a provenance guard test checks
  every flag against the CLI's `--help` output where the binary is
  installed.
