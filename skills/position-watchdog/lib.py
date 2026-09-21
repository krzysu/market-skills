"""position-watchdog — pure evaluator for unified levels + signals.

All functions are pure: they take current price/state + watch config and
return ``(events, new_state)`` where ``events`` is a list of structured
dicts (no pre-formatted strings). String rendering lives in
``formatter.py``.

A watch has:
  levels: list of price-driven alert rules (stop, tp, drop, recovery, zone, invalidation)
  signals: list of strategy-driven alert rules (L3 strategies with conviction threshold)
  interval: candle interval for live-price tick and L3 evaluation (default "4h")
  period: candle lookback for live-price tick and L3 evaluation (default "6mo")

levels entries:
  {"type": "stop",         "price": float}                       — alert when price ≤ price
  {"type": "tp",           "price": float, "exit_pct": int}      — alert when price ≥ price
  {"type": "drop",         "pct": float}                          — alert when pct-from-entry ≤ pct
  {"type": "recovery"}                                               — alert after 2 ticks above entry post-drop
  {"type": "zone",         "low": float, "high": float, "label": str, "emoji": str}  — alert on zone entry
  {"type": "invalidation", "below": float}                        — alert when price < below

Event dict shapes (see ``formatter.py`` for the text rendering layer):

  stop:        {"type": "stop", "level_id", "current_price", "stop_price", "triggered_at"}
  tp:          {"type": "tp", "level_id", "current_price", "tp_price", "exit_pct",
                "qty", "position_size", "triggered_at"}
  drop:        {"type": "drop", "level_id", "current_price", "entry_price",
                "pct_from_entry", "threshold_pct", "severity", "triggered_at"}
  recovery:    {"type": "recovery", "level_id", "current_price", "entry_price", "triggered_at"}
  zone:        {"type": "zone", "level_id", "current_price", "low", "high",
                "label", "emoji", "triggered_at"}
  invalidation:{"type": "invalidation", "level_id", "current_price", "below_price", "triggered_at"}
  signal:      {"type": "signal", "strategy", "direction", "conviction", "entry_price",
                 "entry_range", "stop_loss", "take_profit", "reasoning", "source_skills",
                 "entry_type", "triggered_at"}
  venue_stop_fill: {"type": "venue_stop_fill", "level_id", "name", "order_id", "order_type",
                 "side", "pair", "fill_quote", "fill_price", "filled_volume", "order_volume",
                 "position_size", "entry_price", "cost", "fee", "realised_pnl",
                 "partial", "closed_position", "closed_at", "triggered_at"}

Per-watch state keys added by the venue-stop detector:

  venue_stop_fills — {order_id: {...seen summary...}} dedupe ledger across ticks
  position_closed  — closure record dict (order_id, closed_at, fill_price,
                     filled_volume, reported_at), or absent/None while the
                     position is still open
"""

import datetime as _dt

# Single source of truth for Kraken's legacy asset codes (XXBT -> BTC, ...):
# the execution adapter owns the table and the watchdog process already loads
# that module for the venue read (see run.py's `_read_venue_closed_orders`).
from analysis.providers.execution.kraken_spot import _KRAKEN_ASSET_MAP as _KRAKEN_ASSET_MAP

VENUE_STOP_ORDER_TYPES = frozenset({"stop-loss", "stop-loss-limit", "trailing-stop", "trailing-stop-limit"})
"""Venue order types that are a resting stop/trailing stop — the exit family."""

POSITION_CLOSED_REL_TOL = 0.01
"""Relative tolerance for "the fill covers the whole position" (1%).

The executed quantity is the venue's number and ``position_size`` is the
ledger-derived number; venue/ledger rounding and a fee taken in the base asset
make a full exit fill slightly smaller than the held size. Anything below 99%
of the held size is a partial exit and must NOT stop monitoring."""

_LEGACY_CODES = {"XBT": "BTC", "XDG": "DOGE"}
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "EUR", "GBP", "JPY")


def _now_iso(now: _dt.datetime | None = None) -> str:
    return (now or _dt.datetime.now(_dt.UTC)).isoformat()


def _tp_qty(size, exit_pct) -> float | None:
    if size is None or exit_pct is None:
        return None
    return round(size * exit_pct / 100, 4)


