"""JSON output formatting helpers for skill scripts."""

import json
import sys
from datetime import UTC, datetime


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


def parse_args(argv):
    """Parse CLI args: [TICKER] [--json] [--source=PROVIDER] from sys.argv[1:].

    Returns (ticker, json_mode, source) tuple. ``ticker`` is ``None`` if not supplied;
    callers that require a ticker should validate via :func:`require_ticker`.
    """
    ticker = None
    json_mode = False
    source = None

    for arg in argv:
        if arg == "--json":
            json_mode = True
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1]
        elif not arg.startswith("--"):
            ticker = arg

    return ticker, json_mode, source


def require_ticker(ticker, json_mode):
    """Exit with a usage error if ``ticker`` was not provided on the CLI."""
    if ticker:
        return
    if json_mode:
        print('{"error": "ticker required"}')
    else:
        print("usage: run.py TICKER [--json] [--source=PROVIDER]")
    sys.exit(2)


def print_header(title, width=60):
    """Print a formatted header to stdout (for non-JSON mode)."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    print("=" * width)
    print(f" {title} — {now}")
    print("=" * width)
    print()
