import time

import ccxt

_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
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
        "5y": 157680000,
        "max": 1576800000,
    }
    return int(time.time() * 1000) - (seconds.get(period, 31536000) * 1000)


class CCXTProvider:
    name = "ccxt"

    def __init__(self, exchange_id: str = "binance"):
        exchange_class = getattr(ccxt, exchange_id)
        self._exchange = exchange_class({"enableRateLimit": True})
        self._markets_loaded = False

    def supports(self, ticker: str) -> bool:
        if "/" not in ticker:
            return False
        try:
            if not self._markets_loaded:
                self._exchange.load_markets()
                self._markets_loaded = True
            return ticker in self._exchange.markets
        except Exception:
            return False

    def fetch_funding_rate(self, ticker: str) -> dict | None:
        try:
            rate = self._exchange.fetch_funding_rate(ticker)
            if rate:
                return {
                    "funding_rate": rate.get("fundingRate"),
                    "funding_time": rate.get("fundingTime"),
                    "next_funding_time": rate.get("nextFundingTime"),
                }
        except Exception:
            pass

        try:
            history = self._exchange.fetch_funding_rate_history(ticker, limit=30)
            if history:
                avg = sum(float(h["fundingRate"]) for h in history) / len(history)
                return {"funding_rate_avg_30": avg}
        except Exception:
            pass

        return None

    def fetch(self, ticker: str, interval: str = "1d", period: str = "1y") -> list[list]:
        ccxt_interval = _INTERVAL_MAP.get(interval)
        if ccxt_interval is None:
            return []

        since = _period_to_since_ms(period)

        try:
            ohlcv = self._exchange.fetch_ohlcv(ticker, ccxt_interval, since)
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