def evaluate_levels(
    watch: dict,
    current_price: float,
    prev_state: dict | None,
    now: _dt.datetime | None = None,
) -> tuple[list[dict], dict]:
    """Walk all levels in the watch and emit alert events on state changes.

    Returns ``(events, new_state)`` where ``events`` is a list of
    structured dicts (no pre-formatted strings — see module docstring for
    the per-type shapes). The ``now`` argument is used to stamp
    ``triggered_at`` on every emitted event; callers that need
    deterministic timestamps in tests should pass a fixed value. When
    omitted, defaults to ``datetime.now(UTC)`` (added in the same release
    that moved the formatter out of lib — the old default of
    ``datetime.now(UTC)`` inside the function still holds).

    Levels are evaluated purely against ``current_price``; the candle
    timeframe of the live-price tick does not affect this function.
    """
    levels = watch.get("levels", [])
    if not levels:
        return [], {}

    entry = watch.get("entry_price")
    size = watch.get("position_size")
    name = watch["name"]

    state = prev_state or {}
    alerted = dict(state.get("alerted_levels", {}))
    above_streak = int(state.get("above_entry_streak", 0))
    prev_price = state.get("prev_price")

    ts = _now_iso(now)

    events: list[dict] = []
    new_alerted = dict(alerted)
    new_streak = above_streak

    above_entry = entry is not None and current_price > float(entry)

    for level in levels:
        level_type = level.get("type")
        level_id = _level_id(level)

        if level_type == "stop":
            stop = float(level["price"])
            if current_price <= stop and alerted.get(level_id) != "fired":
                events.append(
                    {
                        "type": "stop",
                        "level_id": level_id,
                        "current_price": float(current_price),
                        "stop_price": stop,
                        "triggered_at": ts,
                    }
                )
                new_alerted[level_id] = "fired"

        elif level_type == "tp":
            tp_price = float(level["price"])
            exit_pct = level.get("exit_pct")
            if current_price >= tp_price and alerted.get(level_id) != "fired":
                events.append(
                    {
                        "type": "tp",
                        "level_id": level_id,
                        "current_price": float(current_price),
                        "tp_price": tp_price,
                        "exit_pct": exit_pct,
                        "qty": _tp_qty(size, exit_pct),
                        "position_size": size,
                        "triggered_at": ts,
                    }
                )
                new_alerted[level_id] = "fired"

        elif level_type == "drop":
            if entry is None:
                continue
            pct_threshold = float(level["pct"])
            pct_from_entry = (current_price - float(entry)) / float(entry) * 100
            if pct_from_entry <= pct_threshold and alerted.get(level_id) != "fired":
                events.append(
                    {
                        "type": "drop",
                        "level_id": level_id,
                        "current_price": float(current_price),
                        "entry_price": float(entry),
                        "pct_from_entry": pct_from_entry,
                        "threshold_pct": pct_threshold,
                        "severity": "critical" if pct_threshold <= -10 else "warn",
                        "triggered_at": ts,
                    }
                )
                new_alerted[level_id] = "fired"

        elif level_type == "recovery":
            if entry is None:
                continue
            if above_entry:
                new_streak = above_streak + 1
                recovery_id = "recovery"
                if (
                    any(alerted.get(_level_id(lv)) == "fired" for lv in levels if lv.get("type") == "drop")
                    and new_streak >= 2
                    and alerted.get(recovery_id) != "fired"
                ):
                    events.append(
                        {
                            "type": "recovery",
                            "level_id": recovery_id,
                            "current_price": float(current_price),
                            "entry_price": float(entry),
                            "triggered_at": ts,
                        }
                    )
                    new_alerted[recovery_id] = "fired"
            else:
                new_streak = 0

        elif level_type == "zone":
            low = float(level["low"])
            high = float(level.get("high", float("inf")))
            label = level.get("label", f"zone {low:g}–{high:g}")
            emoji = level.get("emoji", "🎯")
            in_zone = low <= current_price <= high
            was_in_zone = prev_price is not None and low <= prev_price <= high
            if in_zone and not was_in_zone:
                events.append(
                    {
                        "type": "zone",
                        "level_id": level_id,
                        "current_price": float(current_price),
                        "low": low,
                        "high": high,
                        "label": label,
                        "emoji": emoji,
                        "triggered_at": ts,
                    }
                )
                new_alerted[level_id] = "fired"

        elif level_type == "invalidation":
            below = float(level["below"])
            if current_price < below and alerted.get(level_id) != "fired":
                events.append(
                    {
                        "type": "invalidation",
                        "level_id": level_id,
                        "current_price": float(current_price),
                        "below_price": below,
                        "name": name,
                        "triggered_at": ts,
                    }
                )
                new_alerted[level_id] = "fired"

    if entry is not None and not above_entry:
        new_streak = 0

    new_state = {
        "alerted_levels": new_alerted,
        "above_entry_streak": new_streak,
        "prev_price": current_price,
    }
    return events, new_state


