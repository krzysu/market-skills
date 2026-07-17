"""Per-fix fixture for ``_validate_journal`` in conviction_grid.py.

The journal-parsing bug: the script read the journal as either a single dict
with an ``ideas`` key OR a flat list of ideas, but the daily-trade-pick
journal is actually a *list of rounds* where each round carries its own
``ideas`` list. With the prior parse, every band reported ``n=0`` because
``idea.get('conviction')`` on a round dict returned None, even when dozens of
closed trades existed in the journal.

The fix: flatten list-of-rounds to a single ideas list before banding, and
read realized pnl from ``actual_return_pct`` (the journal's actual field
name) with a fallback to ``pnl``.

These tests pin the new parse contract on three shapes:

  1. dict-with-ideas key (legacy),
  2. flat list of ideas,
  3. list of rounds (the daily-trade-pick shape).
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys

import pytest


def _load_module():
    lib_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "skills",
        "strategy-liquidity-sweep",
        "scripts",
        "conviction_grid.py",
    )
    spec = importlib.util.spec_from_file_location("conviction_grid", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_journal(tmp_path, journal_obj):
    p = tmp_path / "picks.json"
    p.write_text(json.dumps(journal_obj))
    return str(p)


def _capture_validate_journal(mod, journal_obj, monkeypatch, tmp_path, *, strategy=None):
    """Run ``_validate_journal`` against ``journal_obj`` (a python literal)
    and capture its stdout. Returns the captured lines."""
    path = _write_journal(tmp_path, journal_obj)
    monkeypatch.setenv("LIQ_SWEEP_JOURNAL_PATH", path)
    buf = io.StringIO()
    real_stdout = sys.stdout
    try:
        sys.stdout = buf
        mod._validate_journal(strategy=strategy)
    finally:
        sys.stdout = real_stdout
    return buf.getvalue()


def _idea(conviction, *, status="closed", pnl_pct=None, pnl=None, strategy="strategy-trend-follow"):
    """Build a journal idea row with the fields the journal actually carries."""
    row = {"conviction": conviction, "status": status, "strategy": strategy}
    if pnl_pct is not None:
        row["actual_return_pct"] = pnl_pct
    if pnl is not None:
        row["pnl"] = pnl
    return row


class TestJournalParse:
    """_validate_journal must accept dict-with-ideas, flat-list, and list-of-rounds."""

    def test_legacy_dict_with_ideas_key(self, monkeypatch, tmp_path, capsys):
        mod = _load_module()
        journal = {
            "ideas": [
                _idea(2, pnl_pct=-1.0),
                _idea(3, pnl_pct=+2.5),
                _idea(3, pnl_pct=-3.0),
                _idea(4, pnl_pct=+5.0),
                _idea(4, pnl_pct=+1.0),
            ]
        }
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        # Conv=3: 1 of 2 hit (one -3.0, one +2.5) -> 50% hit rate, avg -0.25.
        assert "conv=3:" in out
        assert "n=2" in out
        assert "50.0%" in out
        # Conv=4: 2 of 2 hit -> 100%, avg +3.0.
        assert "conv=4:" in out
        assert "100.0%" in out

    def test_flat_list_of_ideas(self, monkeypatch, tmp_path):
        mod = _load_module()
        journal = [
            _idea(5, pnl_pct=+4.0),
            _idea(5, pnl_pct=-2.0),
            _idea(5, pnl_pct=+1.5),
        ]
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        # Conv=5: 2 of 3 winners, 66.7% hit, avg +1.166...
        assert "conv=5:" in out
        assert "n=3" in out
        assert "66.7%" in out

    def test_list_of_rounds_flattens_correctly(self, monkeypatch, tmp_path):
        """The bug shape: a list of rounds, each carrying its own ideas. Pre-fix
        this reported n=0 for every band because the script iterated rounds
        instead of ideas."""
        mod = _load_module()
        journal = [
            {
                "type": "daily-trade-pick",
                "id": "round-0",
                "created_ts": "2026-06-01T00:00:00Z",
                "ideas": [_idea(3, pnl_pct=+2.0), _idea(4, pnl_pct=-1.5)],
            },
            {
                "type": "daily-trade-pick",
                "id": "round-1",
                "created_ts": "2026-06-02T00:00:00Z",
                "ideas": [_idea(2, pnl_pct=+1.0), _idea(4, pnl_pct=+3.5)],
            },
            {
                "type": "daily-trade-pick",
                "id": "round-2",
                "created_ts": "2026-06-03T00:00:00Z",
                "ideas": [_idea(3, pnl_pct=-2.0)],
            },
        ]
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        # Conv=2: 1 winner of 1, 100% hit, avg +1.0.
        assert "conv=2:" in out
        assert "n=1" in out
        # Conv=3: 1 winner of 2 (one +2.0, one -2.0), 50% hit, avg 0.0.
        assert "conv=3:" in out
        assert "n=2" in out
        assert "50.0%" in out
        # Conv=4: 1 winner of 2 (one -1.5, one +3.5), 50% hit, avg +1.0.
        assert "conv=4:" in out
        assert "n=2" in out
        assert "50.0%" in out

    def test_falls_back_to_pnl_field(self, monkeypatch, tmp_path):
        """Legacy journals may use ``pnl`` instead of ``actual_return_pct``."""
        mod = _load_module()
        journal = [{"ideas": [_idea(4, pnl=+5.0), _idea(4, pnl=-2.0)]}]
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        assert "conv=4:" in out
        assert "50.0%" in out

    def test_ignores_non_int_conviction(self, monkeypatch, tmp_path):
        """Non-int convictions are NOT banded. This was already the prior
        behaviour; pinning it to guard against regressions in the re-parse."""
        mod = _load_module()
        journal = [{"ideas": [_idea("high"), _idea(None), _idea(3, pnl_pct=+1.0)]}]
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        # Only conv=3 row is banded; others skipped.
        assert "conv=3:" in out
        assert "n=1" in out
        # Other bands should show n=0.
        assert "conv=1: n=0" in out
        assert "conv=2: n=0" in out

    def test_open_trades_excluded_from_hit_rate(self, monkeypatch, tmp_path):
        """Closed count drives hit-rate; open rows are ignored."""
        mod = _load_module()
        journal = [
            {
                "ideas": [
                    _idea(3, status="closed", pnl_pct=+4.0),
                    _idea(3, status="open", pnl_pct=+99.0),
                ]
            }
        ]
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        # n=2 (one closed + one open counted in band totals),
        # closed=1, hit_rate=100%, avg=+4.0.
        assert "conv=3:" in out
        assert "n=2" in out
        assert "closed=1" in out
        assert "100.0%" in out
        assert "avg_pnl=+4.00" in out


def test_unset_env_raises(monkeypatch):
    """_validate_journal must raise rather than default to a host-specific path."""
    mod = _load_module()
    monkeypatch.delenv("LIQ_SWEEP_JOURNAL_PATH", raising=False)
    with pytest.raises(RuntimeError, match="LIQ_SWEEP_JOURNAL_PATH"):
        mod._validate_journal()


class TestStrategyFilter:
    """--strategy scopes per-band reporting to one L3. Pre-fix, an unfiltered
    run silently aggregated across strategies and made journal-validated
    decisions on cross-strategy noise (a real bug, see bead 7eq notes)."""

    def test_unfiltered_emits_warning(self, monkeypatch, tmp_path):
        """No --strategy filter: the run aggregates and prints a WARNING line."""
        mod = _load_module()
        journal = [{"ideas": [_idea(3, pnl_pct=+1.0)]}]
        out = _capture_validate_journal(mod, journal, monkeypatch, tmp_path)
        assert "WARNING" in out
        assert "--strategy" in out

    def test_strategy_filter_scopes_per_band(self, monkeypatch, tmp_path):
        """With --strategy=X, only ideas matching X are counted. Other-strategy
        rows don't pollute the bands."""
        mod = _load_module()
        journal = [
            {
                "ideas": [
                    _idea(3, pnl_pct=+2.0, strategy="strategy-trend-follow"),
                    _idea(3, pnl_pct=-1.0, strategy="strategy-trend-follow"),
                    _idea(3, pnl_pct=+99.0, strategy="strategy-mean-reversion"),
                ]
            }
        ]
        out = _capture_validate_journal(
            mod,
            journal,
            monkeypatch,
            tmp_path,
            strategy="strategy-trend-follow",
        )
        # Conv=3: 1 winner of 2 trend-follow trades (the mean-reversion +99 is
        # excluded by the filter). n=2 (only trend-follow), not n=3.
        assert "Strategy filter: --strategy=strategy-trend-follow (3 -> 2 ideas)" in out
        assert "conv=3: n=2" in out
        assert "50.0%" in out
        # And no WARNING line when --strategy is explicit.
        assert "WARNING" not in out

    def test_strategy_filter_drops_to_zero_when_no_match(self, monkeypatch, tmp_path):
        """If no idea matches the strategy filter, every band reports n=0
        (the honest answer; a future run with more data may populate them)."""
        mod = _load_module()
        journal = [
            {
                "ideas": [
                    _idea(3, pnl_pct=+2.0, strategy="strategy-trend-follow"),
                    _idea(4, pnl_pct=+1.0, strategy="strategy-trend-follow"),
                ]
            }
        ]
        out = _capture_validate_journal(
            mod,
            journal,
            monkeypatch,
            tmp_path,
            strategy="strategy-liquidity-sweep",
        )
        # Filter excluded everything.
        assert "(2 -> 0 ideas)" in out
        assert "conv=3: n=0" in out
        assert "conv=4: n=0" in out
