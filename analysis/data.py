"""Unified data-fetching layer with pluggable providers and explicit routing.

Ticker format:
  - `provider:ticker` — explicit provider routing (e.g. `hl:LIT`, `kraken:BTC-USD`)
  - `ticker` — auto-detect based on provider capability

Provider prefixes:
  - `hl:` — Hyperliquid (strip prefix, pass bare coin name)
  - `kraken:` — Kraken spot (strip prefix, pass pair name)
  - `yf:` — YFinance (strip prefix, pass ticker symbol)
  - `yfinance:` — same as `yf:`
"""

import logging

from analysis.intervals import validate_timeframe, warn_unsupported_combo
from analysis.providers.data.base import Provider
from analysis.providers.data.ccxt import CCXTProvider
from analysis.providers.data.hyperliquid import HyperliquidProvider
from analysis.providers.data.kraken import KrakenProvider
from analysis.providers.data.yfinance import YFinanceProvider

logger = logging.getLogger(__name__)

_REGISTRY: list[Provider] = [
    HyperliquidProvider(),
    CCXTProvider("binance"),
    KrakenProvider(),
    YFinanceProvider(),
]

_CCXT_CACHE: dict[str, CCXTProvider] = {}

# Short prefix → provider name mapping for explicit `provider:ticker` routing
_PREFIX_MAP: dict[str, str] = {
    "hl": "hyperliquid",
    "kraken": "kraken",
    "yf": "yfinance",
    "yfinance": "yfinance",
}


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
    raise ValueError(f"Unknown provider: {source!r}")


def _resolve_ticker_prefix(ticker: str) -> tuple[str, str] | None:
    """Check if ticker uses `provider:ticker` format and return (resolved_ticker, provider_name).

    Returns None if no known prefix is found.
    """
    if ":" not in ticker:
        return None
    parts = ticker.split(":", 1)
    prefix = parts[0].lower()
    provider_name = _PREFIX_MAP.get(prefix)
    if provider_name is None:
        return None
    return (parts[1], provider_name)


def fetch_funding_rate(ticker: str, source: str | None = None) -> dict | None:
    """Fetch current funding rate for a perpetual swap ticker.

    Returns a dict with funding rate info, or None if unavailable.
    Only CCXT-based providers support this. Use the ``source`` argument
    (e.g. ``source="ccxt:binance"``) to pick an exchange explicitly;
    ticker-prefix routing is not supported for funding rates.
    """
    if source:
        try:
            p = _get_provider(source)
            if hasattr(p, "fetch_funding_rate"):
                return p.fetch_funding_rate(ticker)
        except Exception as e:
            logger.warning("fetch_funding_rate(source=%s): %s", source, e)
            return None
        return None

    for p in _REGISTRY:
        if hasattr(p, "fetch_funding_rate") and p.name != "ccxt":
            try:
                if p.supports(ticker):
                    result = p.fetch_funding_rate(ticker)
                    if result:
                        return result
            except Exception as e:
                logger.debug("fetch_funding_rate(auto, %s=%s): %s", p.name, ticker, e)
                continue

    for p in _REGISTRY:
        if hasattr(p, "fetch_funding_rate") and p.name == "ccxt":
            try:
                if p.supports(ticker):
                    result = p.fetch_funding_rate(ticker)
                    if result:
                        return result
            except Exception as e:
                logger.debug("fetch_funding_rate(auto, %s=%s): %s", p.name, ticker, e)
                continue

    return None


def _resolve_explicit(ticker: str, interval: str, period: str) -> tuple[list[list], str] | None:
    """Check if ticker uses ``provider:ticker`` format and route explicitly.

    Returns ``(candles, provider_name)`` on prefix match — including on fetch
    failure (``candles=[]``) so the caller can still log the yfinance cap
    warning. Returns ``None`` when no known prefix is present.
    """
    resolved = _resolve_ticker_prefix(ticker)
    if resolved is None:
        return None
    raw_ticker, provider_name = resolved
    try:
        candles = _get_provider(provider_name).fetch(raw_ticker, interval, period)
    except Exception as e:
        logger.warning("_resolve_explicit(%s): %s", ticker, e)
        return [], provider_name
    return candles, provider_name


