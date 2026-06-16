"""market-volume — Volume analysis: ratio, OBV trend, regime classification."""

from analysis.formatting import safe_round
from analysis.indicators import (
    compute_obv_trend,
    compute_sma,
    detect_obv_divergence,
    extract_ohlcv,
)


def analyze(candles, interval="1d", period="1y"):
    """Analyze volume structure from OHLC candles.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with volume indicators (context skill — no score/signal/zone)
    """
    if not candles or len(candles) < 22:
        return {"error": f"insufficient data (need 22+ candles, got {len(candles) if candles else 0})"}

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]
    current_volume = volumes[-1]

    # Volume ratio vs SMA(20)
    sma_vol = compute_sma(volumes, 20)
    volume_ratio = current_volume / sma_vol if sma_vol and sma_vol > 0 else None

    # Volume regime
    if volume_ratio is None:
        regime = None
    elif volume_ratio >= 2.5:
        regime = "CLIMAX"
    elif volume_ratio >= 1.5:
        regime = "HIGH"
    elif volume_ratio >= 0.5:
        regime = "NORMAL"
    else:
        regime = "LOW"

    # OBV trend
    obv_trend = compute_obv_trend(closes, volumes, sma_period=20)

    # OBV divergence
    obv_divergence = detect_obv_divergence(closes, volumes, swing_window=14, lookback=28)

    return {
        "current_price": safe_round(current_price, 2),
        "current_volume": safe_round(current_volume, 0),
        "sma_volume_20": safe_round(sma_vol, 0) if sma_vol else None,
        "volume_ratio": safe_round(volume_ratio, 2) if volume_ratio else None,
        "obv_trend": obv_trend,
        "obv_divergence": obv_divergence,
        "regime": regime,
    }
