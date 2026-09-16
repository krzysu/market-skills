"""Per-strategy, per-(ticker, interval) minimum-conviction-to-emit table.

Beads ``oin`` and ``czr``:

- ``oin`` centralised the table behind one module so per-(ticker, interval)
  overrides can be added in one place instead of editing each L3 lib.
- ``czr`` removed the open-source asset references by shipping an empty
  table and loading private overrides from a JSON file outside the repo at
  import time. The shipped source contains zero ticker references.

## Reading the table

Each strategy calls :func:`lookup_min_conviction` at the end of
``analyze()`` to decide whether to drop low-conviction ideas. The returned
integer is the floor:

- ``0``: opt-out — emit every analyzed idea (``>= 0`` matches all).
- ``1``: no-op — the L3 conviction formula's natural floor on integer L2
  confidences is ``>= 1``, so this never drops anything. This is the
  legacy emit-all behaviour.
- ``>= 2``: drops ideas with conviction strictly below the floor.

The lookup resolution order is:

1. Strategy-specific ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY[strategy_name]``
   keyed on ``(ticker, interval)`` exact match — the stored key is
   whatever notation the overrides JSON carried (the backtest pipeline
   writes ``provider:ticker``, e.g. ``kraken:BTCUSD``).
2. Provider-agnostic canonical match: same interval, and a stored ticker
   whose :func:`canonical_ticker` symbol equals the query's symbol. A
   query carrying a known provider prefix prefers same-prefix
   candidates; same-symbol candidates whose values disagree are
   ambiguous and are never guessed. This is what makes the pipeline's
   ``provider:ticker`` keys bind even when the emit path hands the
   strategy a bare symbol (``BTCUSD``) or a separator form (``BTC-USD``
   / ``BTC/USD``).
3. ``GLOBAL_MIN_CONVICTION_TO_EMIT``.

The trailing default is intentionally ``1`` (= no-op) so the legacy
emit-all behaviour is preserved for any (ticker, interval) without an
explicit entry. Raise the global default or add a more-specific entry
to tighten the gate.

Step 3 is not silent: every fall-through bumps a module-level counter
(a total plus one per ``(strategy, ticker, interval)``), exposed via
:func:`fallthrough_stats` and resettable with
:func:`reset_fallthrough_stats`. Each fall-through also emits a
``logging`` debug record naming the strategy, ticker, interval, the
reason (``unmatched`` or ``ambiguous``) and the global floor used. This
is deliberately a debug log, never a stdout/stderr print — a print
would corrupt the AXI JSON envelope — and never a warning, because a
miss is normal for most tickers and must stay cheap.

## Loading private overrides

The module ships with ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY = {}``. At
import time it resolves the overrides file via:

1. ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` — explicit path (set = MUST exist).
2. ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR/conviction_thresholds_private.json``
   — fallback when the nightly backtest pipeline writes alongside this repo.
   Missing here is not an error (pipeline hasn't run yet).
3. Neither → shipped empty table, ``GLOBAL_MIN_CONVICTION_TO_EMIT=1``.

### Gate kill switch

``MARKET_SKILLS_CONVICTION_GATE`` disables the gate entirely when its
value (stripped, lowercased) is one of ``off``, ``0``, ``false``, ``no``.
When disabled:

- ``_load_overrides()`` returns immediately at import time — the
  overrides file is never read and the shipped empty table survives
  even when either path env var is set.
- :func:`lookup_min_conviction` returns the shipped default ``1``
  (never the possibly-overridden ``GLOBAL_MIN_CONVICTION_TO_EMIT``).

This exists so the nightly backtest pipeline can spawn the backtest
engine as a child process without the engine applying last night's
floors to tonight's runs (a floor of ``99`` would drop every idea, the
backtest would measure zero trades, and the zero-trade result would
re-lock the floor at ``99`` — a self-pollution loop). The pipeline
strips both path env vars from the child environment and sets this
marker to ``off``; any future caller that spawns strategies in-process
should do the same.

    {
      "GLOBAL_MIN_CONVICTION_TO_EMIT": 1,
      "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {
        "strategy-name": {
          "provider:SYMBOL": {"interval": N}
        }
      }
    }

Keys are written in ``provider:ticker`` notation (the backtest pipeline
does the writing); the lookup accepts the bare symbol, ``BASE-QUOTE`` /
``BASE/QUOTE``, or the qualified form equivalently — see
:func:`canonical_ticker`.

Nested dicts (``{ticker: {interval: N}}``) instead of tuple keys because
JSON object keys must be strings. The loader flattens to the in-memory
tuple-keyed form. A missing file raises ``OSError`` (mirroring the
failure-mode contract used by ``analysis.notes`` and
``analysis.watchlist``): a configured-but-missing override file is a
configuration bug, not a silent no-op.

Unset env var → shipped empty table, ``GLOBAL_MIN_CONVICTION_TO_EMIT=1``.

## Out-of-scope strategy overrides

Position-watchdog has its own per-signal-block ``min_conviction`` field
(see ``skills/position-watchdog/SKILL.md``). That is data-driven by the
watch config (signal-level, not strategy-level) and is not governed by
this module.
"""

