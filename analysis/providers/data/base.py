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

    def fetch_funding_rate(self, ticker: str) -> dict | None:
        """Fetch current funding rate for a perpetual swap ticker.

        Returns a dict with keys like 'fundingRate', 'fundingTime',
        'nextFundingTime', or None if not applicable.
        """
        return None

    def fetch_spot_price(self, ticker: str) -> dict | None:
        """Fetch live spot price for a ticker.

        Returns a dict with keys ``price``, ``last``, ``bid``, ``ask``, ``source``
        or ``None`` if the provider cannot serve a live spot quote. Default
        implementation returns ``None``; providers with a public spot endpoint
        should override.
        """
        return None