def evaluate_signals(
    watch: dict,
    l3_ideas_by_strategy: dict,
    prev_state: dict | None,
    now: _dt.datetime | None = None,
) -> tuple[list[dict], dict]:
    """Walk signal blocks, alert on L3 strategy ideas meeting conviction + cooldown.

    Returns ``(events, new_state)`` where each event is a structured dict
    (see module docstring). The ``now`` argument defaults to
    ``datetime.now(UTC)`` and is used to stamp ``triggered_at`` and to
    compare against the cooldown window stored in state.

    l3_ideas_by_strategy: {strategy_name: [TradeIdea, ...]} filtered to this watch's provider/ticker.
    Ideas are assumed to have been built from candles on the watch's
    configured timeframe by the caller; this function does not filter by
    timeframe.
    """
    signals = watch.get("signals", [])
    if not signals:
        return [], {}

    state = prev_state or {}
    last_alert_at: dict = state.get("last_signal_alert_at", {})

    ts = _now_iso(now)
    now_dt = now or _dt.datetime.now(_dt.UTC)

    events: list[dict] = []
    new_last = dict(last_alert_at)

    for sg in signals:
        strategies = sg.get("strategies", [])
        min_conv = int(sg.get("min_conviction", 3))
        cooldown_hours = float(sg.get("cooldown_hours", 0))
        direction_filter = (sg.get("direction") or "").strip().lower() or None

        for strat in strategies:
            for idea in l3_ideas_by_strategy.get(strat, []):
                direction = (idea.get("direction", "") or "").strip().lower()
                conviction = int(idea.get("conviction", 0))
                entry_p = idea.get("entry_price")
                stop_p = idea.get("stop_loss")

                if direction_filter and direction != direction_filter:
                    continue
                if conviction < min_conv:
                    continue

                key = f"{strat}:{direction}"
                prior_ts = last_alert_at.get(key)
                if prior_ts:
                    try:
                        prior_dt = _dt.datetime.fromisoformat(prior_ts)
                        if (now_dt - prior_dt).total_seconds() < cooldown_hours * 3600:
                            continue
                    except ValueError:
                        pass

                events.append(
                    {
                        "type": "signal",
                        "strategy": strat,
                        "direction": direction,
                        "conviction": conviction,
                        "entry_price": float(entry_p) if entry_p is not None else None,
                        "entry_range": list(idea.get("entry_range") or []),
                        "stop_loss": float(stop_p) if stop_p is not None else None,
                        "take_profit": list(idea.get("take_profit") or []),
                        "reasoning": str(idea.get("reasoning") or ""),
                        "source_skills": list(idea.get("source_skills") or []),
                        "entry_type": str(idea.get("entry_type") or "limit"),
                        "triggered_at": ts,
                    }
                )
                new_last[key] = ts

    new_state = {"last_signal_alert_at": new_last}
    return events, new_state


def _canonical_base(code: str) -> str:
    """Map Kraken legacy asset codes onto the canonical codes this repo keys on."""
    code = _KRAKEN_ASSET_MAP.get(code, code)
    return _LEGACY_CODES.get(code, code)


