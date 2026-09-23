"""Tests for skills/market-breadth/lib.py — hand-checked fixtures, hermetic (no network)."""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB_PATH = os.path.join(_REPO_ROOT, "skills", "market-breadth", "lib.py")
_spec = importlib.util.spec_from_file_location("market_breadth_lib", _LIB_PATH)
_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lib)
compute_breadth = _lib.compute_breadth
analyze = _lib.analyze


def _series(*closes):
    """Synthetic daily candles [[ts, o, h, l, c, v], ...]; non-numeric closes stay raw for the skip test."""
    return [[i * 86400, 0.0, 0.0, 0.0, c, 0.0] for i, c in enumerate(closes)]


def _watchlist_file(tmp_path, data):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(data))
    return path


WATCHLIST = {
    "baskets": {
        "crypto_alts": {
            "AAAUSD": {"tier": 1, "source": "kraken"},
            "BBBUSD": {"tier": 2, "source": "kraken"},
            "BTCUSD": {"source": "kraken", "label": "BTC"},
        },
        "crypto_majors": {
            "BTCUSD": {"source": "kraken"},
            "ETHUSD": {"source": "kraken"},
        },
    }
}

FAKE_CANDLES = {
    # benchmark: 100 -> 108 over 8 daily closes (+8.00% over the full window)
    "BTCUSD": _series(100, 101, 102, 103, 104, 105, 106, 108),
    "AAAUSD": _series(100, 100, 100, 100, 100, 100, 100, 120),  # +20.00%
    "BBBUSD": _series(100, 100, 100, 100, 100, 100, 100, 110),  # +10.00%
}


