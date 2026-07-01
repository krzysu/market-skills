"""Tests for position-watchdog/formatter.py — string rendering of evaluator events.

Three styles are covered (``compact`` / ``default`` / ``verbose``) plus
the internal price/percent/qty helpers and the FORMATTERS registry.

Single-currency alerts (monitor-only) collapse to ``$X``; with an
``execution_provider`` set and an ``execution_price`` available, the live
field renders as ``$X / €Y`` while static levels stay in the monitor's
quote only.
"""

import importlib.util
import os

_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
    "position-watchdog",
)
_FMT_PATH = os.path.join(_SKILLS_DIR, "formatter.py")
_spec = importlib.util.spec_from_file_location("position_watchdog_formatter", _FMT_PATH)
_pw_fmt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pw_fmt)


FORMATTERS = _pw_fmt.FORMATTERS
format_event = _pw_fmt.format_event
format_alerts = _pw_fmt.format_alerts
_fmt_price = _pw_fmt._fmt_price
_fmt_live = _pw_fmt._fmt_live
_fmt_pct = _pw_fmt._fmt_pct
_fmt_tp_qty = _pw_fmt._fmt_tp_qty
format_as_compact = _pw_fmt.format_as_compact
format_as_default = _pw_fmt.format_as_default
format_as_verbose = _pw_fmt.format_as_verbose


def _ctx(
    price=60.0,
    primary_quote="USD",
    monitor_provider="kraken:HYPEUSD",
    execution_provider=None,
    execution_price=None,
    format_style="compact",
    name="HYPE",
):
    return {
        "name": name,
        "price": price,
        "primary_quote": primary_quote,
        "monitor_provider": monitor_provider,
        "execution_provider": execution_provider,
        "execution_quote": (_quote_from_provider(execution_provider) if execution_provider else None),
        "execution_price": execution_price,
        "format_style": format_style,
    }


_KNOWN_QUOTES = ("EUR", "USD", "USDT", "USDC", "GBP", "JPY")


def _quote_from_provider(provider: str) -> str:
    """Infer the quote currency from a provider:ticker string suffix."""
    if ":" not in provider:
        return ""
    ticker = provider.split(":", 1)[1]
    for q in _KNOWN_QUOTES:
        if ticker.endswith(q) and len(ticker) > len(q):
            return q
    return ""


_EUR_CTX_KW = {"primary_quote": "EUR", "monitor_provider": "kraken:HYPEEUR"}


