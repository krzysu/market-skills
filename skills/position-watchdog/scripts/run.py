#!/usr/bin/env python3
"""position-watchdog — CLI entrypoint.

Walks watches.json, fetches prices via market-skills data layer, evaluates
unified levels + signals for each enabled watch, persists per-watch state,
prints alerts to stdout with [NAME] prefix.

The pure evaluator (``lib.py``) emits structured event dicts; this
orchestrator (``run.py``) is responsible for fetching prices, building
the formatter context, invoking the formatter, and printing the result.

Exit codes:
  0 — normal tick (silent or alerts printed); also when every enabled watch had a
      single-tick fetch blip (all-fetches-failed but the rolling 5-tick window
      shows <3 failures per watch) — logged as `[WARN]`, no FATAL. Also used
      by `--status` mode when every enabled watch returned a live price.
  1 — fatal: bad config, schema error, or sustained all-watches fetch failure
      (≥3 of last 5 ticks failing per watch)
  2 — partial: some watches had fetch failures but at least one succeeded.
      `--status` mode uses 2 if any per-watch live fetch failed (lines still
      print with `<fetch failed>` fallback).
"""

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys

from analysis.data import fetch_ohlc
from analysis.intervals import validate_timeframe
from analysis.skill_loader import load_skill

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "data", "watches.json")
DEFAULT_STATE_DIR = os.path.join(SKILL_DIR, "data")
STALE_STATE_SECONDS = 24 * 3600
DATA_DIR = ""

# Sustained-failure window for the all-watches FATAL trip. A single tick of
# all-fetches-failed is treated as a blip and logged but not fatal; the FATAL
# only trips when every enabled watch has failed in ≥THRESHOLD of the last
# LOOKBACK ticks. The 3-of-5 calibration suppresses the noisy 1-tick blip
# (single API hiccup) while still alarming on a real multi-tick outage.
FETCH_FAILURES_LOOKBACK = 5
FETCH_FAILURES_THRESHOLD = 3

ENV_CONFIG = "MARKET_SKILLS_WATCHDOG_PATH"
ENV_STATE_DIR = "MARKET_SKILLS_WATCHDOG_STATE_DIR"


