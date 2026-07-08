"""position-watchdog — text rendering for evaluator events.

Pure: takes an event dict (output of ``lib.evaluate_*``) plus a context
dict (caller-supplied; see ``ctx`` shape below) and returns the alert
string. No I/O, no datetime.now().

Three render styles are registered in ``FORMATTERS``:

  compact  — legacy one-liners, back-compat with the pre-formatter output
  default  — richer multi-line output (the new preferred default)
  verbose  — default + reasoning + source_skills line on signal events

The ``format_event`` entry point dispatches to the style selected by
``ctx["format_style"]`` and returns ``None`` for unrenderable events so
the wrapper can filter them out.

ctx shape (built by run.py from the watch + tick state):

  {
      "name": str,                       # watch name
      "price": float,                    # last close in monitor's quote
      "primary_quote": str,              # monitor's quote — "USD" | "EUR" | "GBP" | ...
      "monitor_provider": str,           # "kraken:HYPEUSD"
      "format_style": str,               # "default" | "compact" | "verbose"
  }

All prices in every event dict (``current_price``, ``stop_price``,
``tp_price``, ``entry_price``, ``below_price``, ``low``, ``high``,
``stop_loss``, ``take_profit``, ``entry_range``) are in the monitor's
quote — they came from candles fetched via ``monitor_provider``. The
formatter renders them with the symbol for ``primary_quote``.

The library renders a single-currency alert — see SKILL.md
"Single-currency alert rendering" for the rationale.
"""

_QUOTE_SYMBOLS = {
    "EUR": "€",
    "USD": "$",
    "USDT": "$",
    "USDC": "$",
    "GBP": "£",
    "JPY": "¥",
}


def _symbol_for(quote: str) -> str:
    """Symbol for a quote currency. Falls back to € for unknown quotes."""
    return _QUOTE_SYMBOLS.get(quote, "€")


def _primary_symbol(ctx: dict) -> str:
    return _symbol_for(ctx.get("primary_quote", "EUR"))


def _fmt_price(price: float, ctx: dict) -> str:
    """Render a single price in the monitor's quote (e.g. ``$48.00``)."""
    return f"{_primary_symbol(ctx)}{price:.2f}"


def _fmt_live(price: float, ctx: dict) -> str:
    """Render the live price in the monitor's quote (e.g. ``$48.00``).

    Library renders single-currency only. Consumers wanting dual-currency
    displays (``$X / €Y``) should fork the formatter or post-process the
    rendered strings.
    """
    return f"{_primary_symbol(ctx)}{price:.2f}"


def _fmt_pct(pct: float) -> str:
    """Render a percentage with the proper Unicode minus sign (``−``, not ASCII ``-``)."""
    sign = "−" if pct < 0 else "+"
    return f"{sign}{abs(pct):.1f}%"


def _fmt_tp_qty(size, exit_pct, name) -> str:
    """Render a TP quantity like ``"0.55 HYPE"`` (empty string if inputs missing)."""
    if size is None or exit_pct is None:
        return ""
    return f"{size * exit_pct / 100:.2f} {name}"


def _ctx_name(ctx: dict) -> str:
    return ctx.get("name", "?")


def _event_primary(event: dict, key: str) -> float | None:
    v = event.get(key)
    return float(v) if v is not None else None


def _signal_rr(event: dict) -> float | None:
    """Risk:Reward from mid-TP vs entry/stop. None when not computable."""
    entry = event.get("entry_price")
    stop = event.get("stop_loss")
    tps = event.get("take_profit") or []
    if entry is None or stop is None or not tps:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    mid = sum(tps) / len(tps)
    return abs(mid - entry) / risk


def _signal_tp_count(event: dict) -> int:
    return len(event.get("take_profit") or [])


def _format_stop(event: dict, ctx: dict) -> str:
    now = _fmt_live(event["current_price"], ctx)
    stop = _fmt_price(event["stop_price"], ctx)
    return f"🔴 STOP BREACHED at {now} (stop {stop}). Verify fill manually."


