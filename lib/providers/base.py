from typing import Protocol


class Provider(Protocol):
    name: str

    def supports(self, ticker: str) -> bool:
        """Return True if this provider can serve the given ticker."""
        ...

    def fetch(self, ticker: str, interval: str, period: str) -> list[list]:
        """Fetch OHLC candles.

        Returns a list of candles: [[timestamp, open, high, low, close, volume], ...]
        Timestamps are Unix seconds (int). Returns [] on failure.
        """
        ...
