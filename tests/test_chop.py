"""Tests for analysis.chop — chop_score + L3 idea history store."""

from __future__ import annotations

import os
import tempfile

import pytest

from analysis import chop as regime


class TestChopScore:
    def test_empty_list_returns_none(self):
        assert regime.chop_score([]) is None

    def test_all_conv_1_returns_1_0(self):
        ideas = [{"conviction": 1} for _ in range(5)]
        assert regime.chop_score(ideas) == 1.0

    def test_most_conv_5_returns_0_0(self):
        ideas = [{"conviction": 5} for _ in range(5)]
        assert regime.chop_score(ideas) == 0.0

    def test_mixed_distribution_in_normal_range(self):
        """HANDOFF test case: 14 ideas, conv {1:5, 2:7, 3:2} → 12/14 = 0.857.

        That's well into the transition-zone (>0.70) — proves the
        transition-zone call site.
        """
        ideas = (
            [{"conviction": 1} for _ in range(5)]
            + [{"conviction": 2} for _ in range(7)]
            + [{"conviction": 3} for _ in range(2)]
        )
        score = regime.chop_score(ideas)
        assert score == pytest.approx(12 / 14)
        assert score > 0.70  # transition-zone

    def test_skips_non_numeric_convictions(self):
        ideas = [{"conviction": "v3"}, {"conviction": 1}, {"conviction": 2}]
        # 2 countable, both low → 1.0
        assert regime.chop_score(ideas) == 1.0

    def test_skips_booleans_as_convictions(self):
        """Booleans are technically int in Python — guard against True
        sneaking in as conviction=1.
        """
        ideas = [{"conviction": True}, {"conviction": False}, {"conviction": 1}]
        # Only the int(1) counts; bools are filtered.
        score = regime.chop_score(ideas)
        assert score == 1.0  # the one countable is conv 1

    def test_skips_non_dict_entries(self):
        ideas = [None, "string", {"conviction": 1}, {"conviction": 2}]
        score = regime.chop_score(ideas)
        assert score == 1.0

    def test_all_skipped_returns_none(self):
        ideas = [None, "string", {"conviction": "v3"}]
        assert regime.chop_score(ideas) is None

    def test_window_ticks_accepted_for_api_symmetry(self):
        """window_ticks is a no-op in the current implementation (caller
        slices history before calling). Pin the contract.
        """
        ideas = [{"conviction": 1}, {"conviction": 2}]
        assert regime.chop_score(ideas, window_ticks=10) == 1.0


class TestHistoryStore:
    def _tmp_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)  # start clean
        return path

    def test_load_history_missing_returns_empty(self):
        path = self._tmp_path()
        try:
            assert regime.load_history(path) == []
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_load_history_malformed_returns_empty(self):
        path = self._tmp_path()
        with open(path, "w") as f:
            f.write("not json")
        try:
            assert regime.load_history(path) == []
        finally:
            os.unlink(path)

    def test_append_then_load_roundtrip(self):
        path = self._tmp_path()
        try:
            n = regime.append_tick(
                [{"pair": "BTC-USD", "direction": "long", "conviction": 4, "version": "v4"}],
                path=path,
            )
            assert n == 1
            history = regime.load_history(path)
            assert len(history) == 1
            assert history[0]["pair"] == "BTC-USD"
            assert history[0]["conviction"] == 4
            assert "ts" in history[0]
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_append_caps_at_200(self):
        path = self._tmp_path()
        try:
            for _ in range(5):
                regime.append_tick(
                    [{"pair": "X", "conviction": 1} for _ in range(50)],
                    path=path,
                )
            history = regime.load_history(path)
            assert len(history) == 200
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_chop_score_from_history_too_few_ticks_returns_none(self):
        path = self._tmp_path()
        try:
            regime.append_tick([{"conviction": 1}, {"conviction": 1}], path=path)
            assert regime.chop_score_from_history(path) is None
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_chop_score_from_history_full_window(self):
        path = self._tmp_path()
        try:
            # Three ticks, each with 5 ideas, all conv 1.
            for _ in range(3):
                regime.append_tick([{"conviction": 1} for _ in range(5)], path=path)
            summary = regime.chop_score_from_history(path)
            assert summary is not None
            assert summary["score"] == 1.0
            assert summary["window_ticks"] == 3
            assert summary["ideas_count"] == 15
            assert summary["low_count"] == 15
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_chop_score_from_history_mixed_window(self):
        path = self._tmp_path()
        try:
            # Tick 1: 5 high-conv ideas.
            regime.append_tick([{"conviction": 5} for _ in range(5)], path=path)
            # Tick 2: 5 high-conv ideas.
            regime.append_tick([{"conviction": 5} for _ in range(5)], path=path)
            # Tick 3: 5 low-conv ideas.
            regime.append_tick([{"conviction": 1} for _ in range(5)], path=path)
            summary = regime.chop_score_from_history(path)
            assert summary is not None
            # 5 low out of 15 total = 0.333, rounded to 4 dp.
            assert summary["score"] == pytest.approx(0.3333, abs=1e-4)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_default_history_path_uses_xdg_data_home(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
        assert regime.default_history_path() == "/custom/xdg/market-skills/l3_idea_history.json"

    def test_default_history_path_requires_xdg_data_home(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        with pytest.raises(EnvironmentError, match="XDG_DATA_HOME"):
            regime.default_history_path()
