"""Tests for position-watchdog venue-side stop fill detection (market-skills-f12).

Covers the worked case: a resting venue stop fills entirely venue-side (no
execution-skill call site exists), the watchdog's next tick reports the exit
(position, fill price, filled quantity, realised P&L), marks the watch
``position_closed`` and stops evaluating its levels. Also covers
attribution (stop-family sell orders only), partial fills, dedupe across
ticks, the P&L arithmetic and the suppression switches.

Loading style follows ``test_position_watchdog.py``: ``lib.py`` once at
module scope, ``scripts/run.py`` per-test via a unique spec name registered
in ``sys.modules``.
"""

import datetime as dt
import importlib.util
import json
import os
import sys
import time

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS_DIR = os.path.join(_REPO_ROOT, "skills", "position-watchdog")
_LIB_PATH = os.path.join(_SKILLS_DIR, "lib.py")
_FMT_PATH = os.path.join(_SKILLS_DIR, "formatter.py")
_RUN_PATH = os.path.join(_SKILLS_DIR, "scripts", "run.py")

_spec = importlib.util.spec_from_file_location("position_watchdog_venue_lib", _LIB_PATH)
_pw_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pw_lib)

evaluate_venue_stop_fills = _pw_lib.evaluate_venue_stop_fills
venue_pair_matches = _pw_lib.venue_pair_matches
VENUE_FILL_FRESHNESS_GRACE_SECONDS = _pw_lib.VENUE_FILL_FRESHNESS_GRACE_SECONDS

_fmt_spec = importlib.util.spec_from_file_location("position_watchdog_venue_fmt", _FMT_PATH)
_pw_fmt = importlib.util.module_from_spec(_fmt_spec)
_fmt_spec.loader.exec_module(_pw_fmt)


NOW = dt.datetime(2026, 9, 21, 6, 0, 0, tzinfo=dt.UTC)

# A held-file watch: kraken-monitored USD position; the venue fills a stop on
# the EUR pair — base-asset match, different quote (the worked-case shape).
WATCH = {
    "name": "<TICKER>",
    "enabled": True,
    "monitor_provider": "kraken:<TICKER>USD",
    "entry_price": 60.15,
    "position_size": 1.66,
    "levels": [{"type": "stop", "price": 49.71}],
}


def _venue_fill(
    order_id="OCLOSED-1",
    pair="<TICKER>EUR",
    side="sell",
    ordertype="stop-loss",
    vol="1.66",
    vol_exec="1.66",
    price="48.20",
    cost="80.01",
    fee="0.80",
    status="closed",
    # Default models a fill that has just closed and is reported by the next
    # tick. A fixed old epoch would trip the new watch-state-lifetime bound in
    # the end-to-end fixtures (fresh state dir + recent ``now``). Lib-level
    # tests pass explicit state with no creation key, so they are unaffected.
    closetm=None,
):
    ts = closetm if closetm is not None else time.time()
    return {
        "order_id": order_id,
        "pair": pair,
        "side": side,
        "order_type": ordertype,
        "volume": float(vol),
        "filled_volume": float(vol_exec),
        "fill_price": float(price) if price is not None else None,
        "cost": float(cost) if cost is not None else None,
        "fee": float(fee),
        "status": status,
        "opened_at": ts - 3600.0,
        "closed_at": ts,
        "trigger_price": 49.71,
        "limit_price": None,
        "cl_ord_id": None,
        "raw": {},
    }


# ──────────────────────────────────────────────────────── lib: pair matching


def test_venue_pair_matches_base_asset_across_quotes():
    assert venue_pair_matches("kraken:<TICKER>USD", "<TICKER>EUR") is True
    assert venue_pair_matches("kraken:<TICKER>USD", "<TICKER>USD") is True
    assert venue_pair_matches("kraken:BTCUSD", "XBTUSD") is True
    assert venue_pair_matches("kraken:DOGEUSD", "XDGUSD") is True
    assert venue_pair_matches("kraken:BTCUSD", "BTC/USDT") is True
    assert venue_pair_matches("kraken:<TICKER>USD", "ETH<EUR>".replace("ETH", "<OTHER>")) is False
    assert venue_pair_matches("", "<TICKER>EUR") is False
    assert venue_pair_matches("kraken:<TICKER>USD", "") is False


def test_venue_pair_matches_legacy_kraken_pair_forms():
    """Kraken's legacy X/Z-prefixed forms (the repo's canonical pair keys — see
    daily-trade-pick's kraken-pair-lookup reference) must match too: strip the
    Z-infixed quote and normalise the X-prefixed base."""
    assert venue_pair_matches("kraken:BTCUSD", "XXBTZUSD") is True
    assert venue_pair_matches("kraken:ETHUSD", "XETHZEUR") is True
    assert venue_pair_matches("kraken:BTCUSD", "XBTZEUR") is True  # legacy base, plain quote
    assert venue_pair_matches("kraken:BTCUSD", "XETHZEUR") is False
    # The legacy form can also be the monitor's own pair key.
    assert venue_pair_matches("kraken:XXBTZUSD", "BTCUSD") is True
    assert venue_pair_matches("kraken:BTCZUSD", "BTCEUR") is True  # Z-infixed quote on the monitor