def _format_tp(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    tp_str = _fmt_price(event["tp_price"], ctx)
    qty = _fmt_tp_qty(event.get("position_size"), event.get("exit_pct"), name)
    if event.get("exit_pct") is not None and event.get("position_size") is not None:
        return f"✅ TP hit ({tp_str}). RECOMMEND: sell {qty} (~{event['exit_pct']}%). Manual confirm required."
    return f"✅ TP hit ({tp_str}). RECOMMEND: partial exit. Manual confirm required."


def _format_drop(event: dict, ctx: dict) -> str:
    emoji = "🔶" if event["severity"] == "critical" else "🟡"
    now = _fmt_live(event["current_price"], ctx)
    entry = _fmt_price(event["entry_price"], ctx)
    return f"{emoji} {_fmt_pct(event['threshold_pct'])} from entry. Current {now}, entry {entry}."


def _format_recovery(event: dict, ctx: dict) -> str:
    now = _fmt_live(event["current_price"], ctx)
    return f"🟢 recovered above entry. Current {now}."


def _format_zone(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    return f"{event['emoji']} {event['label']} — {name} @ {now}."


def _format_invalidation(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    below = _fmt_price(event["below_price"], ctx)
    return f"🔴 INVALIDATION — Thesis dead. {name} @ {now}. Stop loss triggered below {below}. Do not average down."


def _format_signal(event: dict, ctx: dict) -> str:
    strategy = event["strategy"]
    direction = event["direction"].upper()
    conv = event["conviction"]
    entry = event.get("entry_price")
    stop = event.get("stop_loss")
    entry_str = _fmt_price(entry, ctx) if entry is not None else "n/a"
    stop_str = _fmt_price(stop, ctx) if stop is not None else "n/a"
    return f"🎯 {strategy} {direction} conv={conv}. Entry {entry_str}, stop {stop_str}."


def format_as_default_stop(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    stop = _fmt_price(event["stop_price"], ctx)
    return f"🔴 STOP BREACHED — {name}. Now {now}. Stop at {stop}."


def format_as_default_tp(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    tp = _fmt_price(event["tp_price"], ctx)
    qty = _fmt_tp_qty(event.get("position_size"), event.get("exit_pct"), name)
    pct = event.get("exit_pct")
    exit_str = f"{pct}% ({qty})" if pct is not None else qty
    return f"✅ TP HIT — {name}. Now {now}. TP at {tp}. Exit {exit_str}."


def format_as_default_drop(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    entry = _fmt_price(event["entry_price"], ctx)
    pct = _fmt_pct(event["pct_from_entry"])
    if event["severity"] == "critical":
        return f"🔶 DEEP DROP — {name}. Now {now} ({pct} from entry {entry})."
    return f"🟡 DROP WARNING — {name}. Now {now} ({pct} from entry {entry})."


def format_as_default_recovery(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    entry = _fmt_price(event["entry_price"], ctx)
    return f"🟢 RECOVERED — {name}. Now {now}. Back above entry {entry}."


def format_as_default_zone(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    return f"{event['emoji']} ZONE ENTRY — {event['label']}. {name} now {now}."


def format_as_default_invalidation(event: dict, ctx: dict) -> str:
    name = _ctx_name(ctx)
    now = _fmt_live(event["current_price"], ctx)
    below = _fmt_price(event["below_price"], ctx)
    return f"🔴 INVALIDATED — {name}. Now {now}. Below invalidation {below}. Thesis dead."


def format_as_default_signal(event: dict, ctx: dict) -> str:
    strategy = event["strategy"]
    direction = event["direction"].upper()
    conv = event["conviction"]
    entry = event.get("entry_price")
    stop = event.get("stop_loss")
    tps = event.get("take_profit") or []
    entry_type = event.get("entry_type", "limit") or "limit"

    lines: list[str] = [f"🎯 {strategy} {direction} conv={conv}."]

    if entry is not None and stop is not None:
        entry_str = _fmt_price(entry, ctx)
        stop_str = _fmt_price(stop, ctx)
        risk_pct = abs(entry - stop) / entry * 100 if entry else 0
        lines.append(f"  Entry {entry_str} ({entry_type}, current). Stop {stop_str} (-{risk_pct:.1f}%).")

        if tps:
            tp_parts: list[str] = []
            risk = abs(entry - stop)
            for tp in tps:
                tp_str = _fmt_price(tp, ctx)
                if risk > 0:
                    r = abs(tp - entry) / risk
                    tp_parts.append(f"{tp_str} ({r:.1f}R)")
                else:
                    tp_parts.append(tp_str)
            lines.append("  TP " + " · ".join(tp_parts) + ".")

        rr = _signal_rr(event)
        if rr is not None:
            lines.append(f"  R:R {rr:.2f}:1 mid.")

    return "\n".join(lines)


def format_as_verbose_signal(event: dict, ctx: dict) -> str:
    """Verbose = default + reasoning + source_skills line."""
    base = format_as_default_signal(event, ctx)
    extras: list[str] = []
    if event.get("reasoning"):
        extras.append(f"  Why: {event['reasoning']}")
    if event.get("source_skills"):
        extras.append("  Sources: " + ", ".join(event["source_skills"]))
    if extras:
        return base + "\n" + "\n".join(extras)
    return base


def format_event(event: dict, ctx: dict) -> str | None:
    """Render one event using the style selected by ``ctx['format_style']``."""
    style = ctx.get("format_style", "default")
    formatter = FORMATTERS.get(style, format_as_default)
    return formatter(event, ctx)


def format_alerts(events: list[dict], ctx: dict) -> list[str]:
    """Render a batch of events. Drops ``None`` results (skipped)."""
    out: list[str] = []
    for ev in events:
        rendered = format_event(ev, ctx)
        if rendered is not None:
            out.append(rendered)
    return out


_COMPACT_FORMATTERS = {
    "stop": _format_stop,
    "tp": _format_tp,
    "drop": _format_drop,
    "recovery": _format_recovery,
    "zone": _format_zone,
    "invalidation": _format_invalidation,
    "signal": _format_signal,
}


_DEFAULT_FORMATTERS = {
    "stop": format_as_default_stop,
    "tp": format_as_default_tp,
    "drop": format_as_default_drop,
    "recovery": format_as_default_recovery,
    "zone": format_as_default_zone,
    "invalidation": format_as_default_invalidation,
    "signal": format_as_default_signal,
}


def format_as_default(event: dict, ctx: dict) -> str | None:
    """Dispatch by event ``type`` to the default-style formatter."""
    fn = _DEFAULT_FORMATTERS.get(event.get("type"))
    return fn(event, ctx) if fn else None


def format_as_compact(event: dict, ctx: dict) -> str | None:
    """Dispatch by event ``type`` to the legacy one-liner formatter."""
    fn = _COMPACT_FORMATTERS.get(event.get("type"))
    return fn(event, ctx) if fn else None


def format_as_verbose(event: dict, ctx: dict) -> str | None:
    """Verbose = default, but signals append reasoning + source_skills."""
    if event.get("type") == "signal":
        return format_as_verbose_signal(event, ctx)
    return format_as_default(event, ctx)


FORMATTERS: dict[str, "callable"] = {
    "default": format_as_default,
    "compact": format_as_compact,
    "verbose": format_as_verbose,
}


def _status_zone_range(zone: dict, ctx: dict) -> str:
    """Render a zone range like ``$7.50–$9.00`` (or ``$7.50–∞`` when high is inf)."""
    low = _fmt_price(zone["low"], ctx)
    high = zone["high"]
    if high == float("inf"):
        return f"{low}–∞"
    return f"{low}–{_fmt_price(high, ctx)}"


def _status_zone_block(event: dict, ctx: dict) -> str:
    """Render the zone-attribution chunk of a status line.

    Examples (Chinese-style separators stripped):
      '🟡 T2 wait zone (no add) — above T1 add ($7.50–$9.00)'
      'no active zone'
      'below all zones'
    """
    active = event.get("active_zone")
    next_below = event.get("next_zone_below")
    if active is not None:
        head = f"{active['emoji']} {active['label']}"
        if next_below is not None:
            tail = f"above {next_below['label']} ({_status_zone_range(next_below, ctx)})"
            return f"{head} — {tail}"
        return head
    if next_below is not None:
        return "below all zones"
    return "no active zone"


def _status_invalidation_block(floor: float | None, ctx: dict) -> str:
    if floor is None:
        return ""
    return f"invalid <{_fmt_price(floor, ctx)}"


def _status_drop_block(fired: list[dict]) -> str:
    if not fired:
        return ""
    parts = [_fmt_pct(d["pct"]) for d in fired]
    return f"drop {', '.join(parts)} fired"


def _status_pct_block(
    *,
    entry: float | None,
    pct: float | None,
    current_price: float | None,
    prev_price: float | None,
    ctx: dict,
) -> str:
    if entry is None:
        return ""
    if pct is not None:
        return f"{_fmt_pct(pct)} from entry {_fmt_price(entry, ctx)}"
    ref = current_price if current_price is not None else prev_price
    if ref is not None:
        return f"(no live price; using last known {_fmt_price(ref, ctx)})"
    return "(entry defined, no current price)"


def format_as_default_status(event: dict, ctx: dict) -> str:
    """Render one watch's current state as a single line.

    Compose in order: name + live price + zone attribution + invalidation
    + most-recent fired drop thresholds + pct-from-entry + above-entry
    streak. Pure function; not registered in ``FORMATTERS`` (which
    dispatches by event ``type`` for transition alerts — status mode has
    no event type, so the dispatcher has nothing to dispatch on).
    """
    name = event["name"]
    price = event.get("current_price")
    live = _fmt_live(price, ctx) if price is not None else "<fetch failed>"

    middle_parts = [
        s
        for s in (
            _status_zone_block(event, ctx),
            _status_invalidation_block(event.get("invalidation_floor"), ctx),
            _status_drop_block(event.get("fired_drops") or []),
        )
        if s
    ]
    middle = "; ".join(middle_parts)

    tail = _status_pct_block(
        entry=event.get("entry_price"),
        pct=event.get("pct_from_entry"),
        current_price=event.get("current_price"),
        prev_price=event.get("prev_price"),
        ctx=ctx,
    )

    streak = int(event.get("above_entry_streak", 0) or 0)
    streak_suffix = f"; above entry streak={streak}" if streak > 0 else ""

    if middle and tail:
        return f"[{name}] @ {live} | {middle} | {tail}{streak_suffix}"
    if middle:
        return f"[{name}] @ {live} | {middle}{streak_suffix}"
    return f"[{name}] @ {live}{streak_suffix}"
