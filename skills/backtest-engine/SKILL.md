---
name: backtest-engine
description: "Purpose-built walk-forward replay engine + deterministic fill simulator for L1/L2/L3 strategies. Drives per-ticker bar iteration, feeding each strategy only the past at bar t so no idea can look ahead. bt-1 covers the replay loop; bt-2 adds the conservative intrabar fill simulator; bt-4 adds per-strategy metrics + buy-and-hold benchmark; bt-5 adds the canonical CLI (--strategy/--ticker/--interval + --from/--to filters, --json AXI envelope, --forensic-drill audit trail)."
version: 0.5.0
metadata:
  hermes:
    tags: [backtest, replay, walk-forward, l3, fill-simulator, metrics, benchmark, forensic-drill]
    category: backtest
  compatibility: "Requires Python 3.12+ and uv"
---

# backtest-engine

Purpose-built backtest engine that reuses the existing market-skills L1/L2/L3
strategies as-is. **bt-1** implements the walk-forward replay loop and the
per-bar data contracts. **bt-2** adds a deterministic, conservative
`FillSimulator` (next-bar-open entry, stop-first intrabar tie, configurable
fee + slippage). **bt-4** adds per-strategy metrics (`compute`) and a
buy-and-hold benchmark (`buy_and_hold_benchmark`). **bt-5** polishes the CLI,
docs, and audit trail: the canonical form `--strategy/--ticker/--interval` with
`--from/--to` date filters, a `--json` AXI envelope, and a `--forensic-drill`
command that reconstructs the per-bar decision, fill, and a preliminary risk
verdict for one chosen bar.

## Purpose

Drive a strategy bar-by-bar over an OHLC series, calling
`strategy.analyze(candles[:t+1], ...)` at each bar so the idea for bar `t` is
built from the past only. Returns one `IdeaWindow` per bar after warmup. The
"compute once on full history, then walk the output" anti-pattern is rejected
with `NoLookaheadError`.

This is a **purpose-built** engine, not a framework (vectorbt / backtrader /
qlib). The market-skills strategies already exist as `analyze(candles, ...)`
callables that return `{ideas, narrative}`; the engine just drives them
bar-by-bar with a prefix slice and a conservative fill model. A framework would
force every strategy to subclass a `Strategy` base, override `next()`/`init()`,
and learn its bar/event model — pure overhead when the strategies are already
pure functions of a candle prefix. The only abstractions the engine owns are
`WalkForwardRunner` (the loop), `FillSimulator` (intrabar fills), and
`compute` / `buy_and_hold_benchmark` (metrics). See
[references/architecture.md](./references/architecture.md) for the per-bar data
flow and the fill/decision logging contract.

## When to use

- Replay how a strategy *would have* acted at each historical bar, with no
  look-ahead.
- Sanity-check a strategy's per-bar firing pattern over a deterministic series.
- Simulate fills / fees / slippage on the replayed ideas (`--fill-sim`, bt-2).
- Compare a strategy to buy-and-hold with per-strategy Sharpe / drawdown /
  profit factor (`--metrics`, bt-4).
- Reconstruct the exact decision, fill, and risk verdict at one bar for an
  audit trail (`--forensic-drill`, bt-5).

## NOT to use

- Live signal generation — use the strategy skill's own `scripts/run.py`.
- Real execution — `FillSimulator` is a model, not a venue adapter; use
  `execution-kraken-spot` / `execution-kraken-perps` for live fills.
- Running a strategy on the full history once and slicing the output — the
  runner raises `NoLookaheadError` if the strategy exposes `precomputed_ideas`.
- Drawing conclusions from a Sharpe on 1-2 trades — see
  [references/pitfalls.md](./references/pitfalls.md) (small-sample Sharpe).

## Quick Start