# ───────────────────────────────────────────────── lib: candidate attribution


def test_manual_market_and_limit_sells_are_not_reported():
    for ordertype in ("market", "limit"):
        orders = [_venue_fill(ordertype=ordertype)]
        events, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
        assert events == []
        assert state["position_closed"] is None
        assert state["venue_stop_fills"] == {}


def test_buy_side_stop_and_zero_fill_canceled_stop_are_not_reported():
    orders = [
        _venue_fill(order_id="OBUY-1", side="buy"),
        _venue_fill(order_id="OCANCEL-1", vol_exec="0", status="canceled"),
    ]
    events, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert events == []
    assert state["position_closed"] is None
    assert state["venue_stop_fills"] == {}


def test_trailing_stop_variants_are_candidates():
    orders = [_venue_fill(ordertype="trailing-stop-limit")]
    events, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert len(events) == 1
    assert events[0]["order_type"] == "trailing-stop-limit"
    assert state["position_closed"] is not None


def test_non_matching_pair_is_not_reported():
    orders = [_venue_fill(pair="OTHER-EUR")]
    events, _ = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert events == []


# ─────────────────────────────────────────────────── lib: full-closure shape


def test_full_stop_fill_event_shape_and_pnl_arithmetic():
    """Same-quote fill (pair ``<TICKER>USD`` against monitor
    ``kraken:<TICKER>USD``): the P&L arithmetic is exact."""
    orders = [_venue_fill(pair="<TICKER>USD")]
    events, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "venue_stop_fill"
    assert ev["level_id"] == "venue_stop_fill:OCLOSED-1"
    assert ev["name"] == "<TICKER>"
    assert ev["order_id"] == "OCLOSED-1"
    assert ev["pair"] == "<TICKER>USD"
    assert ev["fill_quote"] == "USD"
    assert ev["fill_price"] == pytest.approx(48.20)
    assert ev["filled_volume"] == pytest.approx(1.66)
    assert ev["order_volume"] == pytest.approx(1.66)
    assert ev["position_size"] == pytest.approx(1.66)
    assert ev["entry_price"] == pytest.approx(60.15)
    assert ev["fee"] == pytest.approx(0.80)
    assert ev["partial"] is False
    assert ev["closed_position"] is True
    assert ev["triggered_at"] == NOW.isoformat()
    # (48.20 - 60.15) * 1.66 - 0.80 = -20.637 → -20.64
    assert ev["realised_pnl"] == pytest.approx(-20.64)

    assert state["position_closed"]["order_id"] == "OCLOSED-1"
    assert state["position_closed"]["fill_price"] == pytest.approx(48.20)
    assert state["position_closed"]["filled_volume"] == pytest.approx(1.66)
    assert "reported_at" in state["position_closed"]
    assert state["venue_stop_fills"]["OCLOSED-1"]["closed_position"] is True


def test_cross_quote_fill_pnl_is_honestly_unavailable():
    """Cross-quote fill (``<TICKER>EUR`` against monitor ``kraken:<TICKER>USD``):
    detection still fires (``venue_pair_matches`` is quote-insensitive) but the
    P&L is unavailable — ``entry_price`` is in USD, the fill price in EUR, and
    subtracting across quotes would fabricate a figure. Closure is still
    volume-based and quote-independent."""
    orders = [_venue_fill()]
    events, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert len(events) == 1
    ev = events[0]
    assert ev["pair"] == "<TICKER>EUR"
    assert ev["fill_quote"] == "EUR"
    assert ev["realised_pnl"] is None
    assert ev["closed_position"] is True
    assert state["position_closed"]["order_id"] == "OCLOSED-1"


def test_realised_pnl_none_without_entry_price():
    watch = {k: v for k, v in WATCH.items() if k != "entry_price"}
    events, _ = evaluate_venue_stop_fills(watch, [_venue_fill()], None, now=NOW)
    assert len(events) == 1
    assert events[0]["realised_pnl"] is None
    assert events[0]["closed_position"] is True


def test_no_position_size_still_reports_but_keeps_monitoring():
    watch = {k: v for k, v in WATCH.items() if k != "position_size"}
    events, state = evaluate_venue_stop_fills(watch, [_venue_fill()], None, now=NOW)
    assert len(events) == 1
    assert events[0]["position_size"] is None
    assert events[0]["closed_position"] is False
    assert state["position_closed"] is None


def test_partial_fill_reported_once_and_monitoring_continues():
    orders = [_venue_fill(order_id="OPART-1", vol="0.66", vol_exec="0.33")]
    events, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert len(events) == 1
    ev = events[0]
    assert ev["filled_volume"] == pytest.approx(0.33)
    assert ev["partial"] is True
    assert ev["closed_position"] is False
    assert state["position_closed"] is None
    assert "OPART-1" in state["venue_stop_fills"]

    # Same order id on a later tick: no re-report.
    events2, state2 = evaluate_venue_stop_fills(WATCH, orders, state, now=NOW)
    assert events2 == []
    assert state2["position_closed"] is None


# ───────────────────────────────────────────── lib: state-lifetime time bound