def _load_watchdog_mod(filename: str, module_name: str):
    path = os.path.join(SKILL_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pw_lib = _load_watchdog_mod("lib.py", "position_watchdog_lib")
_pw_fmt = _load_watchdog_mod("formatter.py", "position_watchdog_formatter")
evaluate_levels = _pw_lib.evaluate_levels
evaluate_signals = _pw_lib.evaluate_signals
_status_summary = _pw_lib._status_summary
format_alerts = _pw_fmt.format_alerts
format_as_default_status = _pw_fmt.format_as_default_status
FORMATTERS = _pw_fmt.FORMATTERS


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _sanitize_name(name: str) -> str:
    return name.replace(":", "_").replace("/", "_").replace(".", "_")


def _state_path(name: str) -> str:
    return os.path.join(DATA_DIR, f"{_sanitize_name(name)}_state.json")


def _load_state(name: str) -> dict | None:
    path = _state_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(name: str, state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    state["_updated_at"] = _now_iso()
    path = _state_path(name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def _record_fetch_outcome(name: str, fetch_failed: bool) -> None:
    """Append the current tick's fetch outcome to the per-watch failure window.

    The window is a list of booleans, oldest first, capped at
    ``FETCH_FAILURES_LOOKBACK``. Used by the main loop to drive the
    sustained-failure FATAL trigger; a watch with a long True streak is
    part of a real outage, a single True is just a blip.
    """
    existing = _load_state(name) or {}
    window = list(existing.get("fetch_failures_window") or [])
    window.append(bool(fetch_failed))
    if len(window) > FETCH_FAILURES_LOOKBACK:
        window = window[-FETCH_FAILURES_LOOKBACK:]
    existing["fetch_failures_window"] = window
    _save_state(name, existing)


def _all_watches_failed_sustained(
    enabled_names: list[str],
    *,
    lookback: int = FETCH_FAILURES_LOOKBACK,
    threshold: int = FETCH_FAILURES_THRESHOLD,
) -> bool:
    """True iff every enabled watch has ≥threshold failures in the last lookback ticks.

    Reads each watch's ``fetch_failures_window`` from its state file. A
    sustained outage lights up when every watch has been failing together
    — a single 1-tick blip (one True per window) returns False.
    """
    if not enabled_names:
        return False
    for name in enabled_names:
        state = _load_state(name) or {}
        window = state.get("fetch_failures_window") or []
        recent = window[-lookback:]
        if sum(1 for x in recent if x) < threshold:
            return False
    return True


def _window_after_success(name: str, carry: list[bool] | None) -> list[bool]:
    """Return the failure window for a watch that just succeeded this tick.

    The success tick appends ``False`` to whatever window was already on
    disk (capped at ``FETCH_FAILURES_LOOKBACK``). Used to refresh the
    rolling counter when ``_process_watch`` produces a healthy new_state.
    """
    state = _load_state(name) or {}
    window = list(state.get("fetch_failures_window") or carry or [])
    window.append(False)
    if len(window) > FETCH_FAILURES_LOOKBACK:
        window = window[-FETCH_FAILURES_LOOKBACK:]
    return window


def _state_is_stale(state: dict | None) -> bool:
    if not state:
        return True
    ts = state.get("_updated_at")
    if not ts:
        return True
    try:
        age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return True
    return age > STALE_STATE_SECONDS


def _bare_in_watchlist(bare: str, known_tickers: set[str], watchlist_path: str | None) -> bool:
    """True if `bare` is registered in the watchlist (literal or via alias).

    Direct membership is the cheap path; falls back to `resolve()` to catch
    cases like `HYPEEUR` where the watchlist has `HYPEUSD` and the alias
    generator strips the quote-suffix to produce a shared bare token.
    Ambiguous matches count as registered.
    """
    if bare in known_tickers:
        return True
    try:
        from analysis.watchlist import resolve

        return resolve(bare, path=watchlist_path) is not None
    except (ImportError, ValueError):
        return False


def _bare_ticker(provider_ticker: str) -> str:
    """Extract the bare ticker from a `provider:ticker` string."""
    return provider_ticker.split(":", 1)[1] if ":" in provider_ticker else provider_ticker


def _current_price(provider_ticker: str, *, interval: str = "4h", period: str = "6mo") -> float | None:
    """Fetch the most recent close on the watch's timeframe. Returns None on failure.

    Both ``interval`` and ``period`` come from the watch config (defaults
    ``4h`` / ``6mo``). The caller is responsible for validating them via
    ``validate_timeframe`` before this is reached.
    """
    try:
        candles = fetch_ohlc(provider_ticker, interval=interval, period=period)
    except Exception as e:
        print(f"[WARN] fetch_ohlc({provider_ticker}) failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if not candles:
        return None
    last_close = candles[-1][4]
    if last_close is None or last_close <= 0:
        return None
    return float(last_close)


def _run_strategies(
    strategies: list[str],
    provider_ticker: str,
    *,
    interval: str = "4h",
    period: str = "6mo",
) -> dict[str, list]:
    """Run each L3 strategy on the watch's timeframe. Returns {strategy_name: [TradeIdea, ...]}.

    ``interval`` and ``period`` come from the watch config (defaults
    ``4h`` / ``6mo``). The caller validates them via ``validate_timeframe``
    before this is reached.
    """
    candles = None
    try:
        candles = fetch_ohlc(provider_ticker, interval=interval, period=period)
    except Exception as e:
        print(f"[WARN] fetch_ohlc for strategies failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

    if not candles or len(candles) < 50:
        return {}

    # Pass the qualified provider:ticker through to the L3 strategy so its
    # lookup_min_conviction() call can match keys in
    # analysis.conviction_thresholds.MIN_CONVICTION_TO_EMIT_BY_STRATEGY
    # (which are keyed on provider-qualified notation). Stripping the prefix
    # here would silently fall back to GLOBAL_MIN_CONVICTION_TO_EMIT for any
    # configured per-(ticker, interval) gate.
    ideas_by_strat: dict[str, list] = {}
    for strat in strategies:
        mod = load_skill(f"strategy-{strat}")
        if mod is None:
            print(f"[WARN] strategy '{strat}' not found, skipping", file=sys.stderr)
            continue
        try:
            result = mod.analyze(candles, ticker=provider_ticker, interval=interval, period=period)
            ideas_by_strat[strat] = result.get("ideas", [])
        except Exception as e:
            print(f"[WARN] strategy '{strat}' analyze failed: {type(e).__name__}: {e}", file=sys.stderr)
            ideas_by_strat[strat] = []
    return ideas_by_strat


def _is_open_positions(config_path: str | None) -> bool:
    """Exact basename match for the open-positions config (no substring sniff)."""
    base = os.path.basename(config_path) if config_path else ""
    return os.path.splitext(base)[0] == "open-positions"


def _default_format_style(config_path: str | None) -> str:
    """Default alert format style. Open-positions get the richer default; other
    configs keep the legacy compact one-liner for back-compat."""
    return "default" if _is_open_positions(config_path) else "compact"


_KNOWN_QUOTES = ("EUR", "USD", "USDT", "USDC", "GBP", "JPY")


def _primary_quote(provider: str) -> str:
    """Infer the quote currency from a `provider:ticker` string suffix."""
    if ":" not in provider:
        return ""
    ticker = provider.split(":", 1)[1]
    for q in _KNOWN_QUOTES:
        if ticker.endswith(q) and len(ticker) > len(q):
            return q
    return ""


def _validate_provider_format(value, field: str) -> str | None:
    """Return an error string if `value` is not a valid provider:ticker, else None."""
    if not isinstance(value, str) or not value:
        return f"{field} must be a non-empty string in 'provider:ticker' notation (got {value!r})"
    if ":" not in value:
        return f"{field}='{value}' missing ':' — use 'provider:ticker' notation (e.g. 'kraken:BTCUSD')"
    provider, _, ticker = value.partition(":")
    if not provider or not ticker:
        return f"{field}='{value}' has empty provider or ticker — use 'provider:ticker' notation"
    return None


def _validate_watch(watch: dict) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    if "name" not in watch:
        errors.append("missing 'name'")

    monitor = watch.get("monitor_provider")
    if monitor is None:
        errors.append("missing 'monitor_provider'")
    else:
        err = _validate_provider_format(monitor, "monitor_provider")
        if err:
            errors.append(err)

    execution = watch.get("execution_provider")
    if execution is not None:
        # Deprecated in this release — execution_provider was used for the
        # live dual-display UX. Library is single-currency now; consumers
        # wanting fallback monitoring or dual-display should configure a
        # second watch or fork the formatter.
        errors.append("execution_provider is not supported in this version — use monitor_provider only")

    if "enabled" not in watch:
        errors.append("missing 'enabled'")
    has_levels = watch.get("levels") is not None
    has_signals = watch.get("signals") is not None
    if not has_levels and not has_signals:
        errors.append("watch has neither 'levels' nor 'signals' — nothing to evaluate")
    for lv in watch.get("levels", []):
        t = lv.get("type")
        if t in ("stop", "tp") and "price" not in lv:
            errors.append(f"level type='{t}' missing 'price'")
        if t == "invalidation" and "below" not in lv:
            errors.append("invalidation level missing 'below'")
        if t == "drop" and "pct" not in lv:
            errors.append("drop level missing 'pct'")
        if t == "zone" and ("low" not in lv or "high" not in lv):
            errors.append("zone level missing 'low' or 'high'")
        if t == "drop" and "entry_price" not in watch:
            errors.append("drop level requires watch 'entry_price'")
        if t == "recovery" and "entry_price" not in watch:
            errors.append("recovery level requires watch 'entry_price'")
    fmt_style = watch.get("format_style")
    if fmt_style is not None and fmt_style not in FORMATTERS:
        errors.append(f"unknown format_style '{fmt_style}' (valid: {', '.join(sorted(FORMATTERS))})")
    return errors


def _build_ctx(
    watch: dict,
    price: float,
    monitor_provider: str,
    config_path: str | None,
) -> dict:
    """Build the formatter context dict for a watch.

    ``format_style`` comes from the watch (defaulted from filename in
    ``_process_watch``). ``primary_quote`` is inferred from the monitor
    provider ticker suffix. The library renders only the monitor's price
    (single currency); see SKILL.md "Single-currency alert rendering".
    """
    format_style = watch.get("format_style") or _default_format_style(config_path)
    return {
        "name": watch["name"],
        "price": float(price),
        "primary_quote": _primary_quote(monitor_provider) or "EUR",
        "monitor_provider": monitor_provider,
        "format_style": format_style,
    }


def _render_status_mode(watches: list[dict], args) -> int:
    """Render one status line per enabled watch and return the exit code.

    Read-only: makes one live-price fetch per watch (via the existing
    ``_current_price`` retry/error path), reads the existing state file
    (treating stale >24h state as empty), and prints the rendered lines.
    Does not advance ``alerted_levels``, ``above_entry_streak``, or
    ``prev_price`` — and does not update ``fetch_failures_window``.

    ``--watch`` is ignored here (status mode always renders all enabled
    watches per spec).

    Returns 0 if every watch returned a live price, 2 if any watch had a
    fetch failure (lines still print with a ``<fetch failed>`` fallback).

    When ``args.json`` is set, output goes through the AXI envelope
    (see ADR-0004): each watch is an item in ``data.watches[]`` and the
    full event dict is shipped (LLM-agent-friendly — no Unicode-minus /
    EUR-sign parsing). Human format (``--status`` only) is unchanged.
    """
    lines: list[str] = []
    events: list[dict] = []
    any_failed = False
    for watch in watches:
        if not watch.get("enabled"):
            continue
        name = watch["name"]
        monitor = watch["monitor_provider"]
        interval = watch.get("interval", "4h")
        period = watch.get("period", "6mo")
        try:
            validate_timeframe(interval, period)
        except ValueError as e:
            print(
                f"[{name}] invalid timeframe interval={interval!r} period={period!r}: {e} — skipping", file=sys.stderr
            )
            continue

        price = _current_price(monitor, interval=interval, period=period)
        if price is None:
            any_failed = True

        raw_state = _load_state(name) or {}
        state = {} if _state_is_stale(raw_state) else raw_state

        if price is not None:
            ctx = _build_ctx(watch, price, monitor, args.config)
        else:
            ctx = {
                "name": name,
                "price": None,
                "primary_quote": _primary_quote(monitor) or "EUR",
                "monitor_provider": monitor,
                "format_style": "default",
            }

        event = _status_summary(
            name=name,
            config=watch,
            state=state,
            current_price=price,
        )
        events.append(event)
        lines.append(format_as_default_status(event, ctx))

    if args.json:
        from analysis.output import emit_envelope_json

        emit_envelope_json(
            {"watches": events},
            count=len(events),
            help=[
                "Run position-watchdog (no --status) to advance state and fire alerts",
                "Pass --status --json for this structured read-only snapshot",
            ],
            errors=[f"fetch failed for {e['name']}" for e in events if e.get("current_price") is None],
            toon=False,
        )
        return 2 if any_failed else 0

    for line in lines:
        print(line)
    return 2 if any_failed else 0


def _process_watch(
    watch: dict,
    dry_run: bool,
    now: dt.datetime,
    config_path: str | None = None,
) -> tuple[list[str], dict | None]:
    """Process a single watch. Returns (alerts, new_state or None on fetch failure).

    ``interval`` and ``period`` are read from the watch config and default to
    ``4h`` / ``6mo``. A bad combination is reported on stderr and the watch is
    skipped — one typo shouldn't kill the whole tick.

    ``monitor_provider`` drives the live candle fetch, the L3 strategy
    evaluation, the live-price tick, and the formatter. ``execution_provider``
    is optional; when set, an extra tick is fetched from it so the live price
    renders in both quotes. Static levels (stop, TP, invalidation, entry)
    render in the monitor's quote only — they are stored there and the skill
    never synthesizes an execution-quote level from a live ratio.
    """
    name = watch["name"]
    monitor = watch["monitor_provider"]
    # Warn if watch name's provider prefix mismatches monitor_provider
    name_provider = name.split(":")[0] if ":" in name else None
    mon_provider = monitor.split(":")[0] if ":" in monitor else None
    if name_provider and mon_provider and name_provider != mon_provider:
        suggested = f"{name_provider}:{_bare_ticker(name)}"
        print(
            f"[WARN] watch '{name}' uses monitor_provider '{monitor}' (provider={mon_provider}) — "
            f"consider setting monitor_provider = '{suggested}' for accurate pricing",
            file=sys.stderr,
        )
    interval = watch.get("interval", "4h")
    period = watch.get("period", "6mo")

    try:
        validate_timeframe(interval, period)
    except ValueError as e:
        print(
            f"[{name}] invalid timeframe interval={interval!r} period={period!r}: {e} — skipping this watch",
            file=sys.stderr,
        )
        return [], None

    price = _current_price(monitor, interval=interval, period=period)
    if price is None:
        print(f"[{name}] fetch failed for {monitor}, skipping this tick", file=sys.stderr)
        return [], None

    ctx = _build_ctx(watch, price, monitor, config_path)

    prev_state = _load_state(name)
    stale = _state_is_stale(prev_state)
    use_state = {} if stale else (prev_state or {})

    levels_state = use_state.get("levels", {})
    signals_state = use_state.get("signals", {})

    events: list[dict] = []

    if watch.get("levels"):
        level_events, new_levels_state = evaluate_levels(
            watch,
            price,
            levels_state,
            now=now,
        )
        if stale:
            level_events = []
        events.extend(level_events)
        levels_state = new_levels_state

    if watch.get("signals"):
        strategies = []
        for sg in watch["signals"]:
            strategies.extend(sg.get("strategies", []))
        strategies = list(dict.fromkeys(strategies))
        l3_ideas = _run_strategies(strategies, monitor, interval=interval, period=period)
        signal_events, new_signals_state = evaluate_signals(
            watch,
            l3_ideas,
            signals_state,
            now=now,
        )
        if stale:
            signal_events = []
        events.extend(signal_events)
        signals_state = new_signals_state

    alerts = format_alerts(events, ctx)

    new_state = {
        "name": name,
        "levels": levels_state,
        "signals": signals_state,
    }

    if dry_run:
        print(f"[DRY-RUN] [{name}] @ {monitor} price={price} would-fire={len(alerts)} alerts")
        for a in alerts:
            print(f"  {a}")
        return [], None

    return alerts, new_state


def main() -> int:
    parser = argparse.ArgumentParser(description="position-watchdog — unified price + signal monitor")
    parser.add_argument(
        "--config",
        default=os.environ.get(ENV_CONFIG, DEFAULT_CONFIG),
        help=f"Path to watches.json (default: ${{{ENV_CONFIG}}} or {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get(ENV_STATE_DIR, DEFAULT_STATE_DIR),
        help=f"Directory for per-watch state files (default: ${{{ENV_STATE_DIR}}} or {DEFAULT_STATE_DIR})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would alert, don't update state")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the AXI envelope to stdout (machine-readable). Use with --status for a structured status snapshot.",
    )
    parser.add_argument("--watch", help="Process only this watch by name (default: all enabled)")
    parser.add_argument(
        "--watchlist",
        help="Optional path to market-watchlist JSON — warn if a watch's provider ticker isn't registered anywhere",
    )
    parser.add_argument(
        "--formatter",
        choices=sorted(FORMATTERS),
        default=None,
        help=(
            "Default formatter style when a watch doesn't set its own "
            f"format_style (valid: {', '.join(sorted(FORMATTERS))}). "
            "Defaults from filename: open-positions.json→default, else→compact."
        ),
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help=(
            "Render a one-line current-state snapshot per enabled watch and "
            "exit. Read-only: does not advance state, fire alerts, or write "
            "the fetch-failures window. --watch is ignored when --status is set."
        ),
    )
    args = parser.parse_args()

    globals()["DATA_DIR"] = args.state_dir  # noqa: F841 — module-level mutation

    if not os.path.exists(args.config):
        print(f"FATAL: config not found: {args.config}", file=sys.stderr)
        return 1

    try:
        with open(args.config) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FATAL: config unreadable: {e}", file=sys.stderr)
        return 1

    watches = config.get("watches", [])
    if not isinstance(watches, list):
        print("FATAL: 'watches' must be a list", file=sys.stderr)
        return 1

    valid_errors: list[str] = []
    for w in watches:
        errs = _validate_watch(w)
        for e in errs:
            valid_errors.append(f"watch '{w.get('name', '?')}': {e}")
    if valid_errors:
        print("FATAL: schema errors:", file=sys.stderr)
        for e in valid_errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.UTC)
    any_alerts = False
    fetch_failures = 0
    enabled_count = 0
    enabled_names: list[str] = []

    if args.status:
        return _render_status_mode(watches, args)

    known_tickers: set[str] | None = None
    if args.watchlist:
        try:
            from analysis.watchlist import all_tickers

            known_tickers = set(all_tickers(path=args.watchlist))
        except (ImportError, OSError) as e:
            print(f"[WARN] could not load watchlist {args.watchlist}: {e}", file=sys.stderr)
            known_tickers = None

    for watch in watches:
        if not watch.get("enabled"):
            continue
        if args.watch and watch["name"] != args.watch:
            continue
        enabled_count += 1
        enabled_names.append(watch["name"])

        if known_tickers is not None:
            bare = _bare_ticker(watch["monitor_provider"])
            if bare and not _bare_in_watchlist(bare, known_tickers, args.watchlist):
                print(
                    f"[WARN] watch '{watch['name']}' uses monitor_provider '{watch['monitor_provider']}' "
                    f"(bare '{bare}') which is not in the watchlist — consider registering it",
                    file=sys.stderr,
                )

        if args.formatter and not watch.get("format_style"):
            watch = {**watch, "format_style": args.formatter}

        alerts, new_state = _process_watch(watch, args.dry_run, now, config_path=args.config)
        if new_state is None and not args.dry_run:
            _record_fetch_outcome(watch["name"], fetch_failed=True)
            fetch_failures += 1
            continue

        for alert in alerts:
            print(f"[{watch['name']}] {alert}")
            any_alerts = True

        if new_state is not None and not args.dry_run:
            new_state["fetch_failures_window"] = _window_after_success(
                watch["name"], new_state.get("fetch_failures_window")
            )
            _save_state(watch["name"], new_state)

    if fetch_failures and fetch_failures == enabled_count:
        sustained = _all_watches_failed_sustained(enabled_names)
        if sustained:
            print(
                f"FATAL: all {enabled_count} enabled watches had fetch failures "
                f"(sustained: ≥{FETCH_FAILURES_THRESHOLD} of last {FETCH_FAILURES_LOOKBACK} ticks)",
                file=sys.stderr,
            )
            return 1
        print(
            f"[WARN] all {enabled_count} enabled watches failed this tick (sustained=False); suppressing FATAL",
            file=sys.stderr,
        )
    if fetch_failures and any_alerts:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
