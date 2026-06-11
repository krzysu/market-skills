"""JSON output formatting helpers for skill scripts."""

import json
import sys
from datetime import datetime, timezone


def clamp(val, min_val=0, max_val=100):
    """Clamp a numeric value to a range."""
    if val is None:
        return None
    return max(min_val, min(max_val, val))


def safe_round(val, ndigits=2):
    """Round a number, returning None if input is None."""
    if val is None:
        return None
    return round(val, ndigits)


def emit_json(data):
    """Print formatted JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def parse_args(argv, default_ticker=None):
    """Parse CLI args: [TICKER] [--json] from sys.argv[1:].

    Returns (ticker, json_mode) tuple.
    """
    ticker = default_ticker
    json_mode = False
    source = "yfinance"

    for arg in argv:
        if arg == "--json":
            json_mode = True
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1]
        elif not arg.startswith("--"):
            ticker = arg

    return ticker, json_mode, source


def print_header(title, width=60):
    """Print a formatted header to stdout (for non-JSON mode)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("=" * width)
    print(f" {title} — {now}")
    print("=" * width)
    print()