from __future__ import annotations

import json
import logging
import os

ENV_OVERRIDES_PATH = "MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH"

# Belt-and-braces kill switch: when set to one of _GATE_OFF_VALUES the
# conviction gate is inert — the overrides file is never loaded and
# lookups return the shipped default. The backtest pipeline sets this
# in the engine child's environment so a backtest can never consume the
# thresholds file that the pipeline itself is about to write.
ENV_GATE = "MARKET_SKILLS_CONVICTION_GATE"

_GATE_OFF_VALUES = frozenset({"off", "0", "false", "no"})

# Shipped default threshold. Raise to tighten the gate for every strategy /
# (ticker, interval) that does not have a more-specific entry below. ``1``
# preserves the legacy "emit all surviving ideas" behaviour.
_DEFAULT_GLOBAL_MIN_CONVICTION_TO_EMIT = 1

# Live global threshold — starts at the shipped default and is replaced
# when the overrides JSON provides ``GLOBAL_MIN_CONVICTION_TO_EMIT``.
GLOBAL_MIN_CONVICTION_TO_EMIT = _DEFAULT_GLOBAL_MIN_CONVICTION_TO_EMIT

# Per-strategy per-(ticker, interval) overrides. Shipped empty; populated
# at import time from $MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH if the
# env var is set and points at a readable JSON file. ``ticker`` matches
# the ``provider:ticker`` notation used by ``analysis.data``; ``interval``
# is one of the canonical intervals (``1d``, ``4h``, ``1h``, ``15m`` ...).
# Keys are matched as stored; notation equivalence is resolved at lookup
# time via :func:`canonical_ticker` (the table is never re-keyed).
MIN_CONVICTION_TO_EMIT_BY_STRATEGY: dict[str, dict[tuple[str, str], int]] = {}

logger = logging.getLogger(__name__)

# Provider prefixes recognised by :func:`canonical_ticker`. Mirrors
# ``analysis/data.py::_PREFIX_MAP`` (``hl``, ``kraken``, ``yf``,
# ``yfinance``); redeclared here so this module stays a light leaf with
# no asset references and no import cycle.
_KNOWN_PREFIXES = frozenset({"hl", "kraken", "yf", "yfinance"})

# Symbol separators removed by :func:`canonical_ticker` (mirrors the
# Kraken provider's pair normalisation: ``BTC-USD`` == ``BTC/USD`` ==
# ``BTCUSD``).
_SYMBOL_SEPARATORS = ("-", "/", "_")

# Step-3 fall-through accounting (see "Reading the table" above).
_FALLTHROUGH_TOTAL = 0
_FALLTHROUGH_COUNTS: dict[tuple[str, str, str], int] = {}


def canonical_ticker(ticker: str) -> tuple[str | None, str]:
    """Return ``(provider_or_None, canonical_symbol)`` for ``ticker``.

    Splits an optional ``prefix:`` when the prefix is one of
    ``_KNOWN_PREFIXES`` (case-insensitive), then uppercases the symbol
    and removes ``-``, ``/`` and ``_`` separators. An unknown prefix is
    not a prefix — the whole string is treated as the symbol.

    Examples:
        >>> canonical_ticker("kraken:BTCUSD")
        ('kraken', 'BTCUSD')
        >>> canonical_ticker("BTC-USD")
        (None, 'BTCUSD')
        >>> canonical_ticker("hl:LIT")
        ('hl', 'LIT')
        >>> canonical_ticker("venue:XYZ")
        (None, 'VENUE:XYZ')
    """
    prefix: str | None = None
    symbol = ticker
    if ":" in symbol:
        head, tail = symbol.split(":", 1)
        if head.lower() in _KNOWN_PREFIXES:
            prefix = head.lower()
            symbol = tail
    for sep in _SYMBOL_SEPARATORS:
        symbol = symbol.replace(sep, "")
    return prefix, symbol.upper()


def fallthrough_stats() -> dict:
    """Snapshot of the step-3 fall-through accounting.

    Returns a dict with a ``total`` count of every lookup that fell
    through to :data:`GLOBAL_MIN_CONVICTION_TO_EMIT` (since import or the
    last :func:`reset_fallthrough_stats` call) and a ``by_key`` mapping
    keyed by ``(strategy_name, ticker, interval)``.
    """
    return {"total": _FALLTHROUGH_TOTAL, "by_key": dict(sorted(_FALLTHROUGH_COUNTS.items()))}