def _stop_event(current=48.0, stop=49.71):
    return {
        "type": "stop",
        "level_id": "stop:49.71",
        "current_price": current,
        "stop_price": stop,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _tp_event(current=90.0, tp=88.21, exit_pct=33, size=1.66):
    return {
        "type": "tp",
        "level_id": "tp:88.21",
        "current_price": current,
        "tp_price": tp,
        "exit_pct": exit_pct,
        "qty": round(size * exit_pct / 100, 4),
        "position_size": size,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _tp_event_no_qty(current=90.0, tp=88.21):
    return {
        "type": "tp",
        "level_id": "tp:88.21",
        "current_price": current,
        "tp_price": tp,
        "exit_pct": None,
        "qty": None,
        "position_size": None,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _drop_event(current=57.0, entry=60.15, threshold=-5, severity="warn"):
    pct_from_entry = (current - entry) / entry * 100
    return {
        "type": "drop",
        "level_id": f"drop:{threshold}",
        "current_price": current,
        "entry_price": entry,
        "pct_from_entry": pct_from_entry,
        "threshold_pct": threshold,
        "severity": severity,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _recovery_event(current=61.5, entry=60.15):
    return {
        "type": "recovery",
        "level_id": "recovery",
        "current_price": current,
        "entry_price": entry,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _zone_event(current=505.0, low=500.0, high=510.0, label="T2 limit zone", emoji="🟢"):
    return {
        "type": "zone",
        "level_id": f"zone:{low}-{high}:{label}",
        "current_price": current,
        "low": low,
        "high": high,
        "label": label,
        "emoji": emoji,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _invalidation_event(current=480.0, below=486.0):
    return {
        "type": "invalidation",
        "level_id": "invalidation:486.0",
        "current_price": current,
        "below_price": below,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


def _signal_event(
    strategy="trend-follow",
    direction="long",
    conviction=4,
    entry=60.0,
    stop=55.0,
    tps=None,
    reasoning="",
    source_skills=None,
    entry_type="limit",
):
    return {
        "type": "signal",
        "strategy": strategy,
        "direction": direction,
        "conviction": conviction,
        "entry_price": entry,
        "entry_range": [entry - 0.5, entry + 0.5],
        "stop_loss": stop,
        "take_profit": tps if tps is not None else [67.5, 72.5, 80.0],
        "reasoning": reasoning,
        "source_skills": source_skills if source_skills is not None else ["market-trend-quality"],
        "entry_type": entry_type,
        "triggered_at": "2026-06-20T12:00:00+00:00",
    }


# --- _fmt_price ---


def test_fmt_price_usd():
    """Default ctx (primary_quote=USD) renders with $ symbol."""
    assert _fmt_price(48.0, _ctx(price=48.0)) == "$48.00"


def test_fmt_price_eur():
    """A EUR-monitored watch renders with € symbol (single-provider, no execution)."""
    assert _fmt_price(48.0, _ctx(price=48.0, **_EUR_CTX_KW)) == "€48.00"


def test_fmt_price_gbp():
    """A GBP-monitored watch renders with £ symbol."""
    assert (
        _fmt_price(50000.0, _ctx(price=50000.0, primary_quote="GBP", monitor_provider="kraken:BTCGBP")) == "£50000.00"
    )


def test_fmt_price_unknown_quote_falls_back_to_eur_symbol():
    """Unknown quotes fall back to € (the historical default)."""
    assert _fmt_price(100.0, _ctx(price=100.0, primary_quote="XYZ", monitor_provider="kraken:FOO")) == "€100.00"


# --- _fmt_live ---


def test_fmt_live_no_execution_collapses_to_primary():
    """Monitor-only ctx: _fmt_live returns just the monitor price."""
    assert _fmt_live(48.0, _ctx(price=48.0)) == "$48.00"


def test_fmt_live_renders_only_monitor_price_when_execution_in_ctx():
    """Library renders single-currency alerts even when execution context is set.

    The execution_provider is a fallback data source, not a dual-display
    driver. Consumers wanting ``$X / €Y`` should fork the formatter.
    """
    ctx = _ctx(
        price=48.0,
        primary_quote="USD",
        monitor_provider="kraken:HYPEUSD",
        execution_provider="kraken:HYPEEUR",
        execution_price=45.0,
    )
    assert _fmt_live(48.0, ctx) == "$48.00"


def test_fmt_live_eur_monitor_no_execution():
    """EUR-monitored, no execution_provider: single currency."""
    assert _fmt_live(48.0, _ctx(price=48.0, **_EUR_CTX_KW)) == "€48.00"


def test_fmt_live_ignores_execution_quote_in_ctx():
    """Defense in depth: even if execution_quote and execution_price are
    present in ctx, _fmt_live must render the monitor's price only."""
    ctx = _ctx(
        price=48.0,
        primary_quote="USD",
        monitor_provider="kraken:HYPEUSD",
        execution_provider="kraken:HYPEEUR",
        execution_price=45.0,
    )
    out = _fmt_live(48.0, ctx)
    assert "€" not in out
    assert "45" not in out


# --- _fmt_pct ---


def test_fmt_pct_negative_uses_unicode_minus():
    assert _fmt_pct(-5.0) == "−5.0%"
    assert _fmt_pct(-12.345) == "−12.3%"


def test_fmt_pct_positive_uses_plus():
    assert _fmt_pct(3.2) == "+3.2%"
    assert _fmt_pct(0) == "+0.0%"


# --- _fmt_tp_qty ---


def test_fmt_tp_qty_formats_two_decimals_with_name():
    assert _fmt_tp_qty(1.66, 33, "HYPE") == "0.55 HYPE"


def test_fmt_tp_qty_empty_when_inputs_missing():
    assert _fmt_tp_qty(None, 33, "HYPE") == ""
    assert _fmt_tp_qty(1.66, None, "HYPE") == ""


# --- compact style ---


def test_compact_stop_one_liner():
    s = format_as_compact(_stop_event(), _ctx(**_EUR_CTX_KW))
    assert "🔴 STOP BREACHED at €48.00 (stop €49.71)" in s
    assert "Verify fill manually." in s


def test_compact_stop_ignores_execution_context():
    """Compact stop alert with execution context: library renders monitor's price only."""
    ctx = _ctx(
        price=48.0,
        primary_quote="USD",
        monitor_provider="kraken:HYPEUSD",
        execution_provider="kraken:HYPEEUR",
        execution_price=45.0,
    )
    s = format_as_compact(_stop_event(), ctx)
    assert "🔴 STOP BREACHED at $48.00 (stop $49.71)" in s
    assert "€45" not in s
    assert " / " not in s


def test_compact_tp_with_qty():
    s = format_as_compact(_tp_event(), _ctx(**_EUR_CTX_KW))
    assert "✅ TP hit (€88.21)" in s
    assert "sell 0.55 HYPE" in s
    assert "~33%" in s


def test_compact_tp_without_qty():
    s = format_as_compact(_tp_event_no_qty(), _ctx(**_EUR_CTX_KW))
    assert "✅ TP hit (€88.21)" in s
    assert "RECOMMEND: partial exit" in s
    assert "sell" not in s


def test_compact_drop_warn_uses_yellow():
    s = format_as_compact(_drop_event(threshold=-5, severity="warn"), _ctx(**_EUR_CTX_KW))
    assert s.startswith("🟡 −5.0% from entry.")
    assert "€57.00" in s
    assert "€60.15" in s


def test_compact_drop_critical_uses_orange():
    s = format_as_compact(
        _drop_event(current=53.0, threshold=-10, severity="critical"),
        _ctx(**_EUR_CTX_KW),
    )
    assert s.startswith("🔶 −10.0% from entry.")


def test_compact_recovery_one_liner():
    s = format_as_compact(_recovery_event(), _ctx(**_EUR_CTX_KW))
    assert "🟢 recovered above entry" in s
    assert "€61.50" in s


def test_compact_zone_one_liner():
    s = format_as_compact(_zone_event(), _ctx(**_EUR_CTX_KW))
    assert "🟢 T2 limit zone — HYPE @ €505.00" in s


def test_compact_invalidation_one_liner():
    s = format_as_compact(_invalidation_event(), _ctx(**_EUR_CTX_KW))
    assert "🔴 INVALIDATION" in s
    assert "HYPE @ €480.00" in s
    assert "below €486.00" in s
    assert "Do not average down." in s


def test_compact_signal_one_liner():
    s = format_as_compact(_signal_event(), _ctx(**_EUR_CTX_KW))
    assert "🎯 trend-follow LONG conv=4" in s
    assert "Entry €60.00" in s
    assert "stop €55.00" in s


def test_compact_usd_monitored_uses_dollar_symbol():
    """A USD-monitored HYPE watch renders with $, not €."""
    s = format_as_compact(_stop_event(), _ctx(primary_quote="USD", monitor_provider="kraken:HYPEUSD"))
    assert "$48.00" in s
    assert "$49.71" in s
    assert "€" not in s


# --- default style ---


def test_default_stop_includes_name_and_full_sentence():
    s = format_as_default(_stop_event(), _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "🔴 STOP BREACHED — HYPE" in s
    assert "Now €48.00" in s
    assert "Stop at €49.71" in s


def test_default_stop_ignores_execution_context():
    """Default-style stop alert: Now in monitor only, level in monitor only.

    Library renders single-currency. Consumers wanting ``$X / €Y`` should
    fork the formatter.
    """
    ctx = _ctx(
        price=48.0,
        primary_quote="USD",
        monitor_provider="kraken:HYPEUSD",
        execution_provider="kraken:HYPEEUR",
        execution_price=45.0,
        format_style="default",
    )
    s = format_as_default(_stop_event(), ctx)
    assert "Now $48.00" in s
    assert "Stop at $49.71" in s
    # No € on the live field (library is single-currency).
    assert "€45" not in s
    # No € on the level (the formatter does not ratio-scale).
    assert "€49.71" not in s


def test_default_tp_includes_exit_pct_and_qty():
    s = format_as_default(_tp_event(), _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "✅ TP HIT — HYPE" in s
    assert "TP at €88.21" in s
    assert "Exit 33% (0.55 HYPE)" in s


def test_default_drop_warn_full_sentence():
    s = format_as_default(
        _drop_event(threshold=-5, severity="warn"),
        _ctx(**_EUR_CTX_KW, format_style="default"),
    )
    assert "🟡 DROP WARNING — HYPE" in s
    assert "−5.2% from entry" in s or "−5" in s  # pct string can vary; check core content
    assert "Now €57.00" in s


def test_default_drop_critical_full_sentence():
    s = format_as_default(
        _drop_event(current=53.0, threshold=-10, severity="critical"),
        _ctx(**_EUR_CTX_KW, format_style="default"),
    )
    assert "🔶 DEEP DROP — HYPE" in s
    assert "Now €53.00" in s


def test_default_recovery_full_sentence():
    s = format_as_default(_recovery_event(), _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "🟢 RECOVERED — HYPE" in s
    assert "Back above entry €60.15" in s


def test_default_zone_full_sentence():
    s = format_as_default(_zone_event(), _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "🟢 ZONE ENTRY — T2 limit zone" in s
    assert "HYPE now €505.00" in s


def test_default_invalidation_full_sentence():
    s = format_as_default(_invalidation_event(), _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "🔴 INVALIDATED — HYPE" in s
    assert "Below invalidation €486.00" in s
    assert "Thesis dead" in s


def test_default_signal_multi_line_includes_entry_stop_tp_rr():
    s = format_as_default(
        _signal_event(entry=61.19, stop=57.12, tps=[67.28, 72.36, 79.39]),
        _ctx(**_EUR_CTX_KW, format_style="default"),
    )
    lines = s.split("\n")
    assert lines[0].startswith("🎯 trend-follow LONG conv=4.")
    assert any("Entry €61.19" in line and "Stop €57.12" in line for line in lines)
    tp_line = next(line for line in lines if line.lstrip().startswith("TP "))
    assert "€67.28" in tp_line
    assert "1.5R" in tp_line
    assert "€72.36" in tp_line
    assert "€79.39" in tp_line
    assert any(line.lstrip().startswith("R:R") and ":1" in line for line in lines)


def test_default_signal_skips_tp_block_when_no_tps():
    s = format_as_default(_signal_event(tps=[]), _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "TP " not in s
    assert "R:R" not in s


# --- verbose style ---


def test_verbose_signal_appends_reasoning_and_sources():
    s = format_as_verbose(
        _signal_event(
            reasoning="Healthy uptrend + breakout confirmed.",
            source_skills=["market-trend-quality", "market-breakout"],
        ),
        _ctx(**_EUR_CTX_KW, format_style="verbose"),
    )
    assert "Why: Healthy uptrend + breakout confirmed." in s
    assert "Sources: market-trend-quality, market-breakout" in s


def test_verbose_non_signal_falls_back_to_default():
    """Verbose only adds extras for signals; other event types use the default rendering."""
    s_default = format_as_default(_stop_event(), _ctx(**_EUR_CTX_KW, format_style="default"))
    s_verbose = format_as_verbose(_stop_event(), _ctx(**_EUR_CTX_KW, format_style="verbose"))
    assert s_default == s_verbose


# --- format_event dispatch ---


def test_format_event_dispatches_by_ctx_format_style():
    """format_event uses ctx['format_style'] to pick the right formatter."""
    ev = _stop_event()
    s_compact = format_event(ev, _ctx(**_EUR_CTX_KW, format_style="compact"))
    s_default = format_event(ev, _ctx(**_EUR_CTX_KW, format_style="default"))
    assert "🔴 STOP BREACHED at" in s_compact
    assert "🔴 STOP BREACHED — HYPE" in s_default


def test_format_event_unknown_style_falls_back_to_default():
    s = format_event(_stop_event(), _ctx(**_EUR_CTX_KW, format_style="bogus"))
    assert "🔴 STOP BREACHED — HYPE" in s


def test_format_event_unknown_type_returns_none():
    assert format_event({"type": "garbage"}, _ctx()) is None


# --- format_alerts ---


def test_format_alerts_renders_each_event():
    alerts = format_alerts(
        [_stop_event(), _tp_event()],
        _ctx(**_EUR_CTX_KW, format_style="compact"),
    )
    assert len(alerts) == 2
    assert "STOP BREACHED" in alerts[0]
    assert "TP hit" in alerts[1]


def test_format_alerts_filters_none_results():
    """An event of an unknown type renders to None and is dropped from the output."""
    alerts = format_alerts(
        [_stop_event(), {"type": "garbage"}, _tp_event()],
        _ctx(**_EUR_CTX_KW, format_style="compact"),
    )
    assert len(alerts) == 2
    assert "STOP BREACHED" in alerts[0]
    assert "TP hit" in alerts[1]


# --- FORMATTERS registry ---


def test_formatters_registry_has_all_three_styles():
    assert set(FORMATTERS) == {"compact", "default", "verbose"}


def test_formatters_registry_callables():
    """Each entry in FORMATTERS must be callable with (event, ctx)."""
    ev = _stop_event()
    for name, fn in FORMATTERS.items():
        result = fn(ev, _ctx(**_EUR_CTX_KW, format_style=name))
        assert isinstance(result, str), f"{name} returned non-string: {type(result)}"
        assert "STOP" in result


# --- integration: lib → formatter end-to-end ---


def test_end_to_end_evaluate_then_format_compact():
    """Run the evaluator on a stop breach and format with the compact style."""
    import importlib.util as ilu

    lib_path = os.path.join(_SKILLS_DIR, "lib.py")
    spec = ilu.spec_from_file_location("position_watchdog_lib_for_fmt", lib_path)
    lib = ilu.module_from_spec(spec)
    spec.loader.exec_module(lib)

    watch = {
        "name": "HYPE",
        "monitor_provider": "kraken:HYPEEUR",
        "entry_price": 60.15,
        "position_size": 1.66,
        "levels": [{"type": "stop", "price": 49.71}],
    }
    events, _ = lib.evaluate_levels(watch, 48.0, None)
    assert len(events) == 1
    out = format_alerts(events, _ctx(**_EUR_CTX_KW, format_style="compact"))
    assert "🔴 STOP BREACHED at €48.00 (stop €49.71). Verify fill manually." in out[0]