def test_fill_closed_before_watch_state_creation_is_suppressed():
    """A watch whose state was created fresh this morning must not report a
    stop fill that closed three days earlier — the false-alarm shape from
    2026-09-23 (swing-scan recreated the watch, empty ledger, no time
    window, the 2026-09-20 fill re-reported)."""
    state = {
        "watch_state_created_at": NOW.isoformat(),
        "venue_stop_fills": {},
        "position_closed": None,
    }
    old_fill = _venue_fill(closetm=NOW.timestamp() - 3 * 24 * 3600)
    events, new_state = evaluate_venue_stop_fills(WATCH, [old_fill], state, now=NOW)
    assert events == []
    assert new_state["venue_stop_fills"] == {}
    assert new_state["position_closed"] is None
    # The creation anchor is carried forward so the suppression is
    # deterministic on every later tick.
    assert new_state["watch_state_created_at"] == NOW.isoformat()


def test_fill_closed_after_creation_is_reported_and_ledgered():
    state = {
        "watch_state_created_at": NOW.isoformat(),
        "venue_stop_fills": {},
        "position_closed": None,
    }
    fresh_fill = _venue_fill(closetm=NOW.timestamp() + 3600)
    events, new_state = evaluate_venue_stop_fills(WATCH, [fresh_fill], state, now=NOW)
    ids = [e["order_id"] for e in events]
    assert ids == ["OCLOSED-1"]
    assert "OCLOSED-1" in new_state["venue_stop_fills"]
    # Within-lifetime closure still marks the position closed.
    assert new_state["position_closed"]["order_id"] == "OCLOSED-1"


def test_grace_boundary_exactly_at_cutoff_is_suppressed_and_one_second_later_reported():
    state = {
        "watch_state_created_at": NOW.isoformat(),
        "venue_stop_fills": {},
        "position_closed": None,
    }
    boundary = NOW.timestamp() - VENUE_FILL_FRESHNESS_GRACE_SECONDS
    # Exactly at the cutoff (creation minus the grace): suppressed.
    at_cutoff = _venue_fill(order_id="OAT", closetm=boundary)
    events, _ = evaluate_venue_stop_fills(WATCH, [at_cutoff], state, now=NOW)
    assert events == []

    # One second later than that boundary: within the grace, reported.
    just_inside = _venue_fill(order_id="OINSIDE", closetm=boundary + 1.0)
    events2, state2 = evaluate_venue_stop_fills(WATCH, [just_inside], state, now=NOW)
    assert [e["order_id"] for e in events2] == ["OINSIDE"]
    assert "OINSIDE" in state2["venue_stop_fills"]


def test_no_creation_key_still_reports_old_fill_backwards_compatible():
    """Pre-fix state files have no ``watch_state_created_at`` key: no time
    bound applies and the fill remains reportable (the order-id ledger
    dedupes it on later ticks)."""
    state = {"venue_stop_fills": {}, "position_closed": None}
    old_fill = _venue_fill(closetm=NOW.timestamp() - 3 * 24 * 3600)
    events, new_state = evaluate_venue_stop_fills(WATCH, [old_fill], state, now=NOW)
    assert [e["order_id"] for e in events] == ["OCLOSED-1"]
    assert "OCLOSED-1" in new_state["venue_stop_fills"]


def test_undatable_fill_without_closed_at_bypasses_the_bound():
    """An order with ``closed_at=None`` cannot be dated: it is NOT filtered,
    the order-id ledger is its dedupe."""
    state = {
        "watch_state_created_at": NOW.isoformat(),
        "venue_stop_fills": {},
        "position_closed": None,
    }
    undated = _venue_fill(order_id="ONODATE")
    undated["closed_at"] = None
    events, new_state = evaluate_venue_stop_fills(WATCH, [undated], state, now=NOW)
    assert [e["order_id"] for e in events] == ["ONODATE"]
    assert "ONODATE" in new_state["venue_stop_fills"]


def test_unparseable_creation_key_disables_the_bound():
    state = {
        "watch_state_created_at": "not-a-timestamp",
        "venue_stop_fills": {},
        "position_closed": None,
    }
    old_fill = _venue_fill(closetm=NOW.timestamp() - 3 * 24 * 3600)
    events, _ = evaluate_venue_stop_fills(WATCH, [old_fill], state, now=NOW)
    assert [e["order_id"] for e in events] == ["OCLOSED-1"]


def test_carried_creation_key_survives_prior_closure_early_return():
    """The returned state carries ``watch_state_created_at`` through both the
    prior-closure early return and the normal return, so run.py can persist
    it unchanged."""
    created = NOW.isoformat()
    state = {
        "watch_state_created_at": created,
        "venue_stop_fills": {"O1": {"reported_at": created, "closed_position": True}},
        "position_closed": {"order_id": "O1"},
    }
    _, new_state = evaluate_venue_stop_fills(WATCH, [_venue_fill(order_id="O9")], state, now=NOW)
    assert new_state["watch_state_created_at"] == created
    assert new_state["position_closed"]["order_id"] == "O1"

    state2 = {"watch_state_created_at": created, "venue_stop_fills": {}, "position_closed": None}
    _, new_state2 = evaluate_venue_stop_fills(WATCH, [_venue_fill()], state2, now=NOW)
    assert new_state2["watch_state_created_at"] == created


# ──────────────────────────────────────────────────────── lib: dedupe + stickiness