def _split_pair(pair: str) -> tuple[str, str]:
    """Split a pair token into ``(base, quote)``, both normalised.

    Handles a ``provider:`` prefix, ``-`` / ``/`` separators, the legacy Kraken
    forms this repo uses as canonical pair keys (``XXBTZUSD``, ``XETHZEUR`` —
    ``Z``-infixed quote, ``X``-prefixed base) and the plain legacy codes
    (``XBTUSD``). The quote is ``""`` when no known quote suffix is present.
    """
    token = pair.strip().upper()
    token = token.split(":", 1)[1] if ":" in token else token
    token = token.replace("-", "").replace("/", "")
    for q in _QUOTE_SUFFIXES:
        if token.endswith("Z" + q) and len(token) > len(q) + 1:
            return _canonical_base(token[: -(len(q) + 1)]), q
        if token.endswith(q) and len(token) > len(q):
            return _canonical_base(token[: -len(q)]), q
    return _canonical_base(token), ""


def _pair_quote(pair: str) -> str:
    """Canonical quote currency of a pair token (``""`` when none recognised)."""
    return _split_pair(pair)[1]


def venue_pair_matches(provider_ticker: str, venue_pair: str) -> bool:
    """True when a watch's ``monitor_provider`` and a venue pair name the same asset.

    Compares the *base* asset: strip the ``provider:`` prefix, uppercase, drop
    ``-`` / ``/``, drop a trailing quote suffix (USDT/USDC/USD/EUR/GBP/JPY),
    including the legacy Kraken ``Z``-infixed quote forms (``XXBTZUSD``,
    ``XETHZEUR``), and map legacy asset codes through the provider's own
    ``_KRAKEN_ASSET_MAP`` (``XXBT`` -> ``BTC``) plus ``XBT`` -> ``BTC`` /
    ``XDG`` -> ``DOGE``. The quote is ignored on purpose: a held file may
    monitor ``kraken:<TICKER>USD`` while the venue filled the ``<TICKER>EUR``
    pair, and the ledger keys and monitor providers are matched
    quote-insensitively elsewhere in this repo.
    """

    def _base_asset(token: str) -> str:
        return _split_pair(token)[0]

    if not provider_ticker or not venue_pair:
        return False
    watch_base = _base_asset(provider_ticker)
    venue_base = _base_asset(venue_pair)
    if not watch_base or not venue_base:
        return False
    return watch_base == venue_base


