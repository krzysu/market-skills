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

The lookup fall-through is:

1. Strategy-specific ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY[strategy_name]``
   keyed on ``(ticker, interval)`` exact match.
2. ``GLOBAL_MIN_CONVICTION_TO_EMIT``.

The trailing default is intentionally ``1`` (= no-op) so the legacy
emit-all behaviour is preserved for any (ticker, interval) without an
explicit entry. Raise the global default or add a more-specific entry
to tighten the gate.

## Loading private overrides

The module ships with ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY = {}``. At
import time it resolves the overrides file via:

1. ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` — explicit path (set = MUST exist).
2. ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR/conviction_thresholds_private.json``
   — fallback when the nightly backtest pipeline writes alongside this repo.
   Missing here is not an error (pipeline hasn't run yet).
3. Neither → shipped empty table, ``GLOBAL_MIN_CONVICTION_TO_EMIT=1``.

    {
      "GLOBAL_MIN_CONVICTION_TO_EMIT": 1,
      "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {
        "strategy-name": {
          "provider:ticker": {"interval": N}
        }
      }
    }

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
import os

ENV_OVERRIDES_PATH = "MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH"

# Global default threshold. Raise to tighten the gate for every strategy /
# (ticker, interval) that does not have a more-specific entry below. ``1``
# preserves the legacy "emit all surviving ideas" behaviour.
GLOBAL_MIN_CONVICTION_TO_EMIT = 1

# Per-strategy per-(ticker, interval) overrides. Shipped empty; populated
# at import time from $MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH if the
# env var is set and points at a readable JSON file. ``ticker`` matches
# the ``provider:ticker`` notation used by ``analysis.data``; ``interval``
# is one of the canonical intervals (``1d``, ``4h``, ``1h``, ``15m`` ...).
MIN_CONVICTION_TO_EMIT_BY_STRATEGY: dict[str, dict[tuple[str, str], int]] = {}


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


def _load_overrides() -> None:
    """Load overrides from the resolved path.

    Idempotent — safe to call multiple times; existing entries are merged
    (later calls overwrite on conflict). No-op when no path is resolved
    (uses the shipped empty table).

    When ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` is explicitly set but
    the file is missing, raises ``OSError`` (a configured-but-missing
    override file is a configuration bug). When the fallback OUT_DIR path
    is missing, silently uses the empty table (the pipeline hasn't run yet).
    """
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

    Args:
        strategy_name: The L3 strategy name (matches the entry in
            ``MIN_CONVICTION_TO_EMIT_BY_STRATEGY``; e.g.
            ``"strategy-trend-follow"``,
            ``"strategy-liquidity-sweep"``).
        ticker: The ticker in ``provider:ticker`` notation (e.g.
            ``"provider:symbol"``).
        interval: The canonical candle interval string (e.g.
            ``"1d"``, ``"4h"``).

    Returns:
        An integer ``>= 0``. ``0`` is opt-out (every idea survives),
        ``1`` is a no-op (formula floor is ``>= 1``), and any larger
        value drops ideas whose conviction is strictly below it.
        Unknown ``(strategy_name, ticker, interval)`` combinations
        fall through to :data:`GLOBAL_MIN_CONVICTION_TO_EMIT`.
    """
    table = MIN_CONVICTION_TO_EMIT_BY_STRATEGY.get(strategy_name)
    if table:
        entry = table.get((ticker, interval))
        if entry is not None:
            return entry
    return GLOBAL_MIN_CONVICTION_TO_EMIT
