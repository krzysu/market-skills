import logging
import math

import yfinance as yf

from analysis.providers.data._retry import TRANSIENT_NETWORK, with_retry

logger = logging.getLogger(__name__)


class YFinanceIncompatibleTimeframeError(ValueError):
    """Raised when ``interval`` x ``period`` cannot be served by yfinance.

    yfinance silently interprets unknown tokens as ticker symbols, so a
    naive ``yf.download("TICKER", interval="4h", period="1y")`` will
    issue a real symbol lookup for ``4H`` and return a 404 / "No data
    found, symbol may be delisted" error. Raising early surfaces the
    actual incompatibility instead of the misleading downstream yfinance
    error.
    """


# yfinance's per-interval period caps. Anything outside the cap will
# either return no data, raise silently, or — for unrecognised tokens —
# be interpreted as a ticker symbol. Both failure modes are confusing;
# reject upfront.
_YFINANCE_INTERVAL_MAX_PERIOD = {
    "1m": "5d",
    "2m": "5d",
    "5m": "5d",
    "15m": "5d",
    "30m": "5d",
    "1h": "1mo",
    "2h": "1mo",
    "4h": "1mo",
    "1d": "10y",
    "5d": "10y",
    "1wk": "10y",
    "3d": "10y",
    "1M": "10y",
}

_PERIOD_RANK = {
    "1d": 1,
    "5d": 2,
    "1w": 3,
    "2w": 4,
    "3w": 5,
    "4w": 6,
    "1mo": 7,
    "3mo": 8,
    "6mo": 9,
    "1y": 10,
    "2y": 11,
    "5y": 12,
    "10y": 13,
    "ytd": 14,
    "max": 15,
}


def _validate_yfinance_combo(interval: str, period: str) -> None:
    """Raise :class:`YFinanceIncompatibleTimeframeError` if yfinance can't serve the pair.

    Reference: yfinance docs (intraday data limited to ~60 days, 1h limited
    to ~730 days, etc.). Emitting the incompatibility here means callers
    can route around it (e.g. ``hl:LIT`` instead of ``yf:LIT`` for 4h
    candles) instead of getting a cryptic 404 downstream.
    """
    max_period = _YFINANCE_INTERVAL_MAX_PERIOD.get(interval)
    if max_period is None:
        raise YFinanceIncompatibleTimeframeError(
            f"yfinance provider does not support interval={interval!r} — "
            f"known intervals: {sorted(_YFINANCE_INTERVAL_MAX_PERIOD)}"
        )
    # yfinance serves ('1d', 'max') directly — bypass the rank gate
    if interval == "1d" and period == "max":
        return
    requested_rank = _PERIOD_RANK.get(period)
    if requested_rank is None:
        return  # unknown period — let the validator upstream flag it
    max_rank = _PERIOD_RANK.get(max_period, 0)
    if requested_rank > max_rank:
        raise YFinanceIncompatibleTimeframeError(
            f"yfinance provider cannot serve (interval={interval!r}, period={period!r}) "
            f"— max supported period for {interval!r} is {max_period!r}. "
            f"Route around by using `hl:<ticker>` or `kraken:<ticker>` for non-1d intraday data."
        )


class YFinanceProvider:
    name = "yfinance"

    def supports(self, ticker: str) -> bool:
        return True

    def fetch(self, ticker: str, interval: str = "1d", period: str = "1y") -> list[list]:
        _validate_yfinance_combo(interval, period)

        def _do():
            return yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)

        try:
            df = with_retry(
                _do,
                transient=TRANSIENT_NETWORK,
                label=f"yfinance.download({ticker})",
                logger=logger,
            )
        except Exception:
            return []

        if df.empty:
            return []

        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)

        if "Open" not in df.columns:
            return []

        candles = []
        for idx, row in df.iterrows():
            o, h, low, c, v = row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
            if any(isinstance(x, float) and math.isnan(x) for x in (o, h, low, c)):
                continue
            ts = int(idx.timestamp())
            candles.append([ts, float(o), float(h), float(low), float(c), float(v)])
        return candles

    def fetch_spot_price(self, ticker: str) -> dict | None:
        """Fetch latest price via yfinance ``fast_info``.

        Returns the same shape as Kraken/HL providers so callers reading
        ``analysis.data.fetch_spot_price`` get a uniform contract. Uses
        ``fast_info`` (not ``Ticker.history``) to avoid pulling the full
        OHLC table for a single tick. Returns ``None`` on failure.
        """
        try:
            info = yf.Ticker(ticker).fast_info
        except Exception:
            return None
        raw = getattr(info, "last_price", None)
        if raw is None:
            return None
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return None
        if math.isnan(price) or price <= 0:
            return None
        prev_close = getattr(info, "previous_close", None)
        try:
            prev_f = float(prev_close) if prev_close is not None else None
        except (TypeError, ValueError):
            prev_f = None
        return {
            "price": price,
            "last": price,
            "bid": None,
            "ask": None,
            "previous_close": prev_f,
            "source": "yfinance:fast_info",
        }
