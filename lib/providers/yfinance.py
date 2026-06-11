import math

import yfinance as yf


class YFinanceProvider:
    name = "yfinance"

    def supports(self, ticker: str) -> bool:
        return True

    def fetch(self, ticker: str, interval: str = "1d", period: str = "1y") -> list[list]:
        try:
            df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)
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
            o, h, l, c, v = row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
            if any(isinstance(x, float) and math.isnan(x) for x in (o, h, l, c)):
                continue
            ts = int(idx.timestamp())
            candles.append([ts, float(o), float(h), float(l), float(c), float(v)])
        return candles
