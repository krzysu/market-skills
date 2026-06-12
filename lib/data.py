"""Unified data-fetching layer with pluggable providers."""

from lib.providers.base import Provider
from lib.providers.ccxt import CCXTProvider
from lib.providers.kraken import KrakenProvider
from lib.providers.yfinance import YFinanceProvider

_REGISTRY: list[Provider] = [
    KrakenProvider(),
    YFinanceProvider(),
    CCXTProvider("binance"),
]

_CCXT_CACHE: dict[str, CCXTProvider] = {}


def _get_provider(source: str) -> Provider:
    if source.startswith("ccxt"):
        parts = source.split(":", 1)
        exchange_id = parts[1] if len(parts) > 1 else None

        for p in _REGISTRY:
            if p.name == "ccxt":
                if exchange_id:
                    if exchange_id not in _CCXT_CACHE:
                        _CCXT_CACHE[exchange_id] = CCXTProvider(exchange_id=exchange_id)
                    return _CCXT_CACHE[exchange_id]
                return p

    for p in _REGISTRY:
        if p.name == source:
            return p
    raise ValueError(f"Unknown provider: {source}")


def fetch_funding_rate(ticker: str, source: str | None = None) -> dict | None:
    """Fetch current funding rate for a perpetual swap ticker.

    Returns a dict with funding rate info, or None if unavailable.
    Only CCXT-based providers support this.
    """
    if source:
        try:
            p = _get_provider(source)
            if hasattr(p, "fetch_funding_rate"):
                return p.fetch_funding_rate(ticker)
        except Exception:
            return None
        return None

    for p in _REGISTRY:
        if hasattr(p, "fetch_funding_rate") and p.name != "ccxt":
            try:
                if p.supports(ticker):
                    result = p.fetch_funding_rate(ticker)
                    if result:
                        return result
            except Exception:
                continue

    # Try CCXT providers last — check markets before attempting
    for p in _REGISTRY:
        if hasattr(p, "fetch_funding_rate") and p.name == "ccxt":
            try:
                if p.supports(ticker):
                    result = p.fetch_funding_rate(ticker)
                    if result:
                        return result
            except Exception:
                continue

    return None


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
