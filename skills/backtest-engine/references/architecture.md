# backtest-engine — architecture

The per-bar data flow, the macro/regime replay caveats, and the fill/decision
logging contract. This is the "what the system is" companion to the
[why-we-built-it-this-way](../README.md) record; the engine's design choice
(purpose-built, not a framework) is in [SKILL.md § Purpose](../SKILL.md#purpose).

## Per-bar data flow

```
                          fetch_ohlc(ticker, interval, period)   (or --demo synth)
                                          │
                            ┌─────────────▼─────────────┐
                            │  --from/--to post-fetch    │   _slice_candles_by_iso
                            │  timestamp slice (incl.)   │   candles[t][0] = Unix s
                            └─────────────┬─────────────┘
                            │  --bars = N most-recent    │
                            ▼
                       candles: list[Bar]
                                          │
               WalkForwardRunner().run(strategy, ticker, candles,
                                                  warmup, interval, period, asset_class)
                                          │
           ┌──────────────────────────────┴──────────────────────────────┐
           │  for t in [warmup, n):                                       │
           │      prefix = candles[:t+1]          ◄── no-lookahead slice  │
           │      result = strategy.analyze(prefix, ticker=, interval=,  │
           │                                   period=, asset_class=)    │
           │      idea   = result["ideas"][0] if ideas else None         │
           │      windows.append(IdeaWindow(bar_index=t, asof_ts=…,     │
           │                                 idea=idea))                 │
           └──────────────────────────────┬──────────────────────────────┘
                                          │
                              list[IdeaWindow]   (one per bar t in [warmup, n))
                                          │
           ┌──────────────┬───────────────┼───────────────┬────────────────┐
           ▼              ▼               ▼               ▼                ▼
      (default)      --fill-sim      --metrics       --forensic-drill    --json
      print counts   FillSimulator   compute()+      one bar: decision,  AXI
                     per fired idea  buy_and_hold_   fill, risk verdict   envelope
                     → TradeRecord   benchmark()     (see below)          {data,
                                                                     count,errors,help}
```

Key invariants on the per-bar path:

- **One prefix per bar.** `strategy.analyze` is called with `candles[:t+1]` —
  the idea for bar `t` cannot depend on any bar `> t`. This is the structural
  no-lookahead guard; `NoLookaheadError` is the loud-failure guard (rejected
  before the loop if the strategy exposes `precomputed_ideas`).
- **`--from`/`--to` slice post-fetch.** The data layer
  `fetch_ohlc(ticker, interval, period)` has no `--from`/`--to`, so the CLI
  fetches the full period then narrows the window by `candles[t][0]` (Unix
  seconds), inclusive on both ends. `--bars` (N most-recent) is applied after
  the date filter. The slice never changes the prefix semantics — it only
  shortens the series the loop walks.
- **`--forensic-drill` reuses the same replay.** It does not re-fetch or
  re-analyze differently; it runs the same `WalkForwardRunner.run`, picks the
  `IdeaWindow` whose `bar_index` matches, then runs `FillSimulator.simulate`
  for that one bar and prints the three blocks. The audit trail is exactly
  what the replay produced — no second code path to drift out of sync.
- **`--metrics` is pure-strategy.** It builds the equity curve from the
  `TradeRecord` list (`--fill-sim` is required) and the buy-and-hold curve
  from the candles; no regime, no macro, no risk verdict feeds the metrics.

## Macro / regime replay caveats

The L3 strategy `analyze(candles, *, ticker, interval, period, asset_class)`
signature does **not** take a `RegimeSignal`. The `WalkForwardRunner.run`
signature mirrors that, so during walk-forward replay the strategy consumes
only its own candle prefix — no cross-asset macro context is replayed. This is
intentional for bt-1..bt-5: the metrics path measures the strategy's
pure-price edge, not a strategy+regime composite.

Consequences:

- If a strategy reads regime from its own candles (e.g. an internal breadth
  or dominance proxy computed from the prefix), no-lookahead is still
  preserved — the prefix slice guarantees it. The regime is just another
  function of `candles[:t+1]`.
- If a strategy would normally *fetch* a `RegimeSignal` at runtime (the macro
  module hits F&G / VIX / DXY / CoinGecko live), that fetch is **not** replayed
  — the walk-forward loop never calls the macro module. A live run and a
  backtest over the same window can therefore diverge if the strategy's live
  path branches on regime. This is the honest boundary: the backtest measures
  the price-only strategy; regime-conditional behaviour is a bt-3+ concern
  (risk-engine wiring and an optional regime replay hook).
- `analysis/macro/` (live `RegimeSignal` fetch) and `analysis/chop.py`
  (conviction-calibration `chop_score` over L3 idea history) are both
  out-of-band for the replay — neither is invoked by `WalkForwardRunner`.

## Fill / decision logging contract

Every simulated round-trip is a `TradeRecord` composed of two real
`FillConfirmation` TypedDicts (`entry` + `exit`), so the audit trail uses the
exact on-the-wire shape live trading produces — a future orchestrator can run
the same post-fill handling on either side.

### TradeRecord fields

| field | type | notes |
|-------|------|-------|
| `entry` | `FillConfirmation` | next-bar-open fill, slippage + entry fee applied |
| `exit` | `FillConfirmation` | stop/target close fill; `fill_price=None` when still open |
| `status` | `str` | `"filled"` / `"open"` / `"skipped"` |
| `exit_reason` | `str` | `"stop"` / `"target"` / `"none"` |
| `exit_bar_index` | `int \| None` | bar where the exit fired |
| `pnl_quote` | `float \| None` | realized quote P&L for `"filled"`; `None` otherwise |

### Raw slots (simulator-specific, under each fill's `raw`)

| `raw` key | meaning |
|-----------|---------|
| `qty` | mirror of `filled_volume` (position size) |
| `fee_paid` | mirror of `fee` (entry fee, quote) |
| `slippage_paid` | quote cost of entry slippage (abs), entry only |
| `entry_price` | post-slippage entry fill (exit fill only) |
| `exit_reason` | `"stop"` / `"target"` / `"none"` / `"skipped"` |
| `open_price` | the raw next-bar open before slippage (entry fill only) |
| `entry_bar_index` | the bar the entry filled at (entry fill) / exited at (exit fill) |

These keep the `FillConfirmation` TypedDict contract intact — no field is
added to `analysis/providers/execution/base.py`; simulator specifics live
under `raw`.

### `intent_id` format

`intent_id = f"bt-{pair}-{entry_bar_index}"` (e.g. `bt-BTCUSD-150`), unless an
explicit `intent_id` is passed via the `ctx` dict. `cl_ord_id` mirrors
`intent_id` (the LLM owns intent_id uniqueness in live trading; in the
backtest it is deterministic from the bar). `order_id` is
`bt-entry-{entry_bar_index}` / `bt-exit-{exit_bar_index}`.

### Venue + status

`venue="backtest"` on every fill. The trade-level `status` adds one
backtest-only pseudo-status, `"skipped"` (no next bar to fill the entry),
which is NOT in the live `FillConfirmation` status set
(`filled` / `partial` / `open` / `rejected` / `cancelled` / `expired` /
`error`). `fee_currency` is left empty — the simulator has no venue to report
the fee currency from (the live adapter populates it from the venue response).

### What `--forensic-drill` reconstructs

For one chosen `bar_index`, the drill reconstructs and prints:

1. **decision** — the `L3Idea` at that bar (`pair`, `direction`, `conviction`,
   `version`, `entry_price`, `stop_loss`, `take_profit` ladder, `entry_type`,
   `reasoning`), read from the `IdeaWindow` the replay already produced.
2. **fill** — `FillSimulator.simulate(idea, candles, bar_index, ctx)` → the
   `TradeRecord`, summarized as entry/exit `fill_price` / `qty` / `fee` /
   `slippage_paid` / `status` / `exit_reason` / `exit_bar_index` / `pnl_quote`.
3. **risk verdict** — derived locally (preliminary, see SKILL.md): the
   stop/TP1 distances, `rr_to_tp1`, conviction/version, and
   `would_vet` / `preliminary` flags. This is NOT a `RiskVerdict` from
   `analysis.risk.vet` — bt-3 risk-engine wiring is forthcoming; until then
   the verdict is a deterministic function of the idea's stop/TP ladder and
   direction so the audit trail is still reconstructable without the risk
   engine in tree.

Because the drill reuses the same `WalkForwardRunner.run` + `FillSimulator`,
the reconstructed decision/fill are byte-identical to what the standard replay
path would have produced at that bar — there is no separate forensic code path
to drift.
