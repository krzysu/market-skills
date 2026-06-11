"""Unified data-fetching layer with pluggable providers."""

from lib.providers.base import Provider
from lib.providers.kraken import KrakenProvider
from lib.providers.yfinance import YFinanceProvider

_REGISTRY: list[Provider] = [
    KrakenProvider(),
    YFinanceProvider(),
]


def _get_provider(source: str) -> Provider:
    for p in _REGISTRY:
        if p.name == source:
            return p
    raise ValueError(f"Unknown provider: {source}")


def fetch_ohlc(ticker: str, interval: str = "1d", period: str = "1y",
               source: str | None = None) -> list[list]:
    """Fetch OHLC candles for a ticker.

    Args:
        ticker: Ticker symbol (e.g. "AAPL", "BTC-USD", "SPY").
        interval: Candle interval — "1d", "1wk", "1h", etc.
        period: How far back — "1y", "6mo", "2y", "max".
        source: Provider name ("kraken", "yfinance") or None for auto-detect.

    Returns:
        List of candles: [[timestamp, open, high, low, close, volume], ...]
        Timestamps are Unix seconds (int). Returns [] on failure.
    """
    if source:
        try:
            return _get_provider(source).fetch(ticker, interval, period)
        except Exception:
            return []

    for p in _REGISTRY:
        if p.supports(ticker):
            try:
                return p.fetch(ticker, interval, period)
            except Exception:
                continue

    return []
