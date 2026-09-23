"""Tests for market-overview scan() — hermetic, no network.

Mocks ``_analyze_one`` so the concurrent scan path (sorting, action
filter, top_n, error aggregation) is exercised without hitting a venue.
"""

from __future__ import annotations

import importlib.util
import os


def _load_run():
    run_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-overview", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("market_overview_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_analyze(results, errors):
    """Return an ``_analyze_one`` replacement that pops from queues."""

    def _one(ticker, source=None, interval="1d", period="1y"):
        if errors:
            for e in list(errors):
                if e.get("ticker") == ticker:
                    errors.remove(e)
                    return e
        for r in list(results):
            if r["ticker"] == ticker:
                results.remove(r)
                return r
        return {"ticker": ticker, "error": "no data"}

    return _one


def _row(ticker, score, action):
    return {
        "ticker": ticker,
        "unified_score": score,
        "action": action,
        "price": 1.0,
        "trend": "x",
        "rsi": 50,
        "squeeze": "FLAT",
    }


def test_scan_sorts_by_unified_score_desc():
    run = _load_run()
    results = [_row("AAA", 10, "AVOID"), _row("BBB", 90, "STRONG_BUY"), _row("CCC", 50, "WATCH")]
    run._analyze_one = _fake_analyze(results, [])
    out, errs = run.scan(["AAA", "BBB", "CCC"])
    assert errs == []
    assert [r["ticker"] for r in out] == ["BBB", "CCC", "AAA"]


def test_scan_action_filter():
    run = _load_run()
    results = [_row("AAA", 10, "AVOID"), _row("BBB", 90, "STRONG_BUY")]
    run._analyze_one = _fake_analyze(results, [])
    out, errs = run.scan(["AAA", "BBB"], action_filter="STRONG_BUY")
    assert errs == []
    assert [r["ticker"] for r in out] == ["BBB"]


def test_scan_top_n_limits_results():
    run = _load_run()
    results = [_row("AAA", 10, "AVOID"), _row("BBB", 90, "STRONG_BUY"), _row("CCC", 50, "WATCH")]
    run._analyze_one = _fake_analyze(results, [])
    out, errs = run.scan(["AAA", "BBB", "CCC"], top_n=2)
    assert errs == []
    # sorted desc then truncated to 2
    assert [r["ticker"] for r in out] == ["BBB", "CCC"]


def test_scan_aggregates_errors():
    run = _load_run()
    errors = [{"ticker": "ZZZ", "error": "no data"}]
    results = [_row("BBB", 90, "STRONG_BUY")]
    run._analyze_one = _fake_analyze(results, errors)
    out, errs = run.scan(["BBB", "ZZZ"])
    assert [r["ticker"] for r in out] == ["BBB"]
    assert errs == [{"ticker": "ZZZ", "error": "no data"}]


FIXED_NOW = 1_760_000_000
FOUR_H = 14400


def _series_with_forming_bar(n_closed=240):
    """Steady uptrend of ``n_closed`` closed 4h bars plus a trailing forming bar.

    The forming bar's close (999.99) and volume (5,000,000) are wildly different
    from the last closed bar's (339.0 / 1,000,000), so any leak of the partial
    bar into the indicator series is detectable while ``price`` must follow it.
    """
    last_closed_ts = FIXED_NOW - 3600 - FOUR_H  # closes 1h ago
    candles = []
    for i in range(n_closed):
        close = 100.0 + i
        ts = last_closed_ts - (n_closed - 1 - i) * FOUR_H
        candles.append([ts, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000])
    candles.append([FIXED_NOW - 3600, 338.5, 340.0, 338.0, 999.99, 5_000_000])  # ~1h elapsed: forming
    return candles


def test_analyze_one_price_is_forming_bar_close_and_indicators_are_closed_bar(monkeypatch):
    """``price`` reports the current (forming-bar) close; indicator inputs do not.

    Discriminating: on the pre-repair code ``price`` was ``closes[-1]`` of the
    closed-bar series (339.0), and a variant that fed the raw series to the
    indicators would leak the 999.99 close / 5,000,000 volume into them.
    """
    import analysis.bars as bars_mod

    run = _load_run()
    raw = _series_with_forming_bar()
    fetch_calls = []

    def fake_fetch_ohlc(ticker, **kwargs):
        fetch_calls.append(kwargs)
        return [row[:] for row in raw]

    monkeypatch.setattr(run, "fetch_ohlc", fake_fetch_ohlc)
    monkeypatch.setattr(bars_mod, "now_epoch", lambda: FIXED_NOW)  # clock seam: trailing bar is forming

    seen = {}
    real_obv_trend = run.compute_obv_trend

    def spy_obv_trend(closes, volumes, *args, **kwargs):
        seen["closes"] = closes
        seen["volumes"] = volumes
        return real_obv_trend(closes, volumes, *args, **kwargs)

    monkeypatch.setattr(run, "compute_obv_trend", spy_obv_trend)

    result = run._analyze_one("TEST")

    # The forming bar was requested explicitly (raw series, trim done in-script).
    assert fetch_calls[0].get("include_partial") is True

    # (a) reported price is the forming bar's close — the current price.
    assert raw[-2][4] == 339.0  # sanity: the gap is real (last closed close)
    assert result["price"] == 999.99

    # (b) indicator series is the closed-bar one: the partial bar's wild
    # close/volume never entered the OBV (volume/RSI/EMA) inputs.
    assert len(seen["closes"]) == 240
    assert seen["closes"][-1] == 339.0
    assert seen["volumes"][-1] == 1_000_000
    assert 999.99 not in seen["closes"]
    assert 5_000_000 not in seen["volumes"]


def _sub_cent_series(n_closed=240, base=0.0043):
    """Sub-cent uptrend of ``n_closed`` closed 4h bars plus a trailing forming bar.

    Shaped like the real fetch: ``[ts, open, high, low, close, volume]``. The
    forming bar's close (0.004364) differs from the last closed close (0.0043)
    so the reported ``price`` field discriminates between the two paths.
    """
    last_closed_ts = FIXED_NOW - 3600 - FOUR_H  # closes 1h ago
    candles = []
    for i in range(n_closed):
        close = base + i * 0.000002  # gentle drift, stays under $0.01
        ts = last_closed_ts - (n_closed - 1 - i) * FOUR_H
        candles.append([ts, close - 0.0000005, close + 0.000001, close - 0.000001, close, 1_000_000])
    candles.append([FIXED_NOW - 3600, 0.0043, 0.0044, 0.0043, 0.004364, 5_000_000])  # ~1h elapsed: forming
    return candles


def test_analyze_one_sub_cent_price_is_not_collapsed_to_zero(monkeypatch):
    """Sub-cent asset: ``price`` must report the real value at 6dp, not 0.0.

    Discriminating: pre-fix ``price`` was ``safe_round(price, 2)`` which rounds
    any value below $0.005 to 0.0.
    """
    import analysis.bars as bars_mod

    run = _load_run()
    raw = _sub_cent_series()
    monkeypatch.setattr(run, "fetch_ohlc", lambda ticker, **kwargs: [row[:] for row in raw])
    monkeypatch.setattr(bars_mod, "now_epoch", lambda: FIXED_NOW)

    result = run._analyze_one("SUBX")

    assert result["price"] != 0.0, "sub-cent price collapsed to 0.0 by 2dp rounding"
    assert result["price"] == round(raw[-1][4], 6)
    assert result["price"] == 0.004364
