"""JSON output formatting helpers for skill scripts."""

import json
import sys
from datetime import UTC, datetime

from analysis.intervals import DEFAULT_INTERVAL, DEFAULT_PERIOD, validate_timeframe


def safe_round(val, ndigits=2):
    """Round a number, returning None if input is None."""
    if val is None:
        return None
    return round(val, ndigits)


def round_price(value, ndigits=None):
    """Round a price to a precision matching its magnitude.

    Sub-$1 assets collapse to a single 2-decimal value when ATR is small relative
    to price — entry=0.09 with all three TPs rounding to 0.08 would defeat
    ``validate_l3_tp_ladder``'s TP3 vs entry * 0.95 check. This helper picks
    precision based on ``|value|``:

      |value| < 0.01   → 6 dp   (SHIB-tier)
      |value| < 1      → 4 dp   (sub-dollar crypto; standard on most retail venues)
      otherwise        → 2 dp   (covers AAPL at $187, BTC at $67k)

    Pass ndigits explicitly to override (e.g. for percentage-based fields where
    the magnitude heuristic would pick the wrong band).

    Note: this only governs storage precision on the idea dict. The CLI display
    layer can still reduce precision at print time without losing fidelity.
    """
    if value is None:
        return None
    if ndigits is not None:
        return round(value, ndigits)
    av = abs(value)
    if av < 0.01:
        return round(value, 6)
    if av < 1:
        return round(value, 4)
    return round(value, 2)


def emit_json(data):
    """Print formatted JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def parse_args(argv):
    """Parse the standard per-skill CLI surface.

    Recognised flags (all optional except ticker):
        TICKER                       positional ticker symbol
        --json                       emit JSON to stdout
        --source=PROVIDER            override data provider (auto-detected by default)
        --interval=INTERVAL          candle interval (default: 1d)
        --period=PERIOD              lookback period (default: 1y)

    Both ``--flag value`` (space-separated) and ``--flag=value`` (equals) styles
    are accepted. ``interval`` and ``period`` are validated against
    ``analysis.intervals.VALID_INTERVALS`` / ``VALID_PERIODS`` — a bad value
    raises ``ValueError`` which the CLI boundary catches and prints as a
    friendly usage error.

    Returns ``(ticker, json_mode, source, interval, period)``. The ticker
    defaults to ``None``; callers that require one should validate via
    :func:`require_ticker`.
    """
    ticker = None
    json_mode = False
    source = None
    out_interval = DEFAULT_INTERVAL
    out_period = DEFAULT_PERIOD

    _value_flags = {
        "--source": "source",
        "--interval": "interval",
        "--period": "period",
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            json_mode = True
            i += 1
        elif arg in ("--help", "-h", "--usage"):
            print("usage: run.py TICKER [--json] [--source=PROVIDER] [--interval=INTERVAL] [--period=PERIOD]")
            sys.exit(0)
        elif arg in _value_flags:
            if i + 1 >= len(argv):
                raise ValueError(f"{arg} requires a value")
            target = _value_flags[arg]
            if target == "source":
                source = argv[i + 1]
            elif target == "interval":
                out_interval = argv[i + 1]
            else:
                out_period = argv[i + 1]
            i += 2
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1]
            i += 1
        elif arg.startswith("--interval="):
            out_interval = arg.split("=", 1)[1]
            i += 1
        elif arg.startswith("--period="):
            out_period = arg.split("=", 1)[1]
            i += 1
        elif not arg.startswith("--"):
            ticker = arg
            i += 1
        else:
            raise ValueError(f"unrecognized flag: {arg}")

    validate_timeframe(out_interval, out_period)
    return ticker, json_mode, source, out_interval, out_period


def require_ticker(ticker, json_mode):
    """Exit with a usage error if ``ticker`` was not provided on the CLI."""
    if ticker:
        return
    if json_mode:
        print('{"error": "ticker required"}')
    else:
        print("usage: run.py TICKER [--json] [--source=PROVIDER] [--interval=INTERVAL] [--period=PERIOD]")
    sys.exit(2)


def parse_cli_error(exc: ValueError) -> str:
    """Format a timeframe validation error as a one-line CLI usage message."""
    return f"error: {exc.args[0] if exc.args else exc}"


def safe_parse_args(argv):
    """Parse args and exit 2 with a friendly message on validation errors.

    Wraps :func:`parse_args` so per-skill CLIs get the same UX: bad
    ``--interval`` / ``--period`` values print a one-liner to stderr and
    exit with code 2 instead of a stack trace. Returns the full
    ``(ticker, json_mode, source, interval, period)`` tuple.
    """
    try:
        return parse_args(argv)
    except ValueError as e:
        print(parse_cli_error(e), file=sys.stderr)
        sys.exit(2)


def print_header(title, width=60):
    """Print a formatted header to stdout (for non-JSON mode)."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    print("=" * width)
    print(f" {title} — {now}")
    print("=" * width)
    print()


_NOTE_PREVIEW = 80


def render_notes(notes, indent: str = "    ") -> list[str]:
    """Build human-readable lines for a list of active notes.

    Returns an empty list when `notes` is empty so callers can branch on truthiness.
    Layout: "<indent>notes: N active" + N previews + "… (+M more)" overflow tail.
    """
    if not notes:
        return []
    lines = [f"{indent}notes: {len(notes)} active"]
    for n in notes[:3]:
        text = n["note"]
        preview = text[:_NOTE_PREVIEW] + ("…" if len(text) > _NOTE_PREVIEW else "")
        lines.append(f"{indent}  - {preview}")
    if len(notes) > 3:
        lines.append(f"{indent}  … (+{len(notes) - 3} more)")
    return lines