def test_dedupe_across_ticks_new_order_id_reported():
    # Tick 1: partial exit — reported, position stays open.
    orders1 = [_venue_fill(order_id="O1", vol="0.66", vol_exec="0.33")]
    events1, state1 = evaluate_venue_stop_fills(WATCH, orders1, None, now=NOW)
    assert [e["order_id"] for e in events1] == ["O1"]

    # Tick 2: the SAME order id is deduped; a NEW order id fires.
    orders2 = [
        _venue_fill(order_id="O1", vol="0.66", vol_exec="0.33"),
        _venue_fill(order_id="O2", price="47.50"),
    ]
    events2, _ = evaluate_venue_stop_fills(WATCH, orders2, state1, now=NOW)
    assert [e["order_id"] for e in events2] == ["O2"]


def test_position_closed_is_sticky_no_repeat_alerts():
    orders = [_venue_fill(order_id="O1")]
    _, state = evaluate_venue_stop_fills(WATCH, orders, None, now=NOW)
    assert state["position_closed"] is not None

    # Even a NEW stop fill on a later tick produces nothing once closed.
    events, state2 = evaluate_venue_stop_fills(WATCH, [_venue_fill(order_id="O9")], state, now=NOW)
    assert events == []
    assert state2["position_closed"] == state["position_closed"]
    assert state2["venue_stop_fills"] == state["venue_stop_fills"]


# ───────────────────────────────────────────────────────────── formatter


_CTX = {
    "name": "<TICKER>",
    "price": 55.0,
    "primary_quote": "USD",
    "monitor_provider": "kraken:<TICKER>USD",
    "format_style": "default",
}


def test_formatter_names_fill_price_qty_pnl_and_reconciliation():
    # Same-quote fill: the P&L is computable and rendered with the monitor symbol.
    event = evaluate_venue_stop_fills(WATCH, [_venue_fill(pair="<TICKER>USD")], None, now=NOW)[0][0]
    rendered = _pw_fmt.format_as_default_venue_stop_fill(event, _CTX)
    assert "<TICKER>" in rendered
    assert "48.20" in rendered
    assert "1.66" in rendered
    assert "-20.64" in rendered
    assert "venue-side" in rendered or "VENUE" in rendered
    assert "no longer monitored" in rendered
    assert "portfolio-mgmt add --side sell" in rendered
    assert "STOP BREACHED" not in rendered


def test_formatter_cross_quote_fill_not_rendered_as_monitor_currency():
    """Cross-quote fill (``<TICKER>EUR`` vs monitor ``kraken:<TICKER>USD``): the
    fill price renders in the fill's own quote — never with the monitor's
    currency symbol — and no monitor-currency P&L figure is fabricated."""
    event = evaluate_venue_stop_fills(WATCH, [_venue_fill()], None, now=NOW)[0][0]
    assert event["realised_pnl"] is None

    rendered = _pw_fmt.format_as_default_venue_stop_fill(event, _CTX)
    assert "48.20 EUR" in rendered
    assert "$48.20" not in rendered
    assert "€" not in rendered
    assert "-20.64" not in rendered
    assert "not computed" in rendered

    compact = _pw_fmt._format_venue_stop_fill(event, _CTX)
    assert "48.20 EUR" in compact
    assert "$48.20" not in compact
    assert "-20.64" not in compact
    assert "n/a (EUR fill vs USD monitor)" in compact


def test_formatter_partial_fill_says_monitoring_continues():
    event = evaluate_venue_stop_fills(WATCH, [_venue_fill(order_id="OP", vol="0.66", vol_exec="0.33")], None, now=NOW)[
        0
    ][0]
    rendered = _pw_fmt.format_as_default_venue_stop_fill(event, _CTX)
    assert "0.33" in rendered
    assert "still open" in rendered
    assert "no longer monitored" not in rendered
    assert "POSITION CLOSED" not in rendered
    # A partial render must not tell the reader to record/reconcile a full exit.
    assert "portfolio-mgmt add --side sell" not in rendered


def test_formatter_unknown_position_size_never_claims_still_open():
    """The 2026-09-23 event's second defect: a watch with no ``position_size``
    has ``closed_position`` False by construction, so the old render printed
    "Partial exit — position still open" without any basis. The render must
    say the held size is unknown instead."""
    watch = {k: v for k, v in WATCH.items() if k != "position_size"}
    event = evaluate_venue_stop_fills(watch, [_venue_fill()], None, now=NOW)[0][0]
    assert event["position_size"] is None
    assert event["closed_position"] is False

    rendered = _pw_fmt.format_as_default_venue_stop_fill(event, _CTX)
    assert "still open" not in rendered
    assert "partial exit" not in rendered
    assert "POSITION CLOSED" not in rendered
    assert "unknown" in rendered
    assert "monitoring continues" in rendered

    compact = _pw_fmt._format_venue_stop_fill(event, _CTX)
    assert "partial exit" not in compact
    assert "exit size unknown" in compact
    assert "\n" not in compact