```bash
# Canonical form (bt-5) — optional flags override the positionals:
uv run skills/backtest-engine/scripts/run.py \
    --strategy strategy-trend-follow --ticker DEMO --interval 1d \
    --warmup 100 --bars 200 --dry-run --demo

# Positional form (backwards compat — still exercised by existing examples):
uv run skills/backtest-engine/scripts/run.py strategy-trend-follow DEMO 1d --warmup 100 --bars 200 --dry-run --demo

# Narrow the replay window with --from/--to (ISO date or full ISO 8601, inclusive):
uv run skills/backtest-engine/scripts/run.py strategy-trend-follow yf:BTC-USD 1d \
    --from 2024-01-01 --to 2024-06-30 --warmup 50 --dry-run

# Replay + simulate fills (bt-2): per-trade entry/exit, fees, slippage, P&L:
uv run skills/backtest-engine/scripts/run.py strategy-mean-reversion DEMO 1d --warmup 50 --bars 300 --dry-run --demo --fill-sim

# Replay + fills + metrics/benchmark (bt-4): per-strategy metrics + buy-and-hold:
uv run skills/backtest-engine/scripts/run.py strategy-trend-follow DEMO 1d --warmup 100 --bars 200 --dry-run --demo --fill-sim --metrics

# Machine output (bt-5): AXI envelope {data, count, errors, help}:
uv run skills/backtest-engine/scripts/run.py strategy-trend-follow DEMO 1d --warmup 100 --bars 200 --demo --fill-sim --metrics --json

# Audit trail (bt-5): per-bar decision + fill + (preliminary) risk verdict:
uv run skills/backtest-engine/scripts/run.py strategy-trend-follow DEMO 1d --warmup 100 --bars 200 --demo --fill-sim --forensic-drill 150
```

`--dry-run` prints the count of replayed windows and the count where the
strategy actually fired an idea, then exits 0. If `fetch_ohlc` returns no data
the script prints `0 ideas (no data)` and exits 0. `--fill-sim` additionally
runs `FillSimulator` over each fired idea and prints a trade summary + one
line per trade (entry/exit price, exit reason, realized P&L). `--metrics`
(requires `--fill-sim`) appends a stable `sort_keys` JSON block with the
per-strategy metrics and the buy-and-hold benchmark. `--json` replaces the
human-readable text with the canonical AXI envelope. All four default OFF, so
bt-1 dry-run output is unchanged when the flags are absent.

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--strategy=NAME` | — | L3 strategy skill name (e.g. `strategy-trend-follow`). Overrides the positional `STRATEGY`. |
| `--ticker=SYM` | — | Ticker; supports `provider:ticker` (e.g. `yf:BTC-USD`). Overrides the positional `TICKER`. |
| `--interval=TF` | — | Candle interval (`1d`, `4h`, `1h`, ...). Overrides the positional `INTERVAL`. |
| `STRATEGY` (positional) | — | Backwards-compat form; optional when `--strategy` is given. |
| `TICKER` (positional) | — | Backwards-compat form; optional when `--ticker` is given. |
| `INTERVAL` (positional) | — | Backwards-compat form; optional when `--interval` is given. |
| `--from=DATE` | — | Inclusive start filter (ISO `YYYY-MM-DD` or full ISO 8601); slices candles by `candles[t][0]` post-fetch. |
| `--to=DATE` | — | Inclusive end filter (ISO `YYYY-MM-DD` or full ISO 8601); date-only `--to` is end-of-day (23:59:59 UTC). |
| `--warmup=N` | `0` | Bars to skip before emitting ideas. |
| `--bars=N` | `0` | Use the N most-recent candles (`0` = all fetched). Applied after `--from`/`--to`. |
| `--period=PERIOD` | `2y` daily+ / `1y` intraday | Lookback period. |
| `--asset-class=CLASS` | auto | Asset class hint forwarded to the strategy. |
| `--dry-run` | off | Replay and print idea counts; do not trade. |
| `--demo` | off | Offline demo: synthetic candles, no network. The demo is a downtrend-then-V-recovery so a trend-follow strategy fires at the reversal bar. |
| `--fill-sim` | off | Run `FillSimulator` over each fired idea; print a trade summary (bt-2). Default off — preserves bt-1 dry-run. |
| `--fee-bps=N` | `26` | FillSimulator taker fee in bps (26 = Kraken 0.26%). |
| `--slippage-bps=N` | `2` | FillSimulator per-side slippage floor in bps. |
| `--qty=Q` | `1.0` | Position size in base units for `--fill-sim` (sizing is bt-3). |
| `--metrics` | off | Print per-strategy metrics + buy-and-hold benchmark as stable JSON (bt-4). Requires `--fill-sim`. |
| `--forensic-drill=BAR` | — | Replay one bar and print its decision, fill, and (preliminary) risk verdict (bt-5). See [Forensic drill](#forensic-drill). |
| `--json` | off | Emit the AXI envelope `{"data", "count", "errors", "help"}` instead of human-readable text (bt-5). |

If a required input (`strategy` / `ticker` / `interval`) is missing from both
the positional and the optional flag, the script errors out cleanly (exit 2).

## Architecture

The engine has three moving parts — `WalkForwardRunner` (the loop),
`FillSimulator` (intrabar fills), and `compute` / `buy_and_hold_benchmark`
(metrics) — and one audit-trail command (`--forensic-drill`). The per-bar data
flow, the macro/regime replay caveats, and the fill/decision logging contract
(TradeRecord fields, `intent_id` format, `venue="backtest"`) are documented in
[references/architecture.md](./references/architecture.md).

## No-lookahead invariant

The idea for bar `t` is built from `candles[:t+1]` only — the strategy never
sees a bar `> t`. `WalkForwardRunner.run` raises `NoLookaheadError` before the
loop starts if the strategy exposes a `precomputed_ideas` attribute (the
"compute once on full history, then walk the output" anti-pattern). The
prefix slice is the structural guard; the exception is the loud-failure guard.
The runner is cache-free: the walk-forward loop replays each strict prefix of
the candle series exactly once, so there is no repeated prefix to cache. Two
runs with identical inputs return identical outputs because the strategy is
deterministic and the runner retains no state between calls. See
[references/pitfalls.md](./references/pitfalls.md) for the look-ahead and
TTL-cache-leakage failure modes and their guards.

## API

```python
from analysis.skill_loader import load_skill

