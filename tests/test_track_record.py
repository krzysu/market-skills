"""Tests for analysis.track_record — per-ticker hit-rate signal from DTP journal."""

from __future__ import annotations

import pytest

from analysis.track_record import compute_track_record


def _scan(scan_id: str, ideas: list[dict]) -> dict:
    return {"type": "scan", "id": scan_id, "created_ts": "2026-07-07T00:00:00Z", "ideas": ideas}


def _closed_idea(pair: str, verdict: str, return_pct: float) -> dict:
    return {
        "pair": pair,
        "ticker": pair,
        "strategy": "strategy-trend-follow",
        "direction": "long",
        "entry_price": 100.0,
        "stop": 95.0,
        "tp1": 110.0,
        "tp2": 115.0,
        "tp3": 120.0,
        "tp1_pct": 10.0,
        "rr_to_tp1": 2.0,
        "conviction": 4,
        "version": "v3",
        "narrative": "test idea",
        "met_bar": True,
        "picked": True,
        "rejection_reasons": [],
        "macro_aligned": True,
        "cooldown_ok": True,
        "status": "closed",
        "outcome_verdict": verdict,
        "actual_return_pct": return_pct,
    }


def _open_idea(pair: str) -> dict:
    return {
        "pair": pair,
        "ticker": pair,
        "strategy": "strategy-trend-follow",
        "direction": "long",
        "entry_price": 100.0,
        "stop": 95.0,
        "tp1": 110.0,
        "tp2": 115.0,
        "tp3": 120.0,
        "tp1_pct": 10.0,
        "rr_to_tp1": 2.0,
        "conviction": 4,
        "version": "v3",
        "narrative": "test idea",
        "met_bar": True,
        "picked": False,
        "rejection_reasons": [],
        "macro_aligned": True,
        "cooldown_ok": True,
        "status": "open",
    }


def _hl_lit_journal() -> list[dict]:
    """Build a 24-scan journal with 9 closed hl:LIT ideas (7 hits, 2 misses)
    within the most recent 20 scans.  Scans 0-3 have no hl:LIT ideas;
    scans 4-12 carry the closed hl:LIT ideas; scans 13-23 have other tickers.
    """
    closed = [
        _closed_idea("hl:LIT", "hit", 10.0),
        _closed_idea("hl:LIT", "hit", 9.0),
        _closed_idea("hl:LIT", "miss", -3.0),
        _closed_idea("hl:LIT", "hit", 8.0),
        _closed_idea("hl:LIT", "hit", 10.0),
        _closed_idea("hl:LIT", "miss", 0.6),
        _closed_idea("hl:LIT", "hit", 8.0),
        _closed_idea("hl:LIT", "hit", 7.0),
        _closed_idea("hl:LIT", "hit", 8.0),
    ]
    scans = []
    # scans 0-3: no hl:LIT ideas
    for i in range(4):
        scans.append(_scan(f"2026-06-{20 + i:03d}", []))
    # scans 4-12: closed hl:LIT ideas
    for i, idea in enumerate(closed):
        scans.append(_scan(f"2026-06-{24 + i:03d}", [idea]))
    # scans 13-23: other tickers
    for i in range(11):
        scans.append(_scan(f"2026-07-{i:03d}", [_closed_idea("BTCUSD", "hit", 5.0)]))
    return scans


