"""Data fetching via yfinance — free, no API key required."""

import yfinance as yf


def fetch_ohlc(ticker, interval="1d", period="1y"):
    """Fetch OHLC candles for a ticker via Yahoo Finance.

    Args:
        ticker: yfinance ticker symbol (e.g. "AAPL", "BTC-USD", "SPY")
        interval: candle interval — "1d", "1wk", "1h", etc.
        period: how far back to fetch — "1y", "6mo", "2y", "max"

    Returns:
        List of candles: [[timestamp, open, high, low, close, volume], ...]
        Timestamps are Unix seconds (int).
        Returns [] if the ticker has no data.
    """
    try:
        df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)
    except Exception:
        return []

    if df.empty:
        return []

    # yfinance returns MultiIndex columns like ('Close', 'SPY') — flatten
    if hasattr(df.columns, "get_level_values"):
        df.columns = df.columns.get_level_values(0)

    if "Open" not in df.columns:
        return []

    import math

    candles = []
    for idx, row in df.iterrows():
        o, h, l, c, v = row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
        if any(isinstance(x, float) and math.isnan(x) for x in (o, h, l, c)):
            continue
        ts = int(idx.timestamp())
        candles.append([
            ts,
            float(o),
            float(h),
            float(l),
            float(c),
            float(v),
        ])
    return candles