def evaluate_venue_stop_fills(
    watch: dict,
    closed_orders: list[dict],
    prev_state: dict | None,
    now: _dt.datetime | None = None,
) -> tuple[list[dict], dict]:
    """Detect venue-side stop fills for one watch from the venue's closed orders.

    Returns ``(events, new_state)``. Events (one per newly-seen fill):

      {"type": "venue_stop_fill", "level_id": "venue_stop_fill:<order_id>",
       "name", "order_id", "order_type", "side", "pair", "fill_quote",
       "fill_price": float|None, "filled_volume": float, "order_volume": float,
       "position_size": float|None, "entry_price": float|None,
       "cost": float|None, "fee": float, "realised_pnl": float|None,
       "partial": bool, "closed_position": bool,
       "closed_at": float|None, "triggered_at": iso-8601}

    ``realised_pnl`` is ``(fill_price − entry_price) × filled_volume − fee``
    and is computed ONLY when the fill's quote equals the monitor's quote —
    ``entry_price`` is denominated in the monitor quote, and the pair match is
    quote-insensitive (a ``kraken:<TICKER>USD`` monitor can be filled on the
    ``<TICKER>EUR`` pair), so a cross-quote fill reports ``None`` instead of a
    fabricated figure. ``fill_quote`` carries the fill's quote so renderers
    can show the price in its own quote rather than the monitor's symbol.

    ``new_state`` keys (both always present so the caller's merge is explicit):
      ``venue_stop_fills`` — {order_id: {...seen summary...}} dedup ledger,
      ``position_closed`` — the closure record dict, or nothing/None when the
      position is still open.

    An order is a candidate only when it is a venue-side stop-family SELL with
    executed volume on the watch's base asset (see ``venue_pair_matches``).
    Manual ``market`` / ``limit`` sells are deliberately not reported.

    Events are emitted even when the caller's state is stale (>24h). Staleness
    exists to stop *level* alerts re-firing; a venue fill is reported once ever
    (exact ``order_id`` dedupe), so suppressing it would silently lose the
    report.

    Once a closure is recorded (``position_closed`` set in ``prev_state``), the
    function returns no events and carries both state keys forward unchanged —
    no re-detection, no repeat alerts.
    """
    state = prev_state or {}
    seen: dict = dict(state.get("venue_stop_fills") or {})

    prior_closed = state.get("position_closed")
    if prior_closed:
        return [], {"venue_stop_fills": seen, "position_closed": prior_closed}

    name = watch.get("name", "?")
    monitor = watch.get("monitor_provider", "")
    monitor_quote = _pair_quote(monitor)
    position_size = watch.get("position_size")
    entry_price = watch.get("entry_price")

    ts = _now_iso(now)
    events: list[dict] = []
    closed_record: dict | None = None

    for order in closed_orders or []:
        order_id = order.get("order_id")
        if not order_id:
            continue
        side = (order.get("side") or "").strip().lower()
        order_type = (order.get("order_type") or "").strip().lower()
        filled_volume = float(order.get("filled_volume") or 0)
        if side != "sell":
            continue
        if order_type not in VENUE_STOP_ORDER_TYPES:
            continue
        if filled_volume <= 0:
            continue
        if not venue_pair_matches(monitor, order.get("pair") or ""):
            continue
        if order_id in seen:
            continue

        fill_price = order.get("fill_price")
        fill_price = float(fill_price) if fill_price is not None else None
        fill_quote = _pair_quote(order.get("pair") or "")
        order_volume = float(order.get("volume") or 0)
        cost = order.get("cost")
        cost = float(cost) if cost is not None else None
        fee = float(order.get("fee") or 0)

        # P&L only when the fill's quote IS the monitor quote: entry_price is
        # denominated in the monitor quote and the pair match is
        # quote-insensitive, so subtracting across quotes would fabricate a
        # figure. A cross-quote fill reports realised_pnl=None.
        if fill_price is not None and entry_price is not None and monitor_quote and fill_quote == monitor_quote:
            realised_pnl: float | None = round((fill_price - float(entry_price)) * filled_volume - fee, 2)
        else:
            realised_pnl = None

        if order_volume > 0:
            partial = filled_volume < order_volume * (1 - POSITION_CLOSED_REL_TOL)
        else:
            partial = True

        closed_position = position_size is not None and filled_volume >= float(position_size) * (
            1 - POSITION_CLOSED_REL_TOL
        )

        events.append(
            {
                "type": "venue_stop_fill",
                "level_id": f"venue_stop_fill:{order_id}",
                "name": name,
                "order_id": order_id,
                "order_type": order_type,
                "side": side,
                "pair": order.get("pair") or "",
                "fill_quote": fill_quote,
                "fill_price": fill_price,
                "filled_volume": filled_volume,
                "order_volume": order_volume,
                "position_size": position_size,
                "entry_price": float(entry_price) if entry_price is not None else None,
                "cost": cost,
                "fee": fee,
                "realised_pnl": realised_pnl,
                "partial": partial,
                "closed_position": closed_position,
                "closed_at": order.get("closed_at"),
                "triggered_at": ts,
            }
        )
        seen[order_id] = {
            "reported_at": ts,
            "fill_price": fill_price,
            "filled_volume": filled_volume,
            "order_type": order_type,
            "closed_position": closed_position,
        }

        if closed_position and closed_record is None:
            closed_record = {
                "order_id": order_id,
                "closed_at": order.get("closed_at"),
                "fill_price": fill_price,
                "filled_volume": filled_volume,
                "reported_at": ts,
            }

    new_state = {"venue_stop_fills": seen, "position_closed": closed_record}
    return events, new_state


def _level_id(level: dict) -> str:
    """Stable identifier for a level so we can dedupe alerts across ticks."""
    level_type = level.get("type", "?")
    if level_type in ("stop", "tp", "drop", "invalidation"):
        return f"{level_type}:{level.get('price', level.get('pct', level.get('below')))}"
    if level_type == "zone":
        return f"zone:{level.get('low')}-{level.get('high')}:{level.get('label', '')}"
    if level_type == "recovery":
        return "recovery"
    return f"{level_type}:{level}"