bt = load_skill("backtest-engine")
strategy = load_skill("strategy-trend-follow")

windows = bt.WalkForwardRunner().run(
    strategy,
    "BTC-USD",
    candles,
    warmup=100,
    interval="1d",
    period="2y",
    asset_class=None,
)
# -> list[IdeaWindow], one entry per bar from `warmup` to len(candles)-1

# bt-4: per-strategy metrics + buy-and-hold benchmark
metrics = bt.compute(records, equity_curve)
bench = bt.buy_and_hold_benchmark(candles, warmup=100, fee_bps=26, slippage_bps=2)
bench_metrics = bt.compute([], bench)
```

`WalkForwardRunner.run(strategy, ticker, candles, *, warmup=0, interval="1d", period="1y", asset_class=None)`
is keyword-only after `candles`. The constructor takes no arguments — `strategy`
is passed per call. See [Metrics + benchmark (bt-4)](#metrics--benchmark-bt-4)
for `compute` / `buy_and_hold_benchmark` signatures and the metric definitions.

## Bar / IdeaWindow contract

`Bar` (TypedDict) — one candle:

| field | type |
|-------|------|
| `timestamp` | `int` (Unix seconds) |
| `open` | `float` |
| `high` | `float` |
| `low` | `float` |
| `close` | `float` |
| `volume` | `int` |

`Candles = list[Bar]` — the existing list-of-lists shape used across the
codebase (`[[ts, o, h, l, c, v], ...]` at runtime).

`IdeaWindow` (TypedDict):

| field | type | notes |
|-------|------|-------|
| `bar_index` | `int` | position in the input `candles` list |
| `ticker` | `str` | the ticker passed to `run` |
| `asof_ts` | `int` | `candles[bar_index][0]` — the bar the idea was built from |
| `idea` | `L3Idea \| None` | first emitted idea, or `None` when nothing fired |

The first idea is extracted because L3 strategies emit at most one idea per
call; `None` is kept (not dropped) so the window length stays observable.

## Fill simulator (bt-2)

`FillSimulator` turns a replayed `L3Idea` into a simulated round-trip trade
with deterministic, conservative intrabar rules. It returns a `TradeRecord`
composed of **two real `FillConfirmation` TypedDicts** (`entry` + `exit`) —
the exact shape live trading uses (`analysis/providers/execution/base.py`) —
so a future orchestrator can run the same post-fill handling on either side.

### API

```python
from analysis.skill_loader import load_skill

