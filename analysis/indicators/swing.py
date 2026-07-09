"""Swing detection and support/resistance functions."""


def find_swing_highs(highs, window=3):
    """Find local price maxima."""
    swings = []
    for i in range(window, len(highs) - window):
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, window + 1)
        ):
            swings.append(highs[i])
    return swings


def find_swing_lows(lows, window=3):
    """Find local price minima."""
    swings = []
    for i in range(window, len(lows) - window):
        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, window + 1)
        ):
            swings.append(lows[i])
    return swings


def swing_window_for_interval(interval):
    """Return appropriate swing detection window for the given interval."""
    interval_lower = interval.lower().strip()
    if interval_lower in ("1m", "5m", "15m", "30m"):
        return 20
    elif interval_lower == "1h":
        return 12
    elif interval_lower == "4h":
        return 8
    elif interval_lower == "1d":
        return 5
    elif interval_lower in ("1wk", "1w"):
        return 4
    else:
        return 5


def find_swing_high(candles, window=5):
    """Find the most recent significant swing high.

    Returns (price, index) tuple.
    """
    highs = [float(c[2]) for c in candles]
    for i in range(len(highs) - window - 1, window - 1, -1):
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, min(window + 1, len(highs) - i))
        ):
            return highs[i], i
    max_val = max(highs)
    return max_val, highs.index(max_val)


def find_swing_low(candles, window=5):
    """Find the most recent significant swing low.

    Returns (price, index) tuple.
    """
    lows = [float(c[3]) for c in candles]
    for i in range(len(lows) - window - 1, window - 1, -1):
        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, min(window + 1, len(lows) - i))
        ):
            return lows[i], i
    min_val = min(lows)
    return min_val, lows.index(min_val)


def cluster_levels(levels, tolerance_pct=1.5):
    """Group nearby price levels and return weighted clusters."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = []
    current = [levels[0]]

    for price in levels[1:]:
        if (price - current[0]) / current[0] * 100 <= tolerance_pct:
            current.append(price)
        else:
            clusters.append(current)
            current = [price]
    clusters.append(current)

    return [{"price": round(sum(c) / len(c), 2), "touches": len(c)} for c in clusters]


def find_sr_levels(candles, current_price, window=3):
    """Find nearest support and resistance from swing highs/lows.

    Returns (nearest_support, nearest_resistance) as floats or None.
    """
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]

    swing_highs = find_swing_highs(highs, window)
    swing_lows = find_swing_lows(lows, window)

    all_levels = swing_highs + swing_lows
    support = [level for level in all_levels if level < current_price]
    resistance = [level for level in all_levels if level >= current_price]

    nearest_s = max(support) if support else None
    nearest_r = min(resistance) if resistance else None

    return nearest_s, nearest_r
