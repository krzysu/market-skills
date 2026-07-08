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
"""

import datetime as _dt


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
      fired_drops (list[dict]), position_size, pct_from_entry.
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
    }
