"""market-s-r — Support and Resistance from swing point clustering."""

from analysis.formatting import safe_round
from analysis.indicators import (
    cluster_levels,
    extract_ohlcv,
    find_sr_levels,
    find_swing_highs,
    find_swing_lows,
)


def analyze(candles, interval="1d", period="1y"):
    """Analyze support/resistance levels from OHLC candles.

    Args:
        candles: list of [timestamp, open, high, low, close, volume]

    Returns:
        dict with S/R levels (context skill — no score/signal/zone)
    """
    if not candles or len(candles) < 20:
        return {"error": f"insufficient data (need 20+ candles, got {len(candles) if candles else 0})"}

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)
    current_price = closes[-1]

    # Swing points
    s_highs = find_swing_highs(highs, window=3)
    s_lows = find_swing_lows(lows, window=3)

    # Clustered levels from all swing points
    all_swing_levels = s_highs + s_lows
    clustered = cluster_levels(all_swing_levels, tolerance_pct=1.5)

    # Find nearest S/R
    nearest_s, nearest_r = find_sr_levels(candles, current_price, window=3)

    # Distance percentages
    support_dist_pct = None
    if nearest_s is not None:
        if current_price != 0:
            support_dist_pct = ((current_price - nearest_s) / current_price) * 100

    resistance_dist_pct = None
    if nearest_r is not None:
        if current_price != 0:
            resistance_dist_pct = ((nearest_r - current_price) / current_price) * 100

    # no_nearby_level: True when no S/R level is within 0.5% of current price
    # on either side (or no level at all on a side). Lets downstream L3 callers
    # flag "open-air" setups where stop and TPs sit far from any structure to
    # anchor the trade.
    no_nearby_level = (support_dist_pct is None or support_dist_pct > 0.5) and (
        resistance_dist_pct is None or resistance_dist_pct > 0.5
    )

    # Find touch counts from clustered levels
    support_touches = 0
    resistance_touches = 0

    if nearest_s is not None:
        for cl in clustered:
            if abs(cl["price"] - nearest_s) / nearest_s * 100 <= 1.5:
                support_touches = cl["touches"]
                break

    if nearest_r is not None:
        for cl in clustered:
            if abs(cl["price"] - nearest_r) / nearest_r * 100 <= 1.5:
                resistance_touches = cl["touches"]
                break

    # Filter to nearest levels (top 5 on each side)
    support_levels = sorted([level for level in all_swing_levels if level < current_price], reverse=True)[:5]
    resistance_levels = sorted([level for level in all_swing_levels if level >= current_price])[:5]

    # Check if price sits on a level
    on_level = any(abs(current_price - level) / current_price * 100 < 0.1 for level in all_swing_levels)

    return {
        "current_price": safe_round(current_price, 2),
        "nearest_support": safe_round(nearest_s, 2) if nearest_s else None,
        "nearest_resistance": safe_round(nearest_r, 2) if nearest_r else None,
        "support_distance_pct": safe_round(support_dist_pct, 2) if support_dist_pct is not None else None,
        "resistance_distance_pct": safe_round(resistance_dist_pct, 2) if resistance_dist_pct is not None else None,
        "support_touches": support_touches,
        "resistance_touches": resistance_touches,
        "support_count": len(support_levels),
        "resistance_count": len(resistance_levels),
        "clustered_levels": clustered,
        "sits_on_level": on_level,
        "no_nearby_level": no_nearby_level,
    }