class TestComputeBreadthHandChecked:
    def test_alt_rotation_bands_and_order(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 103, 104, 105, 106, 120),  # +20.00%
                "BBBUSD": _series(100, 101, 102, 103, 104, 105, 106, 110),  # +10.00%
                "CCCUSD": _series(100, 101, 102, 103, 104, 105, 106, 105),  # +5.00%
                "DDDUSD": _series(100, 101, 102, 103, 104, 105, 106, 98),  # -2.00%
            },
            _series(100, 101, 102, 103, 104, 105, 106, 104),  # benchmark +4.00%
            window_days=7,
        )
        assert result["pct_beating"] == 75.0  # 3 of 4 strictly above +4.00%
        assert result["median_alt_return_pct"] == 7.5  # median([20, 10, 5, -2])
        assert result["btc_return_pct"] == 4.0
        assert result["members"] == 4
        assert result["regime"] == "alt_rotation"
        assert result["leaders"] == [
            {"ticker": "AAAUSD", "return_pct": 20.0},
            {"ticker": "BBBUSD", "return_pct": 10.0},
            {"ticker": "CCCUSD", "return_pct": 5.0},
        ]
        assert result["laggards"] == [
            {"ticker": "DDDUSD", "return_pct": -2.0},
            {"ticker": "CCCUSD", "return_pct": 5.0},
            {"ticker": "BBBUSD", "return_pct": 10.0},
        ]
        assert result["effective_window_days"] == 7
        assert result["window_days"] == 7
        assert result["errors"] == []

    def test_btc_led_band(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 103, 105),  # +5.00%
                "BBBUSD": _series(100, 101, 102, 103, 103),  # +3.00%
                "CCCUSD": _series(100, 101, 102, 103, 98),  # -2.00%
            },
            _series(100, 101, 102, 103, 110),  # benchmark +10.00%
            window_days=7,
        )
        assert result["pct_beating"] == 0.0
        assert result["regime"] == "btc_led"

    def test_mixed_band(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 103, 104, 105, 106, 120),  # +20.00%
                "BBBUSD": _series(100, 101, 102, 103, 104, 105, 106, 110),  # +10.00%
                "CCCUSD": _series(100, 101, 102, 103, 104, 105, 106, 105),  # +5.00%
                "DDDUSD": _series(100, 101, 102, 103, 104, 105, 106, 98),  # -2.00%
            },
            _series(100, 101, 102, 103, 104, 105, 106, 108),  # benchmark +8.00%
            window_days=7,
        )
        assert result["pct_beating"] == 50.0
        assert result["regime"] == "mixed"

    def test_exact_boundary_60_is_alt_rotation(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 103, 105, 105, 105, 101),  # +1.00%
                "BBBUSD": _series(100, 101, 102, 103, 105, 105, 105, 102),  # +2.00%
                "CCCUSD": _series(100, 101, 102, 103, 105, 105, 105, 99),  # -1.00%
                "DDDUSD": _series(100, 101, 102, 103, 105, 105, 105, 100.5),  # +0.50%
                "EEEUSD": _series(100, 101, 102, 103, 105, 105, 105, 99.5),  # -0.50%
            },
            _series(100, 101, 102, 103, 105, 105, 105, 100),  # benchmark 0.00%
            window_days=7,
        )
        assert result["pct_beating"] == 60.0
        assert result["regime"] == "alt_rotation"

    def test_exact_boundary_40_is_btc_led(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 103, 105, 105, 105, 101),  # +1.00%
                "BBBUSD": _series(100, 101, 102, 103, 105, 105, 105, 102),  # +2.00%
                "CCCUSD": _series(100, 101, 102, 103, 105, 105, 105, 99),  # -1.00%
                "DDDUSD": _series(100, 101, 102, 103, 105, 105, 105, 99.5),  # -0.50%
                "EEEUSD": _series(100, 101, 102, 103, 105, 105, 105, 99.75),  # -0.25%
            },
            _series(100, 101, 102, 103, 105, 105, 105, 100),  # benchmark 0.00%
            window_days=7,
        )
        assert result["pct_beating"] == 40.0
        assert result["regime"] == "btc_led"

    def test_tie_does_not_count_as_beating(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 103, 104, 105, 106, 110),  # +10.00% == benchmark
                "BBBUSD": _series(100, 101, 102, 103, 104, 105, 106, 120),  # +20.00%
                "CCCUSD": _series(100, 101, 102, 103, 104, 105, 106, 98),  # -2.00%
            },
            _series(100, 101, 102, 103, 104, 105, 106, 110),  # benchmark +10.00%
            window_days=7,
        )
        assert result["btc_return_pct"] == 10.0
        assert result["members"] == 3
        assert result["pct_beating"] == round(1 / 3 * 100, 1)  # tie excluded from beating
        assert result["regime"] == "btc_led"

    def test_comparison_uses_unrounded_returns(self):
        # raw +5.0049% vs benchmark raw +4.9951% — both round to 5.00 at 2dp.
        # Comparison must run on unrounded returns: the member IS strictly beating.
        above = compute_breadth(
            {"AAAUSD": _series(100, 100, 100, 100, 100, 100, 100, 105.0049)},
            _series(100, 100, 100, 100, 100, 100, 100, 104.9951),
            window_days=7,
        )
        assert above["btc_return_pct"] == 5.0  # reported value stays rounded to 2dp
        assert above["leaders"] == [{"ticker": "AAAUSD", "return_pct": 5.0}]
        assert above["pct_beating"] == 100.0

        # mirror: raw strictly BELOW the benchmark, still rounding to the same 5.00
        below = compute_breadth(
            {"AAAUSD": _series(100, 100, 100, 100, 100, 100, 100, 104.9951)},
            _series(100, 100, 100, 100, 100, 100, 100, 105.0049),
            window_days=7,
        )
        assert below["btc_return_pct"] == 5.0
        assert below["pct_beating"] == 0.0

    def test_leaders_laggards_deterministic_on_ties(self):
        same = _series(100, 101, 102, 103, 104, 105, 106, 108)
        result = compute_breadth(
            {"ZZZUSD": same, "AAAUSD": same, "MMMUSD": same},
            _series(100, 101, 102, 103, 104, 105, 106, 104),
            window_days=7,
        )
        assert [row["ticker"] for row in result["leaders"]] == ["AAAUSD", "MMMUSD", "ZZZUSD"]
        assert [row["ticker"] for row in result["laggards"]] == ["AAAUSD", "MMMUSD", "ZZZUSD"]


