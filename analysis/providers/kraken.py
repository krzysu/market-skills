import json
import subprocess

_INTERVAL_MAP = {
    "1d": 1440,
    "1wk": 10080,
    "1h": 60,
    "4h": 240,
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
}


class KrakenProvider:
    name = "kraken"

    def __init__(self):
        self._cache: dict[str, bool] = {}

    def supports(self, ticker: str) -> bool:
        pair = ticker.replace("-", "").replace("/", "").upper()

        if pair in self._cache:
            return self._cache[pair]

        try:
            result = subprocess.run(
                ["kraken", "pairs", "--pair", pair, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._cache[pair] = False
            return False

        if result.returncode != 0:
            self._cache[pair] = False
            return False

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self._cache[pair] = False
            return False

        if isinstance(data, dict) and "error" in data:
            self._cache[pair] = False
            return False

        self._cache[pair] = True
        return True

    def fetch_spot_price(self, ticker: str) -> dict | None:
        """Fetch live spot price via Kraken's ticker endpoint.

        Parses the same fields the Kraken REST Ticker response exposes:
          c[0] = last trade closed price
          b[0] = best bid
          a[0] = best ask

        Returns a dict with ``price``, ``last``, ``bid``, ``ask``, ``source`` or
        ``None`` if the call fails (CLI missing, timeout, bad JSON, no fields).
        """
        pair = ticker.replace("-", "").replace("/", "").upper()
        try:
            result = subprocess.run(
                ["kraken", "ticker", pair, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        if result.returncode != 0:
            return None

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

        ticker_data = None
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, dict):
                    ticker_data = v
                    break

        if ticker_data is None:
            return None

        last = None
        bid = None
        ask = None
        try:
            if ticker_data.get("c"):
                last = float(ticker_data["c"][0])
            if ticker_data.get("b"):
                bid = float(ticker_data["b"][0])
            if ticker_data.get("a"):
                ask = float(ticker_data["a"][0])
        except (IndexError, ValueError, TypeError):
            return None

        price = last if last is not None else bid
        if price is None:
            return None

        return {
            "price": price,
            "last": last,
            "bid": bid,
            "ask": ask,
            "source": "kraken:ticker",
        }

    def fetch(self, ticker: str, interval: str = "1d", period: str = "1y") -> list[list]:
        pair = ticker.replace("-", "").replace("/", "").upper()
        kraken_interval = _INTERVAL_MAP.get(interval)
        if kraken_interval is None:
            return []

        args = ["kraken", "ohlc", pair, "--interval", str(kraken_interval), "-o", "json"]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        if result.returncode != 0:
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        candles_raw = None
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], list):
                    candles_raw = v
                    break

        if not candles_raw:
            return []

        candles = []
        for c in candles_raw:
            try:
                ts = int(c[0])
                o = float(c[1])
                h = float(c[2])
                low = float(c[3])
                cl = float(c[4])
                vo = float(c[6])
                candles.append([ts, o, h, low, cl, vo])
            except (IndexError, ValueError, TypeError):
                continue
        return candles