def test_formatter_cost_basis_unknown_when_no_pnl():
    watch = {k: v for k, v in WATCH.items() if k != "entry_price"}
    event = evaluate_venue_stop_fills(watch, [_venue_fill()], None, now=NOW)[0][0]
    assert event["realised_pnl"] is None
    rendered = _pw_fmt.format_as_default_venue_stop_fill(event, _CTX)
    assert "cost basis unknown" in rendered
    compact = _pw_fmt._format_venue_stop_fill(event, _CTX)
    assert "cost basis unknown" in compact
    assert "\n" not in compact


def test_status_line_unchanged_without_position_closed():
    event = {
        "name": "<TICKER>",
        "current_price": 55.0,
        "entry_price": 60.15,
        "pct_from_entry": -8.6,
        "above_entry_streak": 0,
        "active_zone": None,
        "next_zone_below": None,
        "invalidation_floor": None,
        "fired_drops": [],
        "prev_price": None,
    }
    baseline = _pw_fmt.format_as_default_status(event, _CTX)
    assert "closed venue-side" not in baseline
    event["position_closed"] = None
    assert _pw_fmt.format_as_default_status(event, _CTX) == baseline
    event["position_closed"] = {"order_id": "O1"}
    assert "closed venue-side" in _pw_fmt.format_as_default_status(event, _CTX)
    assert "not monitored" in _pw_fmt.format_as_default_status(event, _CTX)


# ────────────────────────────────────────────────────────── run.py end-to-end


def _load_run_mod(spec_name: str):
    spec = importlib.util.spec_from_file_location(spec_name, _RUN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_config(tmp_path, watch, basename="open-positions.json"):
    cfg = tmp_path / basename
    cfg.write_text(json.dumps({"watches": [watch]}))
    return cfg


class TestWorkedCaseVenueStopFill:
    """The card's acceptance gate: held file with an open stop, a venue fill,
    and the resulting alert — the tick's price is ABOVE the stop (the wick
    filled and recovered between samples) so level evaluation has nothing to
    say, exactly like the 2026-09-20 silent stop-out."""

    def _config(self, tmp_path):
        return _write_config(tmp_path, WATCH)

    def _patch_tick(self, monkeypatch, run_mod, price=55.0):
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: price)
        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", lambda: [_venue_fill()])

    def test_venue_fill_reported_and_state_marked_closed(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_worked1")
        cfg = self._config(tmp_path)
        self._patch_tick(monkeypatch, run_mod)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )

        assert run_mod.main() == 0
        out = capsys.readouterr().out
        assert "48.20" in out, out
        assert "1.66" in out, out
        assert "<TICKER>" in out, out
        # Cross-quote fill (<TICKER>EUR vs kraken:<TICKER>USD monitor): no
        # monitor-currency P&L figure, price rendered in the fill's own quote.
        assert "-20.64" not in out, out
        assert "$48.20" not in out, out
        assert "48.20 EUR" in out, out
        assert "STOP BREACHED" not in out

        state_file = tmp_path / "<TICKER>_state.json"
        state = json.loads(state_file.read_text())
        assert state["position_closed"]["order_id"] == "OCLOSED-1"
        assert state["venue_stop_fills"]["OCLOSED-1"]["closed_position"] is True

    def test_second_tick_silent_and_levels_not_evaluated(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_worked2")
        cfg = self._config(tmp_path)
        self._patch_tick(monkeypatch, run_mod)

        argv = ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        monkeypatch.setattr(sys, "argv", argv)
        assert run_mod.main() == 0  # tick 1: report + close
        capsys.readouterr()

        calls: list[bool] = []

        def _spy(*_a, **_kw):
            calls.append(True)
            return [], {}

        monkeypatch.setattr(run_mod, "evaluate_levels", _spy)
        assert run_mod.main() == 0  # tick 2: silent
        out = capsys.readouterr().out
        assert out == "", f"second tick must print nothing, got {out!r}"
        assert calls == [], "evaluate_levels must not run for a closed position"

    def test_status_renders_closed_marker(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_worked3")
        cfg = self._config(tmp_path)
        self._patch_tick(monkeypatch, run_mod)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )
        assert run_mod.main() == 0
        capsys.readouterr()

        monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--status"])
        assert run_mod.main() == 0
        out = capsys.readouterr().out
        assert "closed venue-side" in out
        assert "not monitored" in out


class TestVenueStopSuppression:
    def _config(self, tmp_path, **overrides):
        watch = {**WATCH, **overrides}
        return _write_config(tmp_path, watch)

    def test_venue_stops_false_on_watch_suppresses_read(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_off_watch")
        cfg = self._config(tmp_path, venue_stops=False)
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 55.0)

        def _boom():
            raise AssertionError("venue reader must not be called")

        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", _boom)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )
        assert run_mod.main() == 0

    def test_no_venue_stops_flag_suppresses_read(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_off_flag")
        cfg = self._config(tmp_path)
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 55.0)

        def _boom():
            raise AssertionError("venue reader must not be called")

        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", _boom)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--no-venue-stops"]
        )
        assert run_mod.main() == 0

    def test_failing_venue_read_warns_and_tick_completes(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_fail")
        cfg = self._config(tmp_path)
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 48.0)
        # Seed fresh state: a first-ever tick is stale-silent by design, so the
        # level alert would never fire on the very first tick.
        (tmp_path / "<TICKER>_state.json").write_text(
            json.dumps(
                {
                    "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "levels": {},
                    "signals": {},
                }
            )
        )

        def _boom():
            raise RuntimeError("kraken CLI not found in PATH; install it first")

        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", _boom)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )
        assert run_mod.main() == 0
        captured = capsys.readouterr()
        assert "[WARN] venue closed-orders read failed" in captured.err
        # The level alert path is intact: price 48.0 < stop 49.71.
        assert "STOP BREACHED" in captured.out


