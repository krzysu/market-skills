"""Tests for analysis/valuation.py — SP500 CAPE valuation fetcher.

The fetcher is network-dependent (multpl.com + yfinance). Tests mock
both source paths so the math, regime classification, error isolation,
cache, and history ring buffer can be exercised offline.
"""

from unittest.mock import MagicMock, patch

import pytest

from analysis import valuation
from analysis.valuation import (
    _CAPE_50Y_MEAN,
    _CAPE_50Y_STD,
    _classify_regime,
    _fetch_shiller_cape,
    _fetch_sp500_spot,
    append_valuation_tick,
    clear_cache,
    default_history_path,
    fetch_valuation,
    load_history,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


# --- Classification (pure math) --------------------------------------------


class TestRegimeBands:
    def test_overextended_at_two_sigma(self):
        assert _classify_regime(2.5) == "OVEREXTENDED"

    def test_overextended_at_exactly_two(self):
        assert _classify_regime(2.0) == "OVEREXTENDED"

    def test_elevated_between_one_and_two(self):
        assert _classify_regime(1.5) == "ELEVATED"

    def test_elevated_at_exactly_one(self):
        assert _classify_regime(1.0) == "ELEVATED"

    def test_fair_within_one(self):
        assert _classify_regime(0.0) == "FAIR"
        assert _classify_regime(-0.5) == "FAIR"

    def test_depressed_between_neg_one_and_neg_two(self):
        assert _classify_regime(-1.5) == "DEPRESSED"

    def test_oversold_below_neg_two(self):
        assert _classify_regime(-2.5) == "OVERSOLD"
        assert _classify_regime(-3.0) == "OVERSOLD"

    def test_unknown_when_none(self):
        assert _classify_regime(None) == "UNKNOWN"

    def test_unknown_when_nan(self):
        assert _classify_regime(float("nan")) == "UNKNOWN"


# --- Source fetchers -------------------------------------------------------


class TestFetchShillerCape:
    def test_parses_meta_description(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.text = (
            '<html><head><meta name="description" content="'
            "Current Shiller PE Ratio is 41.97, a change of +0.37 from previous market close. "
            '"/></head><body></body></html>'
        )
        with patch.object(valuation.requests, "get", return_value=fake_resp):
            cape, err = _fetch_shiller_cape()
        assert cape == 41.97
        assert err is None

    def test_returns_error_on_http_failure(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 503
        with patch.object(valuation.requests, "get", return_value=fake_resp):
            cape, err = _fetch_shiller_cape()
        assert cape is None
        assert "503" in err

    def test_returns_error_on_parse_miss(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.text = "<html>no cape here</html>"
        with patch.object(valuation.requests, "get", return_value=fake_resp):
            cape, err = _fetch_shiller_cape()
        assert cape is None
        assert "parse miss" in err

    def test_returns_error_on_implausible_value(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        # Trailing garbage after the number — regex should stop cleanly.
        fake_resp.text = '<meta name="description" content="Current Shiller PE Ratio is 250.0 rubbish..." />'
        with patch.object(valuation.requests, "get", return_value=fake_resp):
            cape, err = _fetch_shiller_cape()
        assert cape is None
        assert "implausible" in err

    def test_returns_error_on_timeout(self):
        import requests as req

        with patch.object(valuation.requests, "get", side_effect=req.Timeout("slow")):
            cape, err = _fetch_shiller_cape()
        assert cape is None
        assert "Timeout" in err


class TestFetchSp500Spot:
    def test_returns_price(self):
        info = MagicMock()
        info.last_price = 5400.5
        with patch.object(valuation.yf, "Ticker", return_value=MagicMock(fast_info=info)):
            price, err = _fetch_sp500_spot()
        assert price == 5400.5
        assert err is None

    def test_returns_error_on_no_data(self):
        info = MagicMock()
        info.last_price = None
        with patch.object(valuation.yf, "Ticker", return_value=MagicMock(fast_info=info)):
            price, err = _fetch_sp500_spot()
        assert price is None
        assert "no data" in err

    def test_returns_error_on_exception(self):
        with patch.object(valuation.yf, "Ticker", side_effect=RuntimeError("yfinance down")):
            price, err = _fetch_sp500_spot()
        assert price is None
        assert "RuntimeError" in err


# --- Fetch orchestration ---------------------------------------------------


def _mock_sources(cape: float | None, sp500: float | None, cape_err: str | None = None, sp500_err: str | None = None):
    """Patch both source fetchers to return canned values."""

    def _cape_stub():
        return cape, cape_err

    def _sp500_stub():
        return sp500, sp500_err

    return patch.object(valuation, "_fetch_shiller_cape", side_effect=_cape_stub), patch.object(
        valuation, "_fetch_sp500_spot", side_effect=_sp500_stub
    )


class TestFetchValuationHappyPath:
    def test_zscore_uses_documented_constants(self):
        cape = 41.0
        expected_z = round((cape - _CAPE_50Y_MEAN) / _CAPE_50Y_STD, 3)
        cape_stub, sp500_stub = _mock_sources(cape, 5400.0)
        with cape_stub, sp500_stub:
            signal = fetch_valuation(ttl_seconds=0, write_history=False)
        assert signal["regime"]["cape_zscore"] == expected_z
        assert signal["regime"]["regime"] == "OVEREXTENDED"
        assert signal["inputs"]["cape"] == 41.0
        assert signal["inputs"]["cape_mean_50y"] == _CAPE_50Y_MEAN
        assert signal["inputs"]["cape_std_50y"] == _CAPE_50Y_STD
        assert signal["errors"] == []
        assert signal["incomplete"] is False

    def test_zscore_zero_when_cape_at_mean(self):
        cape_stub, sp500_stub = _mock_sources(_CAPE_50Y_MEAN, 5400.0)
        with cape_stub, sp500_stub:
            signal = fetch_valuation(ttl_seconds=0, write_history=False)
        assert signal["regime"]["cape_zscore"] == 0.0
        assert signal["regime"]["regime"] == "FAIR"

    def test_zscore_negative_below_mean(self):
        cape = 2.0  # well below mean → z = (2-21)/9 = -2.11, OVERSOLD band
        cape_stub, sp500_stub = _mock_sources(cape, 5400.0)
        with cape_stub, sp500_stub:
            signal = fetch_valuation(ttl_seconds=0, write_history=False)
        assert signal["regime"]["cape_zscore"] < -2.0
        assert signal["regime"]["regime"] == "OVERSOLD"

    def test_regime_note_includes_zscore(self):
        cape_stub, sp500_stub = _mock_sources(41.0, 5400.0)
        with cape_stub, sp500_stub:
            signal = fetch_valuation(ttl_seconds=0, write_history=False)
        assert "41.0" in signal["regime_note"]
        assert "overextended" in signal["regime_note"].lower()


class TestFetchValuationErrorIsolation:
    def test_both_sources_down_returns_unknown(self):
        cape_stub, sp500_stub = _mock_sources(None, None, cape_err="multpl: Timeout", sp500_err="sp500_yf: no data")
        with cape_stub, sp500_stub:
            signal = fetch_valuation(ttl_seconds=0, write_history=False)
        assert signal["regime"]["regime"] == "UNKNOWN"
        assert signal["regime"]["cape_zscore"] is None
        assert signal["inputs"]["cape"] is None
        assert signal["inputs"]["sp500"] is None
        assert "multpl: Timeout" in signal["errors"]
        assert "sp500_yf: no data" in signal["errors"]
        assert signal["incomplete"] is True

    def test_only_cape_down_returns_unknown_with_partial(self):
        cape_stub, sp500_stub = _mock_sources(None, 5400.0, cape_err="multpl: 503")
        with cape_stub, sp500_stub:
            signal = fetch_valuation(ttl_seconds=0, write_history=False)
        # Per macro.py precedent: any source failure downgrades headline label to UNKNOWN.
        assert signal["regime"]["regime"] == "UNKNOWN"
        assert signal["inputs"]["sp500"] == 5400.0
        assert signal["incomplete"] is True
        assert signal["regime_note"].startswith("[VALUATION INCOMPLETE")


class TestFetchValuationCache:
    def test_ttl_hits_cache_without_refetch(self):
        cape_stub, sp500_stub = _mock_sources(30.0, 5000.0)
        with cape_stub, sp500_stub:
            first = fetch_valuation(ttl_seconds=300, write_history=False)
            second = fetch_valuation(ttl_seconds=300, write_history=False)
        assert first is second

    def test_zero_ttl_disables_cache(self):
        call_count = {"cape": 0, "sp500": 0}

        def _cape():
            call_count["cape"] += 1
            return 30.0, None

        def _sp500():
            call_count["sp500"] += 1
            return 5000.0, None

        with (
            patch.object(valuation, "_fetch_shiller_cape", side_effect=_cape),
            patch.object(valuation, "_fetch_sp500_spot", side_effect=_sp500),
        ):
            fetch_valuation(ttl_seconds=0, write_history=False)
            fetch_valuation(ttl_seconds=0, write_history=False)
        assert call_count["cape"] == 2
        assert call_count["sp500"] == 2


# --- History ring buffer ---------------------------------------------------


class TestHistoryRingBuffer:
    def test_append_and_load_roundtrip(self, tmp_path, monkeypatch):
        path = tmp_path / "valuation_history.json"
        signal = {
            "timestamp": "2026-07-07T00:00:00+00:00",
            "inputs": {"sp500": 5400.0, "cape": 41.0, "cape_mean_50y": 21.0, "cape_std_50y": 9.0},
            "regime": {"cape_zscore": 2.222, "regime": "OVEREXTENDED"},
            "errors": [],
            "incomplete": False,
            "regime_note": "Valuation: CAPE 41.0 (+2.22σ) — overextended.",
        }
        assert append_valuation_tick(signal, path=path) == 1
        loaded = load_history(path=path)
        assert len(loaded) == 1
        assert loaded[0]["cape"] == 41.0
        assert loaded[0]["cape_zscore"] == 2.222
        assert loaded[0]["regime"] == "OVEREXTENDED"

    def test_cap_at_200_entries(self, tmp_path):
        path = tmp_path / "valuation_history.json"
        for i in range(210):
            append_valuation_tick(
                {
                    "timestamp": f"2026-07-07T00:00:{i % 60:02d}+00:00",
                    "inputs": {"sp500": 5400.0, "cape": float(i)},
                    "regime": {"cape_zscore": 0.0, "regime": "FAIR"},
                    "errors": [],
                    "incomplete": False,
                    "regime_note": "x",
                },
                path=path,
            )
        loaded = load_history(path=path)
        assert len(loaded) == 200

    def test_default_history_path_raises_when_xdg_unset(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        with pytest.raises(OSError, match="XDG_DATA_HOME"):
            default_history_path()

    def test_safe_append_does_not_crash_on_bad_path(self, monkeypatch):
        # Should silently swallow OSError and never raise into the fetcher.
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        valuation._safe_append_tick(
            {"timestamp": "now", "inputs": {}, "regime": {}, "errors": [], "incomplete": False, "regime_note": ""}
        )
        # No assertion needed; passing means it didn't raise.
