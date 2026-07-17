# backtest-engine — pitfalls

The five failure modes a backtest engine can hit, with the guard that
addresses each. These are the failure modes called out in the engine's design
rationale; each guard is structural (it cannot be forgotten by a caller)
rather than conventional (a comment that says "remember to...").

## 1. Look-ahead bias

**Failure shape.** The "compute once on full history, then walk the output"
anti-pattern: a strategy computes all its ideas up front using the full candle
series, then the loop just reads `ideas[t]`. The idea at bar `t` can then
depend on bars `> t` (a moving average that secretly used the whole series, a
swing high found using future bars, etc.). The backtest looks great and trades
terribly — the classic backtest-vs-live divergence.

**Guard.** Two layers:

- *Structural:* `WalkForwardRunner.run` calls
  `strategy.analyze(candles[:t+1], ...)` at each bar — the strategy only ever
  receives the prefix up to and including bar `t`. It cannot see a future bar
  because the prefix slice physically excludes it.
- *Loud-failure:* before the loop starts, `run` raises `NoLookaheadError` if
  the strategy exposes a `precomputed_ideas` attribute. This catches the
  compute-once-then-walk shape explicitly rather than relying on the prefix
  slice to silently mask it.

## 2. Intrabar ambiguity

**Failure shape.** OHLCV does not reveal which price was touched first inside
a bar. When a single bar's range touches BOTH the stop and the target, the
true exit is unknowable from the candle alone — the stop could have fired
first (adverse) or the target first (favourable). A backtest that
optimistically assumes the target first overstates returns; one that
pessimistically assumes the stop first understates them.

**Guard.** `FillSimulator` uses a **stop-first tie rule**: when one bar's
range touches both the stop and the target, the STOP is assumed to fire first
(worst case) and the trade exits at `stop_loss`. A target fills only on a bar
where the stop is not touched. This is the conservative (adverse) choice —
the backtest's reported edge is a lower bound, not an upper bound. Intrabar
ordering is unknowable from OHLC, so the engine picks the outcome that hurts
the strategy.

## 3. Recompute cost

**Failure shape.** A naive walk-forward that re-runs the strategy on the full
prefix at every bar is `O(n²)` in compute (the prefix grows by one each bar,
and the strategy re-scans it from the start). For strategies that compute
expensive indicators (e.g. a long EMA, a swing scan) the cost is `O(n³)` or
worse in indicator work. A 1000-bar replay can take minutes instead of
milliseconds.

**Guard.** The runner is cache-free. The walk-forward loop replays each strict
prefix of the candle series exactly once, so there is no repeated prefix to
cache — the `O(n²)` prefix construction cost is unavoidable (the prefix grows by
one bar each step) but there is no extra `O(n²)` memoization overhead on top of
it. Because the prefix is never revisited within a single `run()`, a cache would
never hit and would only add `O(n²)` time/memory for zero benefit. The no extra
global state keeps the replay honest: two runs with identical inputs return
identical outputs because the strategy is deterministic, not because of retained
state.

## 4. TTL cache leakage

**Failure shape.** A module-global `lru_cache` (or `functools.cache`) on an
indicator helper leaks state between unrelated runs / strategies / tickers.
The cache key omits something that should distinguish runs (the ticker, the
strategy, the warmup), so run B reads run A's cached indicator values. The
backtest for strategy B silently uses strategy A's numbers — results look
stable (deterministic) and wrong.

**Guard.** There is no module-global cache anywhere in the engine — the runner
is cache-free by design. Two runs with identical inputs return identical outputs
because the strategy is deterministic, not because of retained state. The
runner retains no state between calls; each `.run()` replays the prefixes exactly
once and returns.

## 5. Small-sample Sharpe

**Failure shape.** Sharpe on 1-2 trades (or a tiny equity-curve window)
produces extreme or misleading ratios: with a single trade there is one daily
return, so the standard deviation is undefined (or zero), and `mean / 0` blows
up to `inf` / `nan`. A backtest that reports `Sharpe = inf` on one winning
trade is not measuring edge — it is measuring the absence of variance. The
number looks impressive and carries no information.

**Guard.** `compute()` returns `sharpe = 0.0` when there are fewer than two
daily returns OR the standard deviation of daily returns is zero. The same
guard applies to `sortino` (zero downside variance → `0.0`). The curve-derived
metrics (`total_return`, `annualized_return`, `max_drawdown`) also collapse to
`0.0` when the equity curve has fewer than two points or a zero base. So a
single-trade series reports `sharpe = 0.0` (not `inf`), and an empty curve
reports the all-zero shape — no `inf`, no `nan`. The benchmark (which always
has a non-empty curve) still gets a meaningful Sharpe; an empty-trade strategy
is reported as `trade_count = 0` rather than producing a misleading ratio.
