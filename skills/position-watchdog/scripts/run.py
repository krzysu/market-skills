#!/usr/bin/env python3
"""position-watchdog — CLI entrypoint.

Walks watches.json, fetches prices via market-skills data layer, evaluates
unified levels + signals for each enabled watch, persists per-watch state,
prints alerts to stdout with [NAME] prefix.

The pure evaluator (``lib.py``) emits structured event dicts; this
orchestrator (``run.py``) is responsible for fetching prices, building
the formatter context, invoking the formatter, and printing the result.

Exit codes:
  0 — normal tick (silent or alerts printed)
  1 — fatal: bad config, schema error, all-watches fetch failed
  2 — partial: some watches had fetch failures but at least one succeeded
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
format_alerts = _pw_fmt.format_alerts
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

    bare_ticker = _bare_ticker(provider_ticker)
    ideas_by_strat: dict[str, list] = {}
    for strat in strategies:
        mod = load_skill(f"strategy-{strat}")
        if mod is None:
            print(f"[WARN] strategy '{strat}' not found, skipping", file=sys.stderr)
            continue
        try:
            result = mod.analyze(candles, ticker=bare_ticker, interval=interval, period=period)
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
            fetch_failures += 1
            continue

        for alert in alerts:
            print(f"[{watch['name']}] {alert}")
            any_alerts = True

        if new_state is not None and not args.dry_run:
            _save_state(watch["name"], new_state)

    if fetch_failures and fetch_failures == enabled_count:
        print(f"FATAL: all {enabled_count} enabled watches had fetch failures", file=sys.stderr)
        return 1
    if fetch_failures and any_alerts:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