def reset_fallthrough_stats() -> None:
    """Zero the fall-through counters (tests and long-running callers)."""
    global _FALLTHROUGH_TOTAL
    _FALLTHROUGH_TOTAL = 0
    _FALLTHROUGH_COUNTS.clear()


def _record_fallthrough(strategy_name: str, ticker: str, interval: str, reason: str) -> None:
    """Count and debug-log a step-3 fall-through to the global floor."""
    global _FALLTHROUGH_TOTAL
    key = (strategy_name, ticker, interval)
    _FALLTHROUGH_COUNTS[key] = _FALLTHROUGH_COUNTS.get(key, 0) + 1
    _FALLTHROUGH_TOTAL += 1
    logger.debug(
        "conviction-gate fall-through (%s): strategy=%s ticker=%s interval=%s -> global floor %d",
        reason,
        strategy_name,
        ticker,
        interval,
        GLOBAL_MIN_CONVICTION_TO_EMIT,
    )


def _canonical_lookup(
    table: dict[tuple[str, str], int],
    ticker: str,
    interval: str,
) -> tuple[int | None, str | None]:
    """Resolve a floor via provider-agnostic canonical matching.

    Returns ``(floor, None)`` when exactly one distinct threshold binds,
    ``(None, "ambiguous")`` when same-symbol candidates disagree and no
    prefix preference resolves them, or ``(None, None)`` when no stored
    key matches the query's canonical symbol on this interval.

    The stored table is never mutated or re-keyed; matching is read-side
    only. See the module docstring's "Reading the table" section for the
    full resolution order.
    """
    query_prefix, query_symbol = canonical_ticker(ticker)
    if not query_symbol:
        return None, None
    candidates: list[tuple[str | None, int]] = []
    for (key_ticker, key_interval), value in table.items():
        if key_interval != interval:
            continue
        key_prefix, key_symbol = canonical_ticker(key_ticker)
        if key_symbol == query_symbol:
            candidates.append((key_prefix, value))
    if not candidates:
        return None, None
    if query_prefix is not None:
        preferred = [value for key_prefix, value in candidates if key_prefix == query_prefix]
        if preferred:
            candidates = [(query_prefix, value) for value in preferred]
    distinct = {value for _, value in candidates}
    if len(distinct) == 1:
        return distinct.pop(), None
    return None, "ambiguous"


def _coerce_threshold(value, *, context: str) -> int:
    """Coerce a JSON-decoded threshold value to a non-negative int.

    Rejects bools (which are technically a subclass of ``int`` but never a
    legitimate threshold — a silent ``int(True) == 1`` would mask a typo),
    floats (silent truncation would hide a config bug — ``2.5`` is not the
    same gate as ``2``), strings (the JSON loader already produced native
    types; a string here means the file was hand-written with the wrong
    shape), and negatives (would invert the gate). Raises ``ValueError``
    with the caller-supplied ``context`` so the loader can prefix the file
    path.
    """
    if type(value) is not int:
        raise ValueError(
            f"{context}: threshold must be a non-negative int (got {value!r}, type {type(value).__name__})"
        )
    if value < 0:
        raise ValueError(f"{context}: threshold must be >= 0 (0 = opt-out); got {value!r}")
    return value


def _resolve_path() -> str | None:
    """Resolve the overrides file path.

    1. ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` — explicit override.
    2. ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR/conviction_thresholds_private.json``
       — fallback when the backtest pipeline writes alongside this repo.
    3. Neither → ``None`` (use shipped empty table).
    """
    env = os.environ.get(ENV_OVERRIDES_PATH)
    if env:
        return os.path.expanduser(env)
    out_dir = os.environ.get("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR")
    if out_dir:
        return os.path.join(os.path.expanduser(out_dir), "conviction_thresholds_private.json")
    return None


def _gate_disabled() -> bool:
    """Return True when the ``MARKET_SKILLS_CONVICTION_GATE`` kill switch is off."""
    return os.environ.get(ENV_GATE, "").strip().lower() in _GATE_OFF_VALUES


