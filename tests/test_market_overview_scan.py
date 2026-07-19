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
