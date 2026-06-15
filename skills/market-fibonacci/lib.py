"""market-fibonacci — Fibonacci retracement and extension levels."""

from lib.formatting import safe_round
from lib.indicators import compute_fib_levels, extract_ohlcv, find_swing_high, find_swing_low


def analyze(candles, interval="1d", period="1y"):
    """Analyze Fibonacci levels from OHLC candles.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with Fibonacci levels (context skill — no score/signal/zone)
    """
    if not candles or len(candles) < 25:
        return {"error": f"insufficient data (need 25+ candles, got {len(candles) if candles else 0})"}

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]

    # Find most recent swing high and low
    swing_high_price, swing_high_idx = find_swing_high(candles, window=5)
    swing_low_price, swing_low_idx = find_swing_low(candles, window=5)

    if swing_high_price is None or swing_low_price is None:
        return {"error": "could not identify swing points"}

    # Ensure swing high is above swing low (most recent swing range)
    if swing_high_price < swing_low_price:
        swing_high_price, swing_low_price = swing_low_price, swing_high_price

    # Compute Fibonacci levels (retracements + extensions)
    fib_levels = compute_fib_levels(
        swing_low_price,
        swing_high_price,
        fib_levels=[0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0],
        fib_extensions=[1.272, 1.618],
    )

    # Current position
    if current_price >= swing_high_price * 1.01:
        current_position = "above_swing_high"
    elif current_price <= swing_low_price * 0.99:
        current_position = "below_swing_low"
    else:
        current_position = "inside_swing"

    # Find nearest support and resistance from fib levels
    fib_values = []
    for key, val in fib_levels.items():
        fib_values.append((float(key), val))

    support_levels = [(k, v) for k, v in fib_values if v < current_price]
    resistance_levels = [(k, v) for k, v in fib_values if v > current_price]

    nearest_support = max(support_levels, key=lambda x: x[1]) if support_levels else None
    nearest_resistance = min(resistance_levels, key=lambda x: x[1]) if resistance_levels else None

    nearest_fib_support = nearest_support[1] if nearest_support else None
    nearest_fib_resistance = nearest_resistance[1] if nearest_resistance else None

    nearest_fib_distance_pct = None
    if nearest_fib_support is not None and nearest_fib_resistance is not None:
        dist_to_support = ((current_price - nearest_fib_support) / current_price) * 100
        dist_to_resistance = ((nearest_fib_resistance - current_price) / current_price) * 100
        nearest_fib_distance_pct = safe_round(min(dist_to_support, dist_to_resistance), 2)
    elif nearest_fib_support is not None:
        nearest_fib_distance_pct = safe_round(((current_price - nearest_fib_support) / current_price) * 100, 2)
    elif nearest_fib_resistance is not None:
        nearest_fib_distance_pct = safe_round(((nearest_fib_resistance - current_price) / current_price) * 100, 2)

    return {
        "swing_high": safe_round(swing_high_price, 2),
        "swing_low": safe_round(swing_low_price, 2),
        "current_position": current_position,
        "fib_levels": {k: safe_round(v, 2) for k, v in fib_levels.items()},
        "nearest_fib_support": safe_round(nearest_fib_support, 2) if nearest_fib_support else None,
        "nearest_fib_resistance": safe_round(nearest_fib_resistance, 2) if nearest_fib_resistance else None,
        "nearest_fib_distance_pct": nearest_fib_distance_pct,
        "current_price": safe_round(current_price, 2),
    }
