"""Fibonacci retracement and extension level functions."""


def compute_fib_levels(swing_low, swing_high, fib_levels=None, fib_extensions=None):
    """Compute Fibonacci retracement and extension levels."""
    if fib_levels is None:
        fib_levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    if fib_extensions is None:
        fib_extensions = [1.272, 1.618]
    diff = swing_high - swing_low
    levels = {}
    for fib in fib_levels:
        price = swing_high - diff * fib
        levels[str(fib)] = round(price, 2)
    for fib in fib_extensions:
        price = swing_high + diff * (fib - 1)
        levels[str(fib)] = round(price, 2)
    return levels