class TestStaleStateKeepsVenueKeys:
    """>24h-stale state must not lose the venue keys: staleness exists to
    silence level/signal alerts, not to re-report an already-seen stop fill or
    to drop the sticky closure marker (which would resume monitoring a phantom
    position forever)."""

    def _seed_stale_state(self, tmp_path):
        stale_ts = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)).isoformat()
        (tmp_path / "<TICKER>_state.json").write_text(
            json.dumps(
                {
                    "name": "<TICKER>",
                    "_updated_at": stale_ts,
                    "above_entry_streak": 7,
                    "levels": {},
                    "signals": {},
                    "venue_stop_fills": {"OCLOSED-1": {"reported_at": stale_ts, "closed_position": True}},
                    "position_closed": {
                        "order_id": "OCLOSED-1",
                        "fill_price": 48.20,
                        "filled_volume": 1.66,
                        "reported_at": stale_ts,
                    },
                }
            )
        )

    def test_stale_state_does_not_re_report_stop_fill(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_stale1")
        cfg = _write_config(tmp_path, WATCH)
        self._seed_stale_state(tmp_path)
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 55.0)
        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", lambda: [_venue_fill()])

        calls: list[bool] = []

        def _spy(*_a, **_kw):
            calls.append(True)
            return [], {}

        monkeypatch.setattr(run_mod, "evaluate_levels", _spy)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )

        assert run_mod.main() == 0
        out = capsys.readouterr().out
        assert out == "", f"already-reported stop fill must not re-fire, got {out!r}"
        assert calls == [], "levels must not be evaluated for a closed position"

        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"]["order_id"] == "OCLOSED-1"
        assert state["venue_stop_fills"]["OCLOSED-1"]["closed_position"] is True

    def test_stale_state_marker_survives_when_fill_aged_out(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_stale2")
        cfg = _write_config(tmp_path, WATCH)
        self._seed_stale_state(tmp_path)
        # The fill has aged out of the fetched closed-orders page — only the
        # sticky marker stands between the phantom position and level alerts.
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 48.0)
        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", lambda: [])

        def _spy(*_a, **_kw):
            raise AssertionError("levels must not be evaluated for a closed position")

        monkeypatch.setattr(run_mod, "evaluate_levels", _spy)
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )

        assert run_mod.main() == 0
        assert capsys.readouterr().out == ""
        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"]["order_id"] == "OCLOSED-1"

    def test_status_renders_closed_marker_from_stale_state(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_stale3")
        cfg = _write_config(tmp_path, WATCH)
        self._seed_stale_state(tmp_path)
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 48.0)
        monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--status"])

        assert run_mod.main() == 0
        out = capsys.readouterr().out
        assert "closed venue-side" in out
        assert "not monitored" in out
        # The rest of the stale state is still treated as empty (streaks and
        # alerted levels do not leak through) — only the venue marker survives.
        assert "above entry streak=" not in out


class TestPartialFillMonitoring:
    def test_partial_fill_reported_then_levels_still_evaluated(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_partial")
        cfg = _write_config(tmp_path, WATCH)

        prices = iter([55.0, 48.0])
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: next(prices))
        monkeypatch.setattr(
            run_mod,
            "_read_venue_closed_orders",
            lambda: [_venue_fill(order_id="OPART", vol="0.66", vol_exec="0.33")],
        )
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )

        assert run_mod.main() == 0  # tick 1: partial report
        out1 = capsys.readouterr().out
        assert "0.33" in out1
        assert "STOP BREACHED" not in out1  # price above stop
        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"] is None
        assert "OPART" in state["venue_stop_fills"]

        assert run_mod.main() == 0  # tick 2: price below stop → level alert fires
        out2 = capsys.readouterr().out
        assert "STOP BREACHED" in out2