bt = load_skill("backtest-engine")
sim = bt.FillSimulator()                       # defaults: fee_bps=26, slippage_bps=2
rec = sim.simulate(idea, candles, entry_bar_index, ctx=None, *, fee_bps=None, slippage_bps=None)
# -> TradeRecord
```

`FillSimulator(*, fee_bps=26, slippage_bps=2)` — instantiable with no args;
defaults are Kraken taker tier 0.26% + a 2bps per-side slippage floor.

`simulate(idea, candles, entry_bar_index, ctx=None, *, fee_bps=None, slippage_bps=None) -> TradeRecord`
— per-call `fee_bps` / `slippage_bps` kwargs override the `ctx` dict which
overrides the instance defaults (priority: kwargs > ctx > instance). `ctx`
also carries `qty` (position size in base units, default `1.0`; risk-engine
sizing is bt-3) and an optional `intent_id`.

### Decision model

- **Next-bar-open entry.** The entry fills at `candles[entry_bar_index+1].open`
  — a strategy decides at bar `t` and the earliest it can act is bar `t+1`'s
  open, never the signal bar's close. When `entry_bar_index+1` is out of range
  the fill is skipped (`status="skipped"`) and a debug log line is emitted.
- **Stop-first intrabar tie.** When a single bar's range touches BOTH the stop
  and the target, the STOP is assumed to fire first (worst case) and the trade
  exits at `stop_loss`. A target fills only on a bar where the stop is not
  touched. Intrabar ordering is unknowable from OHLC, so the conservative
  (adverse) outcome is assumed.
- **Slippage** is applied to the entry as a worst-case fill: longs pay
  `open * (1 + slippage_bps/1e4)`, shorts receive `open * (1 - slippage_bps/1e4)`.
- **Target** means `take_profit[0]` (the TP ladder is a list; L3 strategies
  validate ladders via `analysis.contracts.validate_l3_tp_ladder`).

### Fee model (v1 — entry only)

`fee = cost_quote * fee_bps / 10_000` where
`cost_quote = filled_volume * fill_price` (the entry fill price,
post-slippage). Exit fees are NOT modelled in v1 — planned for a v1.1
follow-up if requested. Defaults: `fee_bps=26` (Kraken taker 0.26%),
`slippage_bps=2` (per-side slippage floor).

### TradeRecord

| field | type | notes |
|-------|------|-------|
| `entry` | `FillConfirmation` | open fill at next-bar open (slippage + entry fee applied) |
| `exit` | `FillConfirmation` | close fill at stop/target price; `fill_price=None` when still open |
| `status` | `str` | `"filled"` / `"open"` / `"skipped"` |
| `exit_reason` | `str` | `"stop"` / `"target"` / `"none"` |
| `exit_bar_index` | `int \| None` | bar where the exit fired |
| `pnl_quote` | `float \| None` | realized quote P&L for `"filled"` (exit vs entry, minus entry fee); `None` otherwise |

`status="skipped"` is a backtest-only pseudo-status (no next bar to fill the
entry); it is NOT in the live `FillConfirmation` status set
(`filled` / `partial` / `open` / `rejected` / `cancelled` / `expired` / `error`).

### Slot plumbing

Simulator-specific named slots live under each fill's `raw` dict, keeping the
`FillConfirmation` TypedDict contract intact (no field added to
`analysis/providers/execution/base.py`):

| `raw` key | meaning |
|-----------|---------|
| `qty` | mirror of `filled_volume` (position size) |
| `fee_paid` | mirror of `fee` (entry fee, quote) |
| `slippage_paid` | quote cost of entry slippage (abs), entry only |
| `entry_price` | post-slippage entry fill (exit fill only) |
| `exit_reason` | `"stop"` / `"target"` / `"none"` / `"skipped"` |

### Example

```python
sim = bt.FillSimulator(fee_bps=26, slippage_bps=2)
rec = sim.simulate(idea, candles, entry_bar_index=10, ctx={"qty": 0.5})
if rec["status"] == "filled":
    print(rec["exit_reason"], rec["exit"]["fill_price"], rec["pnl_quote"])