class TestComputeTrackRecord:
    def test_hl_lit_fixture(self):
        tr = compute_track_record("hl:LIT", picks=_hl_lit_journal())
        assert tr["eligible"] is True
        assert tr["n_closed"] == 9
        assert tr["n_hits"] == 7
        assert tr["n_misses"] == 2
        assert round(tr["hit_rate"], 2) == 0.78
        assert round(tr["avg_return_pct"], 2) == 6.40
        assert tr["multiplier"] == pytest.approx(2.12, abs=0.01)

    def test_empty_journal(self):
        tr = compute_track_record("hl:LIT", picks=[])
        assert tr["eligible"] is False
        assert tr["n_closed"] == 0
        assert tr["n_hits"] == 0
        assert tr["n_misses"] == 0
        assert tr["hit_rate"] == 0.0
        assert tr["avg_return_pct"] == 0.0
        assert tr["multiplier"] == 1.0

    def test_below_min_closed(self):
        scans = [_scan(f"2026-07-{i:03d}", [_closed_idea("hl:LIT", "hit", 5.0)]) for i in range(2)]
        tr = compute_track_record("hl:LIT", picks=scans)
        assert tr["eligible"] is False
        assert tr["n_closed"] == 2
        assert tr["multiplier"] == 1.0

    def test_all_misses(self):
        scans = [_scan(f"2026-07-{i:03d}", [_closed_idea("hl:LIT", "miss", -2.0)]) for i in range(6)]
        tr = compute_track_record("hl:LIT", picks=scans)
        assert tr["eligible"] is True
        assert tr["n_closed"] == 6
        assert tr["hit_rate"] == 0.0
        assert tr["multiplier"] == 1.0

    def test_fifty_percent_hit_rate(self):
        ideas = [_closed_idea("hl:LIT", "hit", 5.0), _closed_idea("hl:LIT", "miss", -3.0)] * 3
        scans = [_scan(f"2026-07-{i:03d}", [ideas[i]]) for i in range(6)]
        tr = compute_track_record("hl:LIT", picks=scans)
        assert tr["eligible"] is True
        assert round(tr["hit_rate"], 1) == 0.5
        assert tr["multiplier"] == 1.0

    def test_max_multiplier_at_100pct_10_closed(self):
        ideas = [_closed_idea("hl:LIT", "hit", 5.0) for _ in range(10)]
        scans = [_scan(f"2026-07-{i:03d}", [ideas[i]]) for i in range(10)]
        tr = compute_track_record("hl:LIT", picks=scans)
        assert tr["eligible"] is True
        assert tr["hit_rate"] == 1.0
        assert tr["multiplier"] == 3.0

    def test_partial_scale_at_100pct_3_closed(self):
        ideas = [_closed_idea("hl:LIT", "hit", 5.0) for _ in range(3)]
        scans = [_scan(f"2026-07-{i:03d}", [ideas[i]]) for i in range(3)]
        tr = compute_track_record("hl:LIT", picks=scans)
        assert tr["eligible"] is True
        assert tr["hit_rate"] == 1.0
        assert round(tr["multiplier"], 1) == 2.2

    def test_ideas_older_than_lookback_excluded(self):
        scans = [_scan(f"2026-06-{i:03d}", [_closed_idea("hl:LIT", "hit", 5.0)]) for i in range(25)]
        tr = compute_track_record("hl:LIT", picks=scans, lookback_scans=20)
        assert tr["n_closed"] == 20
        assert tr["eligible"] is True

    def test_expired_ideas_not_counted(self):
        expired = {
            "pair": "hl:LIT",
            "ticker": "hl:LIT",
            "strategy": "strategy-trend-follow",
            "direction": "long",
            "entry_price": 100.0,
            "stop": 95.0,
            "tp1": 110.0,
            "tp2": 115.0,
            "tp3": 120.0,
            "tp1_pct": 10.0,
            "rr_to_tp1": 2.0,
            "conviction": 4,
            "version": "v3",
            "narrative": "expired idea",
            "met_bar": True,
            "picked": False,
            "rejection_reasons": [],
            "macro_aligned": True,
            "cooldown_ok": True,
            "status": "closed",
            "outcome_verdict": "expired",
        }
        hit_ideas = [_closed_idea("hl:LIT", "hit", 5.0) for _ in range(3)]
        scans = [
            _scan("2026-07-001", [expired]),
            *[_scan(f"2026-07-{i + 2:03d}", [hit_ideas[i]]) for i in range(3)],
        ]
        tr = compute_track_record("hl:LIT", picks=scans)
        assert tr["n_closed"] == 3
        assert tr["n_hits"] == 3
        assert tr["n_misses"] == 0
        assert tr["eligible"] is True

    def test_monotonic_in_hit_rate(self):
        scans = [_scan(f"2026-07-{i:03d}", [_closed_idea("hl:LIT", "hit", 5.0)]) for i in range(10)]
        base = compute_track_record("hl:LIT", picks=scans)
        # same n_closed, lower hit_rate -> lower or equal multiplier
        miss_scans = [_scan(f"2026-07-{i:03d}", [_closed_idea("hl:LIT", "miss", -2.0)]) for i in range(10, 20)]
        all_scans = scans + miss_scans
        lower = compute_track_record("hl:LIT", picks=all_scans)
        assert lower["multiplier"] <= base["multiplier"]

    def test_monotonic_in_n_closed(self):
        base = _scan("2026-07-001", [_closed_idea("hl:LIT", "hit", 5.0)])
        extra = _scan("2026-07-002", [_closed_idea("hl:LIT", "hit", 5.0)])
        tr1 = compute_track_record("hl:LIT", picks=[base])
        tr2 = compute_track_record("hl:LIT", picks=[base, extra])
        assert tr2["multiplier"] >= tr1["multiplier"]

    def test_no_hidden_state(self):
        picks = _hl_lit_journal()
        tr1 = compute_track_record("hl:LIT", picks=picks)
        tr2 = compute_track_record("hl:LIT", picks=picks)
        assert tr1 == tr2