class TestReentryRequiresClearingTheMarker:
    """The sticky ``position_closed`` marker suppresses level evaluation; a
    re-entered position only resumes monitoring once the marker is cleared.
    Clearing is marker-ONLY (edit the state file, remove ``position_closed``):
    the state file also holds the ``venue_stop_fills`` dedupe ledger, and the
    detector has no time bound — deleting the file would let the previous stop
    fill, still inside the venue's closed-orders page, re-report as a fresh
    closure of the new position."""

    def test_marker_suppresses_levels_until_cleared(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("position_watchdog_venue_reentry")
        cfg = _write_config(tmp_path, WATCH)
        (tmp_path / "<TICKER>_state.json").write_text(
            json.dumps(
                {
                    "name": "<TICKER>",
                    "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "levels": {},
                    "signals": {},
                    "venue_stop_fills": {},
                    "position_closed": {
                        "order_id": "OCLOSED-1",
                        "fill_price": 48.20,
                        "filled_volume": 1.66,
                    },
                }
            )
        )
        # Price is below the stop level — an open position would alert here.
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 48.0)
        monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path)])

        assert run_mod.main() == 0
        out1 = capsys.readouterr().out
        assert out1 == "", f"closed position must not be level-evaluated, got {out1!r}"

        # Re-entry step from SKILL.md: clear the sticky marker; the position
        # resumes being monitored and the level evaluation fires again.
        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        state.pop("position_closed")
        (tmp_path / "<TICKER>_state.json").write_text(json.dumps(state))
        assert run_mod.main() == 0
        out2 = capsys.readouterr().out
        assert "STOP BREACHED" in out2, out2

    def test_reentry_with_retained_ledger_does_not_re_report_or_un_monitor(self, monkeypatch, tmp_path, capsys):
        """The documented re-entry (marker-only edit, dedupe ledger retained)
        must not re-report the old fill and must keep the new position open."""
        run_mod = _load_run_mod("position_watchdog_venue_reentry_ledger")
        cfg = _write_config(tmp_path, WATCH)
        (tmp_path / "<TICKER>_state.json").write_text(
            json.dumps(
                {
                    "name": "<TICKER>",
                    "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "levels": {},
                    "signals": {},
                    "venue_stop_fills": {
                        # The OLD fill is still inside the venue's closed-orders
                        # page and its volume (1.66) covers the new
                        # position_size (1.66) within the 1% closure tolerance.
                        "OCLOSED-1": {
                            "reported_at": dt.datetime.now(dt.UTC).isoformat(),
                            "fill_price": 48.20,
                            "filled_volume": 1.66,
                            "order_type": "stop-loss",
                            "closed_position": True,
                        }
                    },
                    "position_closed": {
                        "order_id": "OCLOSED-1",
                        "fill_price": 48.20,
                        "filled_volume": 1.66,
                    },
                }
            )
        )
        # Marker-only re-entry edit from SKILL.md — the ledger stays.
        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        state.pop("position_closed")
        (tmp_path / "<TICKER>_state.json").write_text(json.dumps(state))

        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 55.0)
        # The old fill is still on the venue's closed-orders page this tick.
        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", lambda: [_venue_fill()])
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )

        assert run_mod.main() == 0
        out = capsys.readouterr().out
        assert out == "", f"old fill must not re-report against the new position, got {out!r}"
        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"] is None, "the re-entered position must not be re-closed"
        assert state["venue_stop_fills"]["OCLOSED-1"]["closed_position"] is True

        # Monitoring continues: a below-stop tick still fires the level alert.
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: 48.0)
        assert run_mod.main() == 0
        out2 = capsys.readouterr().out
        assert "STOP BREACHED" in out2, out2


class TestWatchStateLifetimeBound:
    """The 2026-09-23 false alarm: swing-scan recreated a watch that same
    morning, the fresh state started with an empty ``venue_stop_fills``
    ledger, the venue read has no time window, and a three-day-old stop fill
    (already reported once under the previous watch state) was re-reported
    as a live event. The detector is now bounded by the watch state's
    lifetime anchor, persisted as ``watch_state_created_at``."""

    def _no_signals_config(self, tmp_path):
        # WATCH has levels only; price 55.0 stays above the stop so level
        # evaluation has nothing to add.
        return _write_config(tmp_path, WATCH)

    def _patch_tick(self, monkeypatch, run_mod, closetm, price=55.0):
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_kw: price)
        monkeypatch.setattr(run_mod, "_read_venue_closed_orders", lambda: [_venue_fill(closetm=closetm)])

    def _run(self, monkeypatch, tmp_path, run_mod, cfg):
        monkeypatch.setattr(
            sys, "argv", ["run.py", "--config", str(cfg), "--state-dir", str(tmp_path), "--venue-stops"]
        )
        return run_mod.main()

    def test_fresh_watch_does_not_report_multi_day_old_fill_and_second_tick_stays_silent(
        self, monkeypatch, tmp_path, capsys
    ):
        """Acceptance 1+2: a watch whose state has no ledger does not report a
        fill that closed days before the state existed — and the suppression
        is deterministic, not a one-shot self-heal."""
        run_mod = _load_run_mod("position_watchdog_venue_bound_fresh")
        cfg = self._no_signals_config(tmp_path)
        now_epoch = dt.datetime.now(dt.UTC).timestamp()
        self._patch_tick(monkeypatch, run_mod, closetm=now_epoch - 3 * 24 * 3600)

        assert self._run(monkeypatch, tmp_path, run_mod, cfg) == 0
        assert capsys.readouterr().out == ""

        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"] is None
        assert state["venue_stop_fills"] == {}
        assert "watch_state_created_at" in state

        # Second tick, same old fill on the venue page: still silent.
        assert self._run(monkeypatch, tmp_path, run_mod, cfg) == 0
        assert capsys.readouterr().out == ""

    def test_seeded_old_creation_anchor_reports_within_lifetime_fill(self, monkeypatch, tmp_path, capsys):
        """The bound never suppresses a within-lifetime fill: a state file
        created ten days ago anchors far enough back, so a three-day-old
        fill is reported and the position is marked closed."""
        run_mod = _load_run_mod("position_watchdog_venue_bound_seeded")
        cfg = self._no_signals_config(tmp_path)
        created = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
        (tmp_path / "<TICKER>_state.json").write_text(
            json.dumps(
                {
                    "name": "<TICKER>",
                    "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "watch_state_created_at": created.isoformat(),
                    "levels": {},
                    "signals": {},
                    "venue_stop_fills": {},
                }
            )
        )
        self._patch_tick(monkeypatch, run_mod, closetm=dt.datetime.now(dt.UTC).timestamp() - 3 * 24 * 3600)

        assert self._run(monkeypatch, tmp_path, run_mod, cfg) == 0
        out = capsys.readouterr().out
        assert "VENUE STOP" in out, out

        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"]["order_id"] == "OCLOSED-1"
        assert state["venue_stop_fills"]["OCLOSED-1"]["closed_position"] is True
        # The persisted anchor is frozen — the tick must not advance it.
        assert state["watch_state_created_at"] == created.isoformat()

    def test_fill_landed_just_before_creation_is_reported_within_the_grace(self, monkeypatch, tmp_path, capsys):
        """The grace covers a fill that landed just before the watch's first
        tick (or before its venue read succeeded): ten minutes before the
        anchor is inside one cadence plus grace, so it is still reported."""
        run_mod = _load_run_mod("position_watchdog_venue_bound_grace")
        cfg = self._no_signals_config(tmp_path)
        (tmp_path / "<TICKER>_state.json").write_text(
            json.dumps(
                {
                    "name": "<TICKER>",
                    "_updated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "watch_state_created_at": dt.datetime.now(dt.UTC).isoformat(),
                    "levels": {},
                    "signals": {},
                    "venue_stop_fills": {},
                }
            )
        )
        self._patch_tick(monkeypatch, run_mod, closetm=dt.datetime.now(dt.UTC).timestamp() - 600)

        assert self._run(monkeypatch, tmp_path, run_mod, cfg) == 0
        out = capsys.readouterr().out
        assert "VENUE STOP" in out, out

        state = json.loads((tmp_path / "<TICKER>_state.json").read_text())
        assert state["position_closed"]["order_id"] == "OCLOSED-1"


