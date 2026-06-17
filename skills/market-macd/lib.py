"""market-macd — MACD momentum indicator analysis."""

from analysis.formatting import safe_round
from analysis.indicators import compute_macd


def analyze(candles, interval="1d", period="1y"):
    """Analyze MACD from OHLC candles.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with MACD indicators, score, signal, zone
    """
    if not candles or len(candles) < 35:
        return {"error": f"insufficient data (need 35+ candles, got {len(candles) if candles else 0})"}

    closes = [float(c[4]) for c in candles]

    macd_line, signal_line, histogram = compute_macd(closes, fast=12, slow=26, signal=9)

    # Get the last non-None values
    def last_valid(arr):
        for v in reversed(arr):
            if v is not None:
                return v
        return None

    macd_val = last_valid(macd_line)
    sig_val = last_valid(signal_line)
    hist_val = last_valid(histogram)

    if macd_val is None or sig_val is None or hist_val is None:
        return {"error": "not enough data for reliable MACD calculation"}

    # Previous histogram bar for direction and flip detection
    prev_hist = None
    for v in reversed(histogram):
        if v is not None and v != hist_val:
            prev_hist = v
            break

    # Direction
    direction = None
    if prev_hist is not None:
        direction = "rising" if hist_val > prev_hist else "falling"

    # Histogram flip
    histogram_flip = None
    if prev_hist is not None:
        if prev_hist <= 0 < hist_val:
            histogram_flip = "negative_to_positive"
        elif prev_hist >= 0 > hist_val:
            histogram_flip = "positive_to_negative"

    # Signal classification
    if hist_val > 0:
        signal = "BULLISH"
        score = 2 if macd_val > 0 and sig_val > 0 else 1
    elif hist_val < 0:
        signal = "BEARISH"
        score = -2 if macd_val < 0 and sig_val < 0 else -1
    else:
        signal = "NEUTRAL"
        score = 0

    # Check for crossovers
    # Find last 2 pairs where both are non-None
    valid_pairs = [(m, s) for m, s in zip(macd_line, signal_line) if m is not None and s is not None]
    if len(valid_pairs) >= 2:
        prev_m, prev_s = valid_pairs[-2]
        curr_m, curr_s = valid_pairs[-1]
        if prev_m < prev_s and curr_m >= curr_s:
            signal = "BULLISH_CROSS"
            score = max(score, 1)
        elif prev_m > prev_s and curr_m <= curr_s:
            signal = "BEARISH_CROSS"
            score = min(score, -1)

    # Zone
    if macd_val > sig_val and hist_val > 0:
        zone = "bullish"
    elif macd_val < sig_val and hist_val < 0:
        zone = "bearish"
    else:
        zone = "neutral"

    return {
        "macd_line": safe_round(macd_val, 4),
        "signal_line": safe_round(sig_val, 4),
        "histogram": safe_round(hist_val, 4),
        "prev_histogram": safe_round(prev_hist, 4) if prev_hist is not None else None,
        "histogram_direction": direction,
        "histogram_flip": histogram_flip,
        "signal": signal,
        "score": score,
        "zone": zone,
    }