def _status_summary(
    *,
    name: str,
    config: dict,
    state: dict | None,
    current_price: float | None,
) -> dict:
    """Build the status event dict for one watch. Read-only; no I/O.

    Composes the existing config (entry_price, levels, position_size) and
    the existing state file (alerted_levels, above_entry_streak,
    prev_price) with the live ``current_price`` into a single event dict
    shaped for ``formatter.format_as_default_status``.

    The caller is responsible for staleness-filtering ``state`` — this
    function treats ``state`` as either ``None`` / ``{}`` or a fresh
    state dict. Stale state (>24h) makes streaks and alerted_levels
    unreliable; ``--status`` mode replaces stale state with ``{}`` so
    the output reflects only the current tick + config.

    Returns keys:
      name, current_price, entry_price, prev_price, above_entry_streak,
      alerted_levels, active_zone (dict|None), next_zone_below (dict|None),
      invalidation_floor (float|None), next_tp_unfired (dict|None),
      fired_drops (list[dict]), position_size, pct_from_entry,
      position_closed (dict|None — read from state, None when unset).
    """
    levels = config.get("levels", []) or []
    entry = config.get("entry_price")
    size = config.get("position_size")

    state = state or {}
    alerted = state.get("alerted_levels") or {}
    streak = int(state.get("above_entry_streak", 0) or 0)
    prev_price = state.get("prev_price")

    zones = [lv for lv in levels if lv.get("type") == "zone"]
    active_zone: dict | None = None
    next_zone_below: dict | None = None

    if current_price is not None:
        for z in zones:
            z_low = float(z.get("low", 0))
            z_high = float(z.get("high", float("inf")))
            if z_low <= current_price <= z_high:
                active_zone = {
                    "label": z.get("label", f"zone {z_low:g}–{z_high:g}"),
                    "emoji": z.get("emoji", "🎯"),
                    "low": z_low,
                    "high": z_high,
                }
                break
        below = [
            z
            for z in zones
            if float(z.get("low", 0)) < current_price
            and not (
                active_zone is not None
                and float(z.get("low", 0)) == active_zone["low"]
                and float(z.get("high", float("inf"))) == active_zone["high"]
            )
        ]
        if below:
            below.sort(key=lambda z: float(z.get("low", 0)), reverse=True)
            top = below[0]
            z_low = float(top.get("low", 0))
            z_high = float(top.get("high", float("inf")))
            next_zone_below = {
                "label": top.get("label", f"zone {z_low:g}–{z_high:g}"),
                "emoji": top.get("emoji", "🎯"),
                "low": z_low,
                "high": z_high,
            }

    invalids = [float(lv["below"]) for lv in levels if lv.get("type") == "invalidation" and "below" in lv]
    invalidation_floor = max(invalids) if invalids else None

    def _tp_price(lv: dict) -> float:
        return float(lv["price"])

    unfired_tps = [lv for lv in levels if lv.get("type") == "tp" and f"tp:{lv.get('price')}" not in alerted]
    next_tp_unfired: dict | None = None
    if unfired_tps:
        ref = current_price if current_price is not None else prev_price
        if entry is not None and ref is not None and float(ref) < float(entry):
            unfired_tps.sort(key=_tp_price, reverse=True)
        else:
            unfired_tps.sort(key=_tp_price)
        head = unfired_tps[0]
        next_tp_unfired = {
            "price": _tp_price(head),
            "exit_pct": head.get("exit_pct"),
        }

    fired_drops = [
        {"pct": float(lv["pct"])} for lv in levels if lv.get("type") == "drop" and f"drop:{lv.get('pct')}" in alerted
    ]
    fired_drops.sort(key=lambda d: d["pct"])

    pct_from_entry: float | None = None
    if entry is not None:
        ref_price = current_price if current_price is not None else prev_price
        if ref_price is not None:
            try:
                pct_from_entry = (float(ref_price) - float(entry)) / float(entry) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                pct_from_entry = None

    return {
        "name": name,
        "current_price": current_price,
        "entry_price": float(entry) if entry is not None else None,
        "prev_price": prev_price,
        "above_entry_streak": streak,
        "alerted_levels": dict(alerted),
        "active_zone": active_zone,
        "next_zone_below": next_zone_below,
        "invalidation_floor": invalidation_floor,
        "next_tp_unfired": next_tp_unfired,
        "fired_drops": fired_drops,
        "position_size": size,
        "pct_from_entry": pct_from_entry,
        "position_closed": state.get("position_closed"),
    }
