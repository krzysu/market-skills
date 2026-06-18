"""position-watchdog — pure evaluator for unified levels + signals.

All functions are pure: they take current price/state + watch config and return
(new alerts, new state). No I/O, no side effects.

A watch has:
  levels: list of price-driven alert rules (stop, tp, drop, recovery, zone, invalidation)
  signals: list of strategy-driven alert rules (L3 strategies with conviction threshold)

levels entries:
  {"type": "stop",         "price": float}                       — alert when price ≤ price
  {"type": "tp",           "price": float, "exit_pct": int}      — alert when price ≥ price
  {"type": "drop",         "pct": float}                          — alert when pct-from-entry ≤ pct
  {"type": "recovery"}                                               — alert after 2 ticks above entry post-drop
  {"type": "zone",         "low": float, "high": float, "label": str, "emoji": str}  — alert on zone entry
  {"type": "invalidation", "below": float}                        — alert when price < below
"""

import datetime as _dt


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def _fmt_pct(pct: float) -> str:
    sign = "−" if pct < 0 else "+"
    return f"{sign}{abs(pct):.1f}%"


def _tp_qty(size, exit_pct, name) -> str:
    if size is None or exit_pct is None:
        return ""
    return f"{size * exit_pct / 100:.2f} {name}"


def evaluate_levels(watch: dict, current_price: float, prev_state: dict | None) -> tuple[list[str], dict]:
    """Walk all levels in the watch and emit alerts on state changes. Returns (alerts, new_state)."""
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

    alerts: list[str] = []
    new_alerted = dict(alerted)
    new_streak = above_streak

    above_entry = entry is not None and current_price > float(entry)

    for level in levels:
        level_type = level.get("type")
        level_id = _level_id(level)

        if level_type == "stop":
            stop = float(level["price"])
            if current_price <= stop and alerted.get(level_id) != "fired":
                alerts.append(f"🔴 STOP BREACHED at €{current_price:.2f} (stop €{stop:.2f}). Verify fill manually.")
                new_alerted[level_id] = "fired"

        elif level_type == "tp":
            tp_price = float(level["price"])
            exit_pct = level.get("exit_pct")
            if current_price >= tp_price and alerted.get(level_id) != "fired":
                qty = _tp_qty(size, exit_pct, name)
                if exit_pct is not None and size is not None:
                    alerts.append(
                        f"✅ TP hit (€{tp_price:.2f}). RECOMMEND: sell {qty} (~{exit_pct}%). Manual confirm required."
                    )
                else:
                    alerts.append(f"✅ TP hit (€{tp_price:.2f}). RECOMMEND: partial exit. Manual confirm required.")
                new_alerted[level_id] = "fired"

        elif level_type == "drop":
            if entry is None:
                continue
            pct_threshold = float(level["pct"])
            pct_from_entry = (current_price - float(entry)) / float(entry) * 100
            if pct_from_entry <= pct_threshold and alerted.get(level_id) != "fired":
                alerts.append(
                    f"{'🔶' if pct_threshold <= -10 else '🟡'} {_fmt_pct(pct_threshold)} from entry. "
                    f"Current €{current_price:.2f}, entry €{float(entry):.2f}."
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
                    alerts.append(f"🟢 recovered above entry. Current €{current_price:.2f}.")
                    new_alerted[recovery_id] = "fired"
            else:
                new_streak = 0

        elif level_type == "zone":
            low = float(level["low"])
            high = float(level.get("high", float("inf")))
            label = level.get("label", f"zone €{low:.2f}–€{high:.2f}")
            emoji = level.get("emoji", "🎯")
            in_zone = low <= current_price <= high
            was_in_zone = prev_price is not None and low <= prev_price <= high
            if in_zone and not was_in_zone:
                alerts.append(f"{emoji} {label} — {name} @ €{current_price:.2f}.")
                new_alerted[level_id] = "fired"

        elif level_type == "invalidation":
            below = float(level["below"])
            if current_price < below and alerted.get(level_id) != "fired":
                alerts.append(
                    f"🔴 INVALIDATION — Thesis dead. {name} @ €{current_price:.2f}. "
                    f"Stop loss triggered below €{below:.2f}. Do not average down."
                )
                new_alerted[level_id] = "fired"

    if entry is not None and not above_entry:
        new_streak = 0

    new_state = {
        "alerted_levels": new_alerted,
        "above_entry_streak": new_streak,
        "prev_price": current_price,
    }
    return alerts, new_state


def evaluate_signals(
    watch: dict,
    l3_ideas_by_strategy: dict,
    prev_state: dict | None,
    now: _dt.datetime | None = None,
) -> tuple[list[str], dict]:
    """Walk signal blocks, alert on L3 strategy ideas meeting conviction + cooldown. Returns (alerts, new_state).

    l3_ideas_by_strategy: {strategy_name: [TradeIdea, ...]} filtered to this watch's provider/ticker.
    """
    signals = watch.get("signals", [])
    if not signals:
        return [], {}

    state = prev_state or {}
    last_alert_at: dict = state.get("last_signal_alert_at", {})

    now = now or _dt.datetime.now(_dt.UTC)

    alerts: list[str] = []
    new_last = dict(last_alert_at)

    for sg in signals:
        strategies = sg.get("strategies", [])
        min_conv = int(sg.get("min_conviction", 3))
        cooldown_hours = float(sg.get("cooldown_hours", 0))

        for strat in strategies:
            for idea in l3_ideas_by_strategy.get(strat, []):
                direction = idea.get("direction", "")
                conviction = int(idea.get("conviction", 0))
                entry_p = idea.get("entry_price")
                stop_p = idea.get("stop_loss")

                if conviction < min_conv:
                    continue

                key = f"{strat}:{direction}"
                prior_ts = last_alert_at.get(key)
                if prior_ts:
                    try:
                        prior_dt = _dt.datetime.fromisoformat(prior_ts)
                        if (now - prior_dt).total_seconds() < cooldown_hours * 3600:
                            continue
                    except ValueError:
                        pass

                entry_str = f"€{entry_p:.2f}" if entry_p is not None else "n/a"
                stop_str = f"€{stop_p:.2f}" if stop_p is not None else "n/a"
                alerts.append(f"🎯 {strat} {direction.upper()} conv={conviction}. Entry {entry_str}, stop {stop_str}.")
                new_last[key] = now.isoformat()

    new_state = {"last_signal_alert_at": new_last}
    return alerts, new_state


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