class TestComputeBreadthDegradation:
    def test_member_with_one_bar_excluded_and_named(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100),  # 1 bar — cannot produce a return
                "BBBUSD": _series(100, 101, 102, 103, 104, 105, 106, 110),  # +10.00%
            },
            _series(100, 101, 102, 103, 104, 105, 106, 105),  # benchmark +5.00%
            window_days=7,
        )
        assert result["members"] == 1
        assert result["pct_beating"] == 100.0  # BBB +10.00% beats benchmark +5.00%
        assert result["regime"] == "alt_rotation"
        assert any("AAAUSD" in err for err in result["errors"])
        assert any("SKIPPED" in err for err in result["errors"])

    def test_short_history_truncates_window_without_raising(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 101, 102, 120),  # +20.00% over 3d
                "BBBUSD": _series(100, 101, 102, 98),  # -2.00% over 3d
            },
            _series(100, 101, 102, 104),  # +4.00% over 3d
            window_days=7,
        )
        assert result["effective_window_days"] == 3
        assert result["window_days"] == 7
        assert result["pct_beating"] == 50.0
        assert result["regime"] == "mixed"
        assert any("WINDOW TRUNCATED" in err and "requested 7d, used 3d" in err for err in result["errors"])

    def test_benchmark_is_the_shortest_series(self):
        result = compute_breadth(
            {
                "AAAUSD": _series(100, 100, 100, 100, 100, 100, 100, 120),
                "BBBUSD": _series(100, 100, 100, 100, 100, 100, 100, 110),
            },
            _series(100, 104),  # only 2 closes → 1d return
            window_days=7,
        )
        assert result["effective_window_days"] == 1
        assert result["btc_return_pct"] == 4.0
        assert result["pct_beating"] == 100.0
        assert any("WINDOW TRUNCATED" in err for err in result["errors"])

    def test_benchmark_with_single_close_returns_empty_state(self):
        result = compute_breadth(
            {"AAAUSD": _series(100, 120)},
            _series(100),
            window_days=7,
        )
        assert result["data"] is None
        assert result["count"] == 0
        assert any("usable close" in err for err in result["errors"])

    def test_all_members_unusable_returns_empty_state(self):
        result = compute_breadth(
            {"AAAUSD": _series(100), "BBBUSD": _series(100)},
            _series(100, 110),
            window_days=7,
        )
        assert result["data"] is None
        assert result["count"] == 0
        assert any("nothing to measure" in err for err in result["errors"])

    def test_window_days_zero_raises(self):
        with pytest.raises(ValueError):
            compute_breadth({}, _series(100, 110), window_days=0)

    def test_non_numeric_closes_are_skipped(self):
        result = compute_breadth(
            {"AAAUSD": _series(100, None, "junk", 101, 102, 103, 104, 105, 106, 120)},
            _series(100, 101, 102, 103, 104, 105, 106, 104),
            window_days=7,
        )
        assert result["members"] == 1
        assert result["leaders"] == [{"ticker": "AAAUSD", "return_pct": 20.0}]


