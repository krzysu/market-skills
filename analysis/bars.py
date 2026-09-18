"""Bar-clock helpers: judge whether the trailing candle is closed.

All four providers (Hyperliquid, ccxt, Kraken, yfinance) return the venue's
raw series **including the current, partially-elapsed bar**. A partial bar
holds only the volume traded so far, so any volume ratio computed against it
is understated by roughly the elapsed-time fraction.

This module is the single place callers judge partiality:
:func:`analysis.data.fetch_ohlc` trims the non-closed trailing bar on read so
every consumer — live scan paths and the nightly backtest alike — sees the
same closed-bars series.
"""

from __future__ import annotations

import time

# Seconds per bar for every interval in analysis.intervals.VALID_INTERVALS.
# `1M` is the conservative upper bound (31 days): a calendar month can be
# shorter, and over-estimating the bar length can only ever keep a bar
# classified as "still forming" — never the unsafe direction of dropping a
# closed bar.
INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1wk": 604800,
    "1M": 2678400,
}


def now_epoch() -> int:
    """Current Unix time in whole seconds.

    A monkeypatchable clock seam for tests — production code should not
    reach for ``time.time()`` directly when judging bar closure.
    """
    return int(time.time())


def last_bar_is_closed(candles: list[list], interval: str, *, now: int | None = None) -> bool:
    """Return True when the trailing bar's close time has passed.

    False when the trailing bar is still forming, including future-dated
    timestamps / clock skew (a bar whose close time lies in the future is by
    definition not yet closed). An empty list is True — nothing to drop.
    """
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"unknown interval {interval!r}; expected one of {sorted(INTERVAL_SECONDS)}")
    if not candles:
        return True
    if now is None:
        now = now_epoch()
    return int(candles[-1][0]) + INTERVAL_SECONDS[interval] <= now


def closed_bars(candles: list[list], interval: str, *, now: int | None = None) -> list[list]:
    """Return the series with the trailing bar dropped iff it is not closed.

    Never drops more than one bar, never reorders, and never mutates the
    caller's list. A fully historical series is returned unchanged (element
    identity preserved). ``now`` defaults to :func:`now_epoch`.
    """
    if last_bar_is_closed(candles, interval, now=now):
        return candles
    return candles[:-1]
