# 0007. As-of-last-closed-bar for OHLC consumers

- **Status**: accepted
- **Date**: 2026-09-18

## Context

Every provider returns the venue's raw series **including the current,
partially-elapsed bar**, and `fetch_ohlc` passed it straight through. A
partial bar holds only the volume traded so far, so every volume ratio
computed against it is understated by roughly the elapsed-time fraction.
Measured live on `hl:HYPE` (4h): the in-progress bar (opened 2.00h ago,
~50% elapsed) held 1,287,385 volume against a 20-bar closed-bar average of
1,171,267 — a computed ratio of 1.10x where the projected full-bar value
was 2.20x.

The bias is two-sided and hit two surfaces the same morning: at 08:42
Swing-Scan declined HYPE ("volume 0.37x SMA20 with bearish OBV divergence —
breakout unconfirmed"; the bar was ~17% elapsed, which alone explains
0.37x), while at 10:08 Morning Brief — on completed bars — measured
volume 1.8–2.4x and called HYPE LONG conviction 4. False negatives early
in a bar (volume looks thin); false positives late in a bar when a genuine
spike is smeared against itself. Scope: every volume-based L1/L2 signal
(volume ratio, OBV, volume-confirmed breakout, accumulation/distribution)
on every interval, for every consumer (Swing-Scan, Daily Trade Pick, the
Watchlist Monitor, the Signal Action loop).

Provider behaviour was probed directly on 2026-09-18 (kraken:BTCUSD 4h,
ccxt/binance BTC/USDT 4h, yfinance:SPY 1d, hyperliquid:HYPE 4h): all four
return the raw venue series including the non-closed trailing bar.

## Decision

Exclude non-closed bars from all volume computations at the single shared
source — the data layer — so the live scan path and the backtest path see
identical series. No per-provider special-casing, no per-strategy
compensation, and no normalising by elapsed fraction.

- New `analysis/bars.py`: `INTERVAL_SECONDS` (seconds per bar for every
  interval in `analysis.intervals.VALID_INTERVALS`), `now_epoch()` (a
  monkeypatchable clock seam), `last_bar_is_closed()`, and
  `closed_bars()` (drops at most the one trailing bar iff not closed;
  never mutates the caller's list). An unknown interval raises
  `ValueError`. `1M` uses a conservative 31-day bound: a calendar month
  can be shorter, and over-estimating the bar length can only ever keep a
  bar classified as "still forming" — never the unsafe direction.
- `analysis.data.fetch_ohlc` gains `include_partial: bool = False`. The
  default returns `closed_bars(raw, interval)`, applied to **both** the
  cache-hit path and the live path, uniform across `hl:` / `kraken:` /
  `yf:` / `ccxt` and identical for auto-detected tickers.
  `include_partial=True` returns the raw provider series (venue contract
  unchanged).
- The disk cache keeps storing the **raw** provider payload; the trim is
  applied on read, never stored. A bar that closes while an entry is
  cached is correctly admitted on the next read; a partial bar is never
  frozen in as "closed".
- `fetch_spot_price` is untouched — it is the live-price path.
- Callers that genuinely need the forming bar opt in explicitly with
  `include_partial=True`; everything else takes the default (last closed
  bar, as of a fixed read of the series). This is a **class rule, not a
  fixed list** — any consumer that needs the last traded price rather than
  the last closed-bar close opts in at its own call site. The opt-in sites
  as of this change:
  `position-watchdog._current_price()`, `market-overview/scripts/run.py`,
  `market-snapshot/scripts/run.py`, and `portfolio/db/prices.py`.
  `_run_strategies()` in `position-watchdog` keeps the default, since
  strategy evaluation must not read a partial bar.

## Consequences

- (+) Live scan and nightly backtest now consume identical series — the
  08:42/10:08 contradiction class is closed at the source.
- (+) Every volume-based L1/L2 signal (volume ratio, OBV,
  volume-confirmed breakout, accumulation/distribution) reads completed
  bars only, on every interval and every provider.
- (-) Price-sensitive callers (watchdogs, live-price proxies) must use
  `fetch_spot_price` or opt into `include_partial=True` explicitly.
- (-) `1M` bars may be admitted up to ~a day late near month boundaries
  because 31 days over-estimates some months — conservative by design.