class TestAnalyzeEndToEnd:
    @pytest.fixture(autouse=True)
    def _watchlist_env(self, monkeypatch, tmp_path):
        self.wl_path = _watchlist_file(tmp_path, WATCHLIST)
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(self.wl_path))

    def _patch_fetch(self, monkeypatch, candles_by_ticker, calls=None):
        def fake_fetch_ohlc(ticker, interval="1d", period="1y", source=None, **kw):
            if calls is not None:
                calls.append((ticker, interval, period, source))
            return candles_by_ticker[ticker]

        monkeypatch.setattr(_lib, "fetch_ohlc", fake_fetch_ohlc)

    def test_success_benchmark_excluded_and_payload_shape(self, monkeypatch):
        calls: list[tuple] = []
        self._patch_fetch(monkeypatch, FAKE_CANDLES, calls)
        result = analyze(basket="crypto_alts", window_days=7, benchmark="btc")
        assert result["benchmark"] == "BTCUSD"
        assert result["basket"] == "crypto_alts"
        assert result["members"] == 2  # BTCUSD resolved as benchmark and excluded
        assert result["pct_beating"] == 100.0  # AAA +20% and BBB +10% both beat BTC +8%
        assert result["regime"] == "alt_rotation"
        assert result["btc_return_pct"] == 8.0
        assert [row["ticker"] for row in result["leaders"]] == ["AAAUSD", "BBBUSD"]
        assert "BTCUSD" not in [row["ticker"] for row in result["leaders"] + result["laggards"]]
        assert result["narrative"].startswith("Breadth 7d: 100.0% of 2 members beating BTCUSD")
        assert "uncalibrated first guesses - trust pct_beating, not the label" in result["narrative"]
        # daily closed bars, 1y period, source resolved from watchlist metadata
        assert all(interval == "1d" and period == "1y" and src == "kraken" for _, interval, period, src in calls)

    def test_universe_comes_from_the_watchlist_file(self, monkeypatch, tmp_path):
        alt_path = _watchlist_file(
            tmp_path,
            {
                "baskets": {
                    "crypto_alts": {
                        "CCCUSD": {"source": "kraken"},
                        "BTCUSD": {"source": "kraken"},
                    }
                }
            },
        )
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(alt_path))
        self._patch_fetch(
            monkeypatch,
            {
                "BTCUSD": _series(100, 101, 102, 103, 104, 105, 106, 108),
                "CCCUSD": _series(100, 101, 102, 103, 104, 105, 106, 98),
            },
            None,
        )
        result = analyze(basket="crypto_alts", window_days=7, benchmark="btc")
        assert result["members"] == 1
        assert result["leaders"] == [{"ticker": "CCCUSD", "return_pct": -2.0}]
        assert result["pct_beating"] == 0.0

    def test_member_fetch_failure_named_in_errors(self, monkeypatch):
        candles = dict(FAKE_CANDLES)
        candles["BBBUSD"] = []  # fetch returned nothing

        def fake_fetch_ohlc(ticker, interval="1d", period="1y", source=None, **kw):
            return candles[ticker]

        monkeypatch.setattr(_lib, "fetch_ohlc", fake_fetch_ohlc)
        result = analyze(basket="crypto_alts", window_days=7, benchmark="btc")
        assert result["members"] == 1
        assert any("BBBUSD" in err and "FETCH FAILED" in err for err in result["errors"])

    def test_missing_basket_returns_empty_state_not_exception(self, monkeypatch):
        self._patch_fetch(monkeypatch, FAKE_CANDLES, None)
        result = analyze(basket="nope", window_days=7, benchmark="btc")
        assert result["data"] is None
        assert result["count"] == 0
        assert any("nope" in err for err in result["errors"])
        joined_help = " ".join(result["help"])
        assert "crypto_alts" in joined_help and "crypto_majors" in joined_help

    def test_unresolvable_benchmark_returns_empty_state(self, monkeypatch):
        self._patch_fetch(monkeypatch, FAKE_CANDLES, None)
        result = analyze(basket="crypto_alts", window_days=7, benchmark="zzz")
        assert result["data"] is None
        assert result["count"] == 0
        assert any("zzz" in err for err in result["errors"])

    def test_benchmark_fetch_failure_returns_empty_state(self, monkeypatch):
        candles = dict(FAKE_CANDLES)
        candles["BTCUSD"] = []

        def fake_fetch_ohlc(ticker, interval="1d", period="1y", source=None, **kw):
            return candles[ticker]

        monkeypatch.setattr(_lib, "fetch_ohlc", fake_fetch_ohlc)
        result = analyze(basket="crypto_alts", window_days=7, benchmark="btc")
        assert result["data"] is None
        assert result["count"] == 0
        assert any("BTCUSD" in err and "benchmark" in err for err in result["errors"])


def test_loadable_via_skill_loader():
    from analysis.skill_loader import load_skill

    mod = load_skill("market-breadth")
    assert mod is not None
    assert callable(mod.compute_breadth)
    assert callable(mod.analyze)