class TestVenueStopsValidation:
    def test_venue_stops_must_be_bool(self):
        run_mod = _load_run_mod("position_watchdog_venue_validate")
        errs = run_mod._validate_watch({**WATCH, "venue_stops": "yes"})
        assert any("venue_stops" in e and "boolean" in e for e in errs)
        assert run_mod._validate_watch({**WATCH, "venue_stops": False}) == []


class TestVenueReaderResolvesProviderThroughRegistry:
    """BLOCKER regression: the watchdog process never imports the execution
    skill, so the ``kraken`` execution provider is in the registry ONLY if
    ``_read_venue_closed_orders`` itself imports
    ``analysis.providers.execution.kraken_spot`` for its registration side
    effect. This test calls the real ``_read_venue_closed_orders()`` with only
    the ``kraken`` CLI shell-out (``subprocess.run``) patched, so the unpatched
    registry lookup is covered — remove the side-effect import and it raises
    ``ValueError: Unknown execution venue`` on every tick."""

    def test_read_resolves_kraken_provider_and_normalises_orders(self, monkeypatch):
        import subprocess

        import analysis.providers.execution as _exec_pkg
        import analysis.providers.execution.base as _base_mod

        run_mod = _load_run_mod("position_watchdog_venue_registry")

        # Pretend the registry has never seen kraken: the function under test
        # must re-register it via its own import. monkeypatch restores the
        # original registration after the test.
        monkeypatch.setattr(
            _base_mod,
            "_EXECUTION_REGISTRY",
            {k: v for k, v in _base_mod._EXECUTION_REGISTRY.items() if k != "kraken"},
        )
        # Force the function's `from analysis.providers.execution import
        # kraken_spot` to re-execute the module (and its module-level
        # registration) even when another test module already imported it in
        # this process.
        monkeypatch.delitem(sys.modules, "analysis.providers.execution.kraken_spot", raising=False)
        monkeypatch.delattr(_exec_pkg, "kraken_spot", raising=False)

        envelope = {
            "closed": {
                "OTEST-1": {
                    "descr": {"pair": "XXBTZUSD", "type": "sell", "ordertype": "stop-loss"},
                    "vol": "0.010",
                    "vol_exec": "0.010",
                    "price": "48000.0",
                    "cost": "480.0",
                    "fee": "0.35",
                    "status": "closed",
                    "opentm": 1788996400.0,
                    "closetm": 1789000000.0,
                    "stopprice": "49000.0",
                }
            },
            "count": 1,
        }
        cmds: list[list[str]] = []

        def _fake_run(cmd, **_kwargs):
            cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(envelope), stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        orders = run_mod._read_venue_closed_orders()

        assert cmds, "the kraken CLI shell-out must have been invoked"
        assert cmds[0][:3] == ["kraken", "closed-orders", "--trades"]
        assert [o["order_id"] for o in orders] == ["OTEST-1"]
        assert orders[0]["pair"] == "XXBTZUSD"
        assert orders[0]["side"] == "sell"
        assert orders[0]["order_type"] == "stop-loss"
        assert orders[0]["filled_volume"] == pytest.approx(0.010)
        assert orders[0]["fill_price"] == pytest.approx(48000.0)
        assert orders[0]["closed_at"] == pytest.approx(1789000000.0)
