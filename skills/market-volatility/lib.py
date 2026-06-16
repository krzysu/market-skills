"""market-volatility — Realized volatility analysis, percentile rank, regime."""

from analysis.formatting import safe_round
from analysis.indicators import log_returns, percentile_rank, realized_vol


def analyze(candles, interval="1d", period="1y"):
    """Analyze volatility from OHLC candles.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with volatility indicators (context skill — no score/signal/zone)
    """
    if not candles or len(candles) < 8:
        return {"error": f"insufficient data (need 8+ candles, got {len(candles) if candles else 0})"}

    closes = [float(c[4]) for c in candles]

    # Log returns
    returns = log_returns(closes)

    # Realized volatility — 7d and 30d
    realized_7d = realized_vol(returns, min(7, len(returns)))
    realized_30d = realized_vol(returns, min(30, len(returns))) if len(returns) >= 30 else None

    # Percentile rank of 30d vol in the full history
    pct_rank = None
    if realized_30d is not None:
        # Compute rolling 30d vols for history
        rolling_30d = []
        for i in range(30, len(returns) + 1):
            rv = realized_vol(returns[:i], 30)
            if rv is not None:
                rolling_30d.append(rv)
        if rolling_30d:
            pct_rank = percentile_rank(realized_30d, rolling_30d)

    # Volatility regime
    if pct_rank is None:
        regime = None
    elif pct_rank >= 90:
        regime = "EXTREME"
    elif pct_rank >= 75:
        regime = "HIGH"
    elif pct_rank >= 25:
        regime = "NORMAL"
    else:
        regime = "LOW"

    # Volatility trend
    trend = None
    if realized_7d is not None and realized_30d is not None:
        if realized_7d > realized_30d * 1.2:
            trend = "spiking"
        elif realized_30d > realized_7d * 1.2:
            trend = "compressing"
        else:
            trend = "stable"
    elif realized_7d is not None:
        trend = "spiking"  # only short-term available, assume recent direction

    return {
        "realized_vol_7d": safe_round(realized_7d, 2) if realized_7d else None,
        "realized_vol_30d": safe_round(realized_30d, 2) if realized_30d else None,
        "percentile_rank_30d": safe_round(pct_rank, 1) if pct_rank is not None else None,
        "regime": regime,
        "trend": trend,
    }
