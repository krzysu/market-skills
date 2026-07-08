import json
import logging
import subprocess

from analysis.providers.data._retry import with_retry

logger = logging.getLogger(__name__)

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

_TRANSIENT_API_MARKERS: tuple[str, ...] = (
    "egeneral",
    "eservice",
    "einternal",
    "busy",
    "rate limit",
    "timeout",
    "temporarily unavailable",
)


def _is_kraken_api_error(result: subprocess.CompletedProcess) -> bool:
    """True if a Kraken subprocess result is a transient API-level error body.

    The Kraken CLI exits 0 even when the upstream returns an error envelope
    like ``{"error":"api","message":"EGeneral:Internal error"}``. Without
    this predicate, ``with_retry`` only catches ``subprocess.TimeoutExpired``
    and the bad result is returned on the first attempt — a 1-2 second
    blip cascades into a hard fetch failure for every caller.

    Transient markers (``egeneral``/``eservice``/``einternal``/``busy``/``rate
    limit``/``timeout``/``temporarily unavailable``) are the operator's
    shortlist of retry-worthy classes. ``EQuery:Unknown asset pair`` and
    other permanent errors don't match and propagate immediately.
    """
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict) or not data.get("error"):
        return False
    msg = (data.get("message") or "").lower()
    return any(m in msg for m in _TRANSIENT_API_MARKERS)


class KrakenProvider:
    name = "kraken"

    def __init__(self):
        self._cache: dict[str, bool] = {}

    def supports(self, ticker: str) -> bool:
        pair = ticker.replace("-", "").replace("/", "").upper()

        if pair in self._cache:
            return self._cache[pair]

        def _do():
            return subprocess.run(
                ["kraken", "pairs", "--pair", pair, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

        # Transient tuple is intentionally narrow: subprocess.TimeoutExpired only.
        # OSError is excluded because FileNotFoundError (CLI not in PATH) inherits
        # from OSError — retrying it would waste 7s before giving up.
        try:
            result = with_retry(
                _do,
                transient=(subprocess.TimeoutExpired,),
                transient_result=_is_kraken_api_error,
                label=f"kraken.pairs({pair})",
                logger=logger,
            )
        except FileNotFoundError:
            self._cache[pair] = False
            return False
        except subprocess.TimeoutExpired:
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

        def _do():
            return subprocess.run(
                ["kraken", "ticker", pair, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

        try:
            result = with_retry(
                _do,
                transient=(subprocess.TimeoutExpired,),
                transient_result=_is_kraken_api_error,
                label=f"kraken.ticker({pair})",
                logger=logger,
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

        def _do():
            return subprocess.run(args, capture_output=True, text=True, timeout=30)

        try:
            result = with_retry(
                _do,
                transient=(subprocess.TimeoutExpired,),
                transient_result=_is_kraken_api_error,
                label=f"kraken.ohlc({pair})",
                logger=logger,
            )
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