```

## Metrics + benchmark (bt-4)

`compute` turns a trade list + equity curve into a per-strategy metrics dict.
`buy_and_hold_benchmark` builds a mark-to-market equity curve for one unit
bought at `candles[warmup + 1].open` — the same bar the strategy's earliest
fill occurs — and held to the end, using the same worst-case fee + slippage
rule as `FillSimulator`. The strategy equity curve counts **realized (closed)
trade PnL only**; open trades still open at series end are excluded, because
marking them to the last close injected forward-looking noise and produced
absurd returns. Comparability with the benchmark is preserved via the
benchmark curve (same base capital, cost-basis + close), not via M2M. The
CLI `--metrics` flag (requires `--fill-sim`) prints both as stable
`sort_keys` JSON.

### API

```python
from analysis.skill_loader import load_skill

bt = load_skill("backtest-engine")

metrics = bt.compute(records, equity_curve, risk_free_rate=0.0, periods_per_year=365)
# -> {"trade_count", "total_return", "annualized_return", "sharpe",
#     "sortino", "max_drawdown", "profit_factor", "average_trade"}

bench = bt.buy_and_hold_benchmark(candles, warmup, *, fee_bps=26, slippage_bps=2)
# -> list[float], cost basis + one close per bar from warmup to the end
bench_metrics = bt.compute([], bench)
```

`compute(trades, equity_curve, risk_free_rate=0.0, *, periods_per_year=365) -> dict`
— `risk_free_rate` is positional-or-keyword; `periods_per_year` is
keyword-only. Returns a dict with keys in canonical insertion order:
`trade_count`, `total_return`, `annualized_return`, `sharpe`, `sortino`,
`max_drawdown`, `profit_factor`, `average_trade`.

`buy_and_hold_benchmark(candles, warmup, *, fee_bps=26, slippage_bps=2) -> list[float]`
— keyword-only after `warmup`. Returns `[]` when `len(candles) <= warmup`,
else `len(candles) - warmup + 1` floats: the cost basis
(`open * (1+slip) * (1+fee)`) followed by one close per bar from `warmup`
onward. The `total_return` derived from this curve is
`(last_close - cost_basis) / cost_basis`, so it carries the entry cost.

### Metric definitions

| key | definition |
|-----|------------|
| `trade_count` | `len(trades)` |
| `total_return` | `(equity_curve[-1] - equity_curve[0]) / equity_curve[0]` when the curve has ≥ 2 points and a non-zero base; else `0.0` |
| `annualized_return` | `(1 + total_return) ** (periods_per_year / (n - 1)) - 1` (geometric); `0.0` when `n < 2` or `total_return == 0.0` |
| `sharpe` | `mean(daily_returns) / stdev(daily_returns) * sqrt(p)`; `0.0` when `< 2` daily returns or zero variance (a single-trade series → Sharpe = 0) |
| `sortino` | same numerator over downside deviation (stdev of negative returns only) `* sqrt(p)`; `0.0` when no negative returns or zero downside variance |
| `max_drawdown` | largest `(peak - equity) / peak` over the curve; `0.0` for a monotonically non-decreasing curve |
| `profit_factor` | `sum(pos pnl) / abs(sum(neg pnl))`; `float("inf")` sentinel when there are no losers but ≥ 1 positive pnl; `0.0` when no numeric pnls |
| `average_trade` | mean of non-`None` `pnl_quote` values; `0.0` when no trade has a numeric pnl |

Daily returns are `equity_curve[i] / equity_curve[i-1] - 1`; a zero base
(`equity_curve[i-1] == 0`) yields `0.0` to avoid division-by-zero on P&L
curves that start flat at 0.0. `risk_free_rate` is subtracted from the mean
per-period return before scaling (treated as a per-period rate; default
`0.0`). `periods_per_year` defaults to `365` (daily bars / 24-7 crypto).

### Empty-input contract

When `equity_curve` is empty the curve carries no information, so `compute`
returns the all-zero shape (no `inf`, no `nan`):

```python
{"trade_count": 0, "total_return": 0.0, "annualized_return": 0.0,
 "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
 "profit_factor": 0.0, "average_trade": 0.0}
