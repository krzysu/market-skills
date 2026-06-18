#!/usr/bin/env python3
"""position-watchdog — CLI entrypoint.

Walks watches.json, fetches prices via market-skills data layer, evaluates
unified levels + signals for each enabled watch, persists per-watch state,
prints alerts to stdout with [NAME] prefix.

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.data import fetch_ohlc  # noqa: E402
from analysis.skill_loader import load_skill  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "watches.json")
DEFAULT_STATE_DIR = os.path.join(SKILL_DIR, "data")
STALE_STATE_SECONDS = 24 * 3600
DATA_DIR = ""


def _load_watchdog_lib():
    lib_path = os.path.join(SKILL_DIR, "lib.py")
    spec = importlib.util.spec_from_file_location("position_watchdog_lib", lib_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load spec from {lib_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pw_lib = _load_watchdog_lib()
evaluate_levels = _pw_lib.evaluate_levels
evaluate_signals = _pw_lib.evaluate_signals


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


def _current_price(provider_ticker: str) -> float | None:
    try:
        candles = fetch_ohlc(provider_ticker, interval="1m", period="1d")
    except Exception as e:
        print(f"[WARN] fetch_ohlc({provider_ticker}) failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if not candles:
        return None
    last_close = candles[-1][4]
    if last_close is None or last_close <= 0:
        return None
    return float(last_close)


def _run_strategies(strategies: list[str], provider_ticker: str) -> dict[str, list]:
    """Returns {strategy_name: [TradeIdea, ...]} for the given ticker."""
    interval = "1d"
    period = "1y"
    candles = None
    try:
        candles = fetch_ohlc(provider_ticker, interval=interval, period=period)
    except Exception as e:
        print(f"[WARN] fetch_ohlc for strategies failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

    if not candles or len(candles) < 50:
        return {}

    bare_ticker = provider_ticker.split(":", 1)[1] if ":" in provider_ticker else provider_ticker
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


def _validate_watch(watch: dict) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    if "name" not in watch:
        errors.append("missing 'name'")
    if "provider" not in watch:
        errors.append("missing 'provider'")
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
    return errors


def _process_watch(watch: dict, dry_run: bool, now: dt.datetime) -> tuple[list[str], dict | None]:
    """Process a single watch. Returns (alerts, new_state or None on fetch failure)."""
    name = watch["name"]
    provider = watch["provider"]

    price = _current_price(provider)
    if price is None:
        print(f"[{name}] fetch failed for {provider}, skipping this tick", file=sys.stderr)
        return [], None

    prev_state = _load_state(name)
    stale = _state_is_stale(prev_state)
    use_state = {} if stale else (prev_state or {})

    levels_state = use_state.get("levels", {})
    signals_state = use_state.get("signals", {})

    alerts: list[str] = []

    if watch.get("levels"):
        level_alerts, new_levels_state = evaluate_levels(watch, price, levels_state)
        if stale:
            level_alerts = []
        alerts.extend(level_alerts)
        levels_state = new_levels_state

    if watch.get("signals"):
        strategies = []
        for sg in watch["signals"]:
            strategies.extend(sg.get("strategies", []))
        strategies = list(dict.fromkeys(strategies))
        l3_ideas = _run_strategies(strategies, provider)
        signal_alerts, new_signals_state = evaluate_signals(watch, l3_ideas, signals_state, now=now)
        if stale:
            signal_alerts = []
        alerts.extend(signal_alerts)
        signals_state = new_signals_state

    new_state = {
        "name": name,
        "levels": levels_state,
        "signals": signals_state,
    }

    if dry_run:
        print(f"[DRY-RUN] [{name}] @ {provider} price={price:.2f} would-fire={len(alerts)} alerts")
        for a in alerts:
            print(f"  {a}")
        return [], None

    return alerts, new_state


def main() -> int:
    parser = argparse.ArgumentParser(description="position-watchdog — unified price + signal monitor")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to watches.json")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="Directory for per-watch state files")
    parser.add_argument("--dry-run", action="store_true", help="Print what would alert, don't update state")
    parser.add_argument("--watch", help="Process only this watch by name (default: all enabled)")
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

    for watch in watches:
        if not watch.get("enabled"):
            continue
        if args.watch and watch["name"] != args.watch:
            continue
        enabled_count += 1

        alerts, new_state = _process_watch(watch, args.dry_run, now)
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
