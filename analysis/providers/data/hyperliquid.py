"""Hyperliquid perp data provider using the official CCXT wrapper.

Ticker convention:
  - Use ``hl:TICKER`` for explicit HL routing (e.g. ``hl:LIT``).
  - Bare name: auto-detected via ``supports()`` (checks HL perpetual markets).
"""

import logging
import time

import ccxt
from hyperliquid.ccxt.hyperliquid import hyperliquid

from analysis.providers.data._retry import with_retry

logger = logging.getLogger(__name__)

# ccxt transient errors (network glitches, request timeouts). ExchangeError
# and BadRequest are intentionally excluded — those are 4xx / business-rule
# responses, not retryable.
_CCXT_TRANSIENT: tuple[type[BaseException], ...] = (ccxt.NetworkError,)

_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "3d": "3d",
    "1wk": "1w",
    "1M": "1M",
}


def _period_to_since_ms(period: str) -> int:
    seconds = {
        "1d": 86400,
        "5d": 432000,
        "1mo": 2592000,
        "3mo": 7776000,
        "6mo": 15552000,
        "1y": 31536000,
        "2y": 63072000,
        "max": 1576800000,
    }
    return int(time.time() * 1000) - (seconds.get(period, 31536000) * 1000)


def _to_symbol(name: str) -> str:
    """Normalise a short ticker name to HL perpetual symbol (e.g. LIT → LIT/USDC:USDC)."""
    clean = name.replace("/", "").replace("-", "").upper()
    for suffix in ("USD", "USDC", "USDT"):
        if clean.endswith(suffix) and clean != suffix:
            clean = clean[: -len(suffix)]
    return f"{clean}/USDC:USDC"


class HyperliquidProvider:
    name = "hyperliquid"

    def __init__(self):
        self._exchange = hyperliquid({"enableRateLimit": True})
        self._markets_loaded = False

    def _ensure_markets(self):
        if not self._markets_loaded:
            swap_markets = self._exchange.fetch_swap_markets()
            self._exchange.markets = {m["symbol"]: m for m in swap_markets}
            self._exchange.markets_by_id = {str(m["id"]): m for m in swap_markets}
            self._exchange.symbols = [m["symbol"] for m in swap_markets]
            self._markets_loaded = True

    def supports(self, ticker: str) -> bool:
        try:
            self._ensure_markets()
            return _to_symbol(ticker) in self._exchange.markets
        except Exception:
            return False

    def fetch(self, ticker: str, interval: str = "1d", period: str = "1y") -> list[list]:
        hl_interval = _INTERVAL_MAP.get(interval, "1d")
        since = _period_to_since_ms(period)
        symbol = _to_symbol(ticker)

        def _do():
            self._ensure_markets()
            return self._exchange.fetch_ohlcv(symbol, hl_interval, since)

        try:
            ohlcv = with_retry(
                _do,
                transient=_CCXT_TRANSIENT,
                label=f"hyperliquid.ohlcv({symbol})",
                logger=logger,
            )
        except Exception:
            return []

        candles = []
        for c in ohlcv:
            try:
                ts = int(c[0] // 1000)
                o = float(c[1])
                h = float(c[2])
                low = float(c[3])
                cl = float(c[4])
                v = float(c[5])
                candles.append([ts, o, h, low, cl, v])
            except (IndexError, ValueError, TypeError):
                continue
        return candles

    def fetch_funding_rate(self, ticker: str) -> dict | None:
        symbol = _to_symbol(ticker)

        def _do():
            self._ensure_markets()
            return self._exchange.fetch_funding_rate(symbol)

        try:
            rate = with_retry(
                _do,
                transient=_CCXT_TRANSIENT,
                label=f"hyperliquid.funding_rate({symbol})",
                logger=logger,
            )
            if rate:
                return {
                    "funding_rate": rate.get("fundingRate"),
                    "funding_time": rate.get("fundingTime"),
                    "next_funding_time": rate.get("nextFundingTime"),
                }
        except Exception:
            pass

        def _history():
            self._ensure_markets()
            return self._exchange.fetch_funding_rate_history(symbol, limit=30)

        try:
            history = with_retry(
                _history,
                transient=_CCXT_TRANSIENT,
                label=f"hyperliquid.funding_rate_history({symbol})",
                logger=logger,
            )
            if history:
                avg = sum(float(h["fundingRate"]) for h in history) / len(history)
                return {"funding_rate_avg_30": avg}
        except Exception:
            pass

        return None