```

A strategy that fired no trades (`trades=[]`) but has a non-empty equity curve
(e.g. the buy-and-hold benchmark) still gets the curve-derived metrics; only
the trade-derived fields collapse to zero. This lets the benchmark report a
meaningful return and Sharpe, while an empty-trade strategy is reported as
"0 trades" rather than producing a misleading Sharpe.

### `profit_factor` sentinel

When there are no losing trades but at least one positive pnl, `profit_factor`
is `float("inf")`. `json.dumps(..., allow_nan=True)` (the default) serializes
this as `Infinity` — stable across runs but non-strict JSON. When there are no
numeric pnls at all, `profit_factor` is `0.0`.

### Equity curve convention

The CLI builds the per-strategy equity curve as cumulative realized P&L, one
point per bar from `warmup` to the last bar:
`equity_curve[t] = sum(pnl_quote)` for trades whose `exit_bar_index <= warmup + t`.
It starts at `0.0` (no trades exited yet); if no trades have a non-`None` pnl,
`equity_curve = [0.0]`. The benchmark curve is the cost basis followed by the
close of each bar from `warmup` onward (see `buy_and_hold_benchmark`), so its
`total_return` reflects the entry fee + slippage.

### Example

```python
records = [sim.simulate(idea, candles, b) for b in bars]
curve = [0.0, 0.0, 5.0, 5.0, 12.0]  # cumulative realized P&L per bar
metrics = bt.compute(records, curve)
bench = bt.buy_and_hold_benchmark(candles, warmup=50, fee_bps=26, slippage_bps=2)
bench_metrics = bt.compute([], bench)
print(metrics["sharpe"], metrics["max_drawdown"], bench_metrics["total_return"])
```

## Forensic drill

`--forensic-drill <BAR>` reconstructs the audit trail for one chosen bar: it
replays the strategy (the same `WalkForwardRunner` walk-forward), then runs
`FillSimulator` for that one bar, and prints three blocks:

- **decision** — the `L3Idea` fields at that bar (`pair`, `direction`,
  `conviction`, `version`, `entry_price`, `stop_loss`, the `take_profit`
  ladder, `entry_type`, `reasoning`).
- **fill** — entry + exit `FillConfirmation` summaries (`fill_price`, `qty`,
  `fee`, `slippage_paid`, `status`, `exit_reason`, `exit_bar_index`,
  `pnl_quote`).
- **risk verdict** — `{direction, entry, stop, stop_distance_pct, target,
  target_distance_pct, rr_to_tp1, conviction, version, would_vet, preliminary}`.

```bash
uv run skills/backtest-engine/scripts/run.py strategy-trend-follow DEMO 1d \
    --warmup 100 --bars 200 --demo --fill-sim --forensic-drill 150
```

With `--json` the same three blocks ride as the AXI envelope `data`
(`{"decision", "fill", "risk_verdict"}`, `count=1`).

**Risk verdict (preliminary — bt-3 risk-engine wiring is forthcoming; this
section derives a verdict from the idea's stop/TP ladder and direction only.)**
The verdict is computed locally from the `L3Idea` shape: `stop_distance_pct`
and `target_distance_pct` are the entry-to-stop / entry-to-TP1 distances as a
percentage of entry; `rr_to_tp1` is the direction-asymmetric reward-to-risk
(long `(target-entry)/(entry-stop)`, short `(entry-target)/(stop-entry)`);
`would_vet: True` and `preliminary: True` signal that the real
`analysis.risk.vet` call is not yet wired into the replay loop. When bt-3
lands, this block will be replaced by the actual `RiskVerdict` (fragments,
concerns, suggested_volume) produced by `vet(intent, ctx)`.

The drill exits cleanly with a useful message when the bar is out of range
(`< warmup` or `>= len(candles)`) or has no fired idea — both exit non-zero
(exit 2). The last bar (`bar_index == len(candles)-1`) has no next bar to fill
the entry, so the drill notes that and exits 0 (the data is valid, there is
just no next bar to fill).

## Pitfalls

The five failure modes a backtest engine can hit — look-ahead bias, intrabar
ambiguity, recompute cost, TTL cache leakage, and small-sample Sharpe — are
documented with the guard that addresses each in
[references/pitfalls.md](./references/pitfalls.md).

## Roadmap

- **bt-3** — risk-engine wiring (`risk-engine.vet` per idea; replaces the
  forensic drill's preliminary verdict with the real `RiskVerdict`).
