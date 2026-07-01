"""Shared interval + period constants and validation.

Single source of truth used by `analysis.formatting.parse_args` and
`analysis.data.fetch_ohlc` so the entire CLI surface (per-skill scripts,
run-all-l2/l3, run-watchlist, market-basis, position-watchdog) speaks the
same vocabulary.

Why centralise this: providers each have a different interval map
(Kraken omits 1M/2h/8h; yfinance passes through; CCXT/Hyperliquid have their
own `_INTERVAL_MAP`). Defining the supported superset here lets us reject
typos early with a clear error instead of silently returning no data.

Supported intervals (union across providers):
    1m, 2m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 3d, 1wk, 1M

Supported periods (yfinance-style strings):
    1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
"""

from __future__ import annotations

VALID_INTERVALS: frozenset[str] = frozenset(
    {"1m", "2m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1wk", "1M"}
)

VALID_PERIODS: frozenset[str] = frozenset({"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"})

DEFAULT_INTERVAL: str = "1d"
DEFAULT_PERIOD: str = "1y"

# Rough upper bound on candle count for each (interval, period) combo that
# providers like yfinance can actually return. Used to emit a stderr warning
# (not a hard error) when callers ask for combinations that will likely
# produce truncated data. The rough math: minutes_per_candle * period_seconds/60.
_MINUTE_INTERVALS = {"1m", "2m", "5m", "15m", "30m"}
_HOUR_INTERVALS = {"1h", "2h", "4h", "8h", "12h"}
# Intervals that have no yfinance lookback cap. Used as a fast path in
# `warn_unsupported_combo` to short-circuit the check before we touch
# `_PERIOD_SECONDS`. 1d/3d/1wk/1M all fall in this bucket.
_UNBOUNDED_INTERVALS = frozenset({"1d", "3d", "1wk", "1M"})

# yfinance docs: intraday data (anything <1d) is limited to ~60 days,
# 1h is limited to ~730 days. Periods above those produce truncated or empty data.
# We store the limits in seconds directly since "60d" isn't a valid yfinance period.
_YF_INTRADAY_MAX_SECONDS = 60 * 86400  # ~60 days
_YF_HOURLY_MAX_SECONDS = 2 * 31536000  # ~2 years

# Providers whose interval/period caps match the yfinance model above. When
# the warning fires, we know the cap is real. For other providers (Kraken,
# Hyperliquid, CCXT) the actual limits differ and we stay silent rather
# than suggest a cap that may not apply. Callers in analysis.data always
# pass the canonical registry name (`yfinance`), so no aliasing is needed
# here.
_YFINANCE_PROVIDER_NAMES = frozenset({"yfinance"})

_PERIOD_SECONDS = {
    "1d": 86400,
    "5d": 432000,
    "1mo": 2592000,
    "3mo": 7776000,
    "6mo": 15552000,
    "1y": 31536000,
    "2y": 63072000,
    "5y": 157680000,
    "10y": 315360000,
    "ytd": 31536000,  # approximate; yfinance handles it server-side
    "max": 1576800000,  # 50y approximation
}


def validate_timeframe(interval: str, period: str) -> None:
    """Raise ``ValueError`` if ``interval`` or ``period`` is not in the supported set.

    Catches typos like ``--interval=1hr`` (should be ``1h``) or ``--period=12m``
    (should be ``1y``). Callers should wrap with a friendly message at the CLI
    boundary; library callers can let the raw ValueError propagate.
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(f"invalid interval {interval!r}; expected one of {sorted(VALID_INTERVALS)}")
    if period not in VALID_PERIODS:
        raise ValueError(f"invalid period {period!r}; expected one of {sorted(VALID_PERIODS)}")


def warn_unsupported_combo(interval: str, period: str, provider: str | None = None) -> str | None:
    """Return a stderr-friendly warning string for known-limited combos, or None.

    For (1m..30m, period>60d) yfinance will truncate; for (1h..12h, period>2y)
    same. Doesn't raise — we want the caller to still get whatever data the
    provider can supply, just with a heads-up.

    Pass ``provider`` (e.g. ``"yfinance"``, ``"kraken"``, ``"hl"``) to make
    the warning provider-aware. Non-yfinance providers have different caps,
    so the warning stays silent rather than suggest a limit that may not
    apply. Defaults to silent when ``provider`` is None (caller didn't
    indicate the provider yet).
    """
    if provider is not None and provider not in _YFINANCE_PROVIDER_NAMES:
        return None
    if interval in _UNBOUNDED_INTERVALS:
        return None
    if interval in _MINUTE_INTERVALS and period in _PERIOD_SECONDS:
        if _PERIOD_SECONDS[period] > _YF_INTRADAY_MAX_SECONDS:
            return (
                f"interval={interval!r} with period={period!r} likely exceeds "
                f"yfinance's ~60d intraday limit; candles may be truncated"
            )
    if interval in _HOUR_INTERVALS and period in _PERIOD_SECONDS:
        if _PERIOD_SECONDS[period] > _YF_HOURLY_MAX_SECONDS:
            return (
                f"interval={interval!r} with period={period!r} likely exceeds "
                f"yfinance's ~2y hourly limit; candles may be truncated"
            )
    return None