def _load_overrides() -> None:
    """Load overrides from the resolved path.

    No-op when the ``MARKET_SKILLS_CONVICTION_GATE`` kill switch is off —
    the shipped empty table survives even if a path env var is set.

    Idempotent — safe to call multiple times; existing entries are merged
    (later calls overwrite on conflict). No-op when no path is resolved
    (uses the shipped empty table).

    When ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` is explicitly set but
    the file is missing, raises ``OSError`` (a configured-but-missing
    override file is a configuration bug). When the fallback OUT_DIR path
    is missing, silently uses the empty table (the pipeline hasn't run yet).
    """
    if _gate_disabled():
        return
    path = _resolve_path()
    if not path:
        return
    explicit_configured = bool(os.environ.get(ENV_OVERRIDES_PATH))
    if not os.path.isfile(path):
        if explicit_configured:
            raise OSError(
                f"{ENV_OVERRIDES_PATH}={os.environ[ENV_OVERRIDES_PATH]!r} "
                f"but no file at {path!r}; unset the env var to use the shipped empty table"
            )
        return
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level, got {type(data).__name__}")
    if "GLOBAL_MIN_CONVICTION_TO_EMIT" in data:
        globals()["GLOBAL_MIN_CONVICTION_TO_EMIT"] = _coerce_threshold(
            data["GLOBAL_MIN_CONVICTION_TO_EMIT"],
            context=f"{path}: GLOBAL_MIN_CONVICTION_TO_EMIT",
        )
    raw_table = data.get("MIN_CONVICTION_TO_EMIT_BY_STRATEGY", {})
    if not isinstance(raw_table, dict):
        raise ValueError(
            f"{path}: MIN_CONVICTION_TO_EMIT_BY_STRATEGY must be an object, got {type(raw_table).__name__}"
        )
    for strategy_name, ticker_map in raw_table.items():
        if not isinstance(ticker_map, dict):
            continue
        bucket = MIN_CONVICTION_TO_EMIT_BY_STRATEGY.setdefault(strategy_name, {})
        for ticker, interval_map in ticker_map.items():
            if not isinstance(interval_map, dict):
                continue
            for interval, threshold in interval_map.items():
                bucket[(ticker, interval)] = _coerce_threshold(
                    threshold,
                    context=f"{path}: {strategy_name}.{ticker}.{interval}",
                )


_load_overrides()


def lookup_min_conviction(strategy_name: str, ticker: str, interval: str) -> int:
    """Return the conviction floor for ``(strategy_name, ticker, interval)``.

    Reads the live ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY`` table on each
    call. Tests may mutate the table directly; production callers
    should treat it as read-only.

    Resolution order (see the module docstring for details):

    1. Exact ``(ticker, interval)`` match on the stored table key —
       fully backward compatible; the stored key is whatever notation
       the overrides JSON carried.
    2. Provider-agnostic canonical match (:func:`canonical_ticker`):
       same interval and an equivalent symbol, so ``kraken:BTCUSD``,
       ``BTCUSD`` and ``BTC-USD`` all bind to a stored
       ``kraken:BTCUSD`` key. A query prefix prefers same-prefix
       candidates; same-symbol candidates with disagreeing values are
       ambiguous and never guessed.
    3. Fall through to :data:`GLOBAL_MIN_CONVICTION_TO_EMIT`. Every
       fall-through is counted (see :func:`fallthrough_stats`) and
       debug-logged with the strategy, ticker, interval, reason
       (``unmatched`` or ``ambiguous``) and the global floor used.

    Args:
        strategy_name: The L3 strategy name (matches the entry in
            ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY``; e.g.
            ``"strategy-trend-follow"``,
            ``"strategy-liquidity-sweep"``).
        ticker: The ticker as the emit path holds it — bare symbol,
            ``provider:symbol``, or a separator form such as
            ``BASE-QUOTE`` / ``BASE/QUOTE``. All three resolve to the
            same floor for a given stored key.
        interval: The canonical candle interval string (e.g.
            ``"1d"``, ``"4h"``).

    Returns:
        An integer ``>= 0``. ``0`` is opt-out (every idea survives),
        ``1`` is a no-op (formula floor is ``>= 1``), and any larger
        value drops ideas whose conviction is strictly below it.
        Unknown ``(strategy_name, ticker, interval)`` combinations
        fall through to :data:`GLOBAL_MIN_CONVICTION_TO_EMIT`.

        When the ``MARKET_SKILLS_CONVICTION_GATE`` kill switch is off,
        returns the shipped default ``1`` regardless of the live table
        or ``GLOBAL_MIN_CONVICTION_TO_EMIT``.
    """
    if _gate_disabled():
        return _DEFAULT_GLOBAL_MIN_CONVICTION_TO_EMIT
    table = MIN_CONVICTION_TO_EMIT_BY_STRATEGY.get(strategy_name)
    if table:
        entry = table.get((ticker, interval))
        if entry is not None:
            return entry
        floor, reason = _canonical_lookup(table, ticker, interval)
        if floor is not None:
            return floor
        if reason == "ambiguous":
            _record_fallthrough(strategy_name, ticker, interval, "ambiguous")
            return GLOBAL_MIN_CONVICTION_TO_EMIT
    _record_fallthrough(strategy_name, ticker, interval, "unmatched")
    return GLOBAL_MIN_CONVICTION_TO_EMIT