def fetch_spot_price(ticker: str, source: str | None = None) -> dict | None:
    """Fetch live spot price for a ticker.

    Routes through the same explicit ``provider:ticker`` resolution and
    auto-detection as :func:`fetch_ohlc`, but asks each provider for a live
    spot quote (Kraken ``ticker``, ccxt ``fetch_ticker``, etc.) instead of an
    OHLC candle. Returns ``None`` if no provider can serve a live price; the
    caller is expected to fall back to a stale OHLC close if it needs *some*
    number.

    Args:
        ticker: Ticker symbol. Supports ``provider:ticker`` format.
        source: Provider name override, or None for auto-detect.

    Returns:
        ``{"price": float, "last": float|None, "bid": float|None, "ask": float|None, "source": str}``
        or ``None`` on failure.
    """
    explicit = _resolve_ticker_prefix(ticker)
    if explicit is not None:
        raw_ticker, provider_name = explicit
        try:
            provider = _get_provider(provider_name)
            return provider.fetch_spot_price(raw_ticker)
        except Exception as e:
            logger.debug("fetch_spot_price(%s): %s", ticker, e)
            return None

    if source:
        try:
            provider = _get_provider(source)
            return provider.fetch_spot_price(ticker)
        except Exception as e:
            logger.debug("fetch_spot_price(source=%s, %s): %s", source, ticker, e)
            return None

    for p in _REGISTRY:
        if not hasattr(p, "fetch_spot_price"):
            continue
        try:
            if p.supports(ticker):
                result = p.fetch_spot_price(ticker)
                if result:
                    return result
        except Exception as e:
            logger.debug("fetch_spot_price(auto, %s=%s): %s", p.name, ticker, e)
            continue

    return None


def fetch_ohlc(ticker: str, interval: str = "1d", period: str = "1y", source: str | None = None) -> list[list]:
    """Fetch OHLC candles for a ticker.

    Args:
        ticker: Ticker symbol. Supports `provider:ticker` format (e.g. `hl:LIT`).
        interval: Candle interval — "1d", "1wk", "1h", etc.
        period: How far back — "1y", "6mo", "2y", "max".
        source: Provider name override, or None for auto-detect.

    Returns:
        List of candles: [[timestamp, open, high, low, close, volume], ...]
        Timestamps are Unix seconds (int). Returns [] on failure.

    Raises:
        ValueError: if ``interval`` or ``period`` is not in the supported set
            (see ``analysis.intervals``).
        YFinanceIncompatibleTimeframeError: if the resolved provider is
            yfinance and the (interval, period) combo can't be served.
            yfinance would otherwise 404 with a misleading "symbol may
            be delisted" message because it interprets unknown tokens
            like ``4h`` as ticker symbols.
    """
    validate_timeframe(interval, period)

    # Try explicit `provider:ticker` routing first
    explicit = _resolve_ticker_prefix(ticker)
    if explicit is not None:
        raw_ticker, provider_name = explicit
        return _fetch_from_provider(provider_name, raw_ticker, interval, period)

    # Legacy source argument (used by some scripts)
    if source:
        return _fetch_from_provider(source, ticker, interval, period)

    # Auto-detect: try each provider in registry order
    for p in _REGISTRY:
        if p.supports(ticker):
            try:
                return _fetch_from_provider(p.name, ticker, interval, period)
            except Exception as e:
                logger.debug("fetch_ohlc(auto, %s=%s): %s", p.name, ticker, e)
                continue

    return []


def _fetch_from_provider(provider_name: str, raw_ticker: str, interval: str, period: str) -> list[list]:
    """Dispatch to one provider and emit the yfinance cap warning on success.

    Raises :class:`YFinanceIncompatibleTimeframe` from yfinance's own
    boundary when the requested combo isn't serveable. Other providers
    return ``[]`` on any error (timeout, no data, etc.) — the existing
    contract.
    """
    try:
        provider = _get_provider(provider_name)
    except Exception as e:
        logger.warning("_fetch_from_provider(%s): unknown provider: %s", provider_name, e)
        return []
    try:
        candles = provider.fetch(raw_ticker, interval, period)
    except Exception as e:
        if provider_name == "yfinance":
            # Surface the incompatibility to the caller — yfinance swallows
            # bad (interval, period) combos into a misleading 404.
            logger.warning("fetch_ohlc(yfinance, %s, %s/%s): %s", raw_ticker, interval, period, e)
        else:
            logger.debug("fetch_ohlc(%s, %s, %s/%s): %s", provider_name, raw_ticker, interval, period, e)
        return []
    _log_provider_warn(provider_name, interval, period)
    return candles


def _log_provider_warn(provider_name: str, interval: str, period: str) -> None:
    """Emit yfinance-specific cap warnings only when the resolved provider
    actually uses those caps. Keeps the log noise-free for Kraken/CCXT/HL."""
    warn = warn_unsupported_combo(interval, period, provider=provider_name)
    if warn:
        logger.warning(warn)
