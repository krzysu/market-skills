"""Tests for analysis.macro — fetcher, classifier, history store, TTL cache.

The fetcher touches 3 sources (Alternative.me HTTP, CoinGecko HTTP,
yfinance ``fast_info``). All three are mocked here so the tests run
in CI without network. The classifier is pure math; it gets its
own direct tests. The history store mirrors ``analysis.chop``'s
test surface (XDG resolution, roundtrip, cap, malformed).
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from analysis import macro
from analysis.macro.fetchers import _fetch_yf_market_cap, _fetch_yf_price

# --- Fakes for yfinance.fast_info --------------------------------------------


class FakeFastInfo:
    def __init__(self, last_price=None, market_cap=None):
        self.last_price = last_price
        self.market_cap = market_cap


_FAST_INFO_BY_SYMBOL: dict[str, FakeFastInfo] = {}


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def fast_info(self):
        if self.symbol not in _FAST_INFO_BY_SYMBOL:
            raise KeyError(f"no fast_info fixture for {self.symbol!r}")
        return _FAST_INFO_BY_SYMBOL[self.symbol]


def _set_fast_info(symbol: str, **kw) -> None:
    _FAST_INFO_BY_SYMBOL[symbol] = FakeFastInfo(**kw)


# --- Fakes for requests.get --------------------------------------------------


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _fng_payload(value=22, label="Extreme Fear"):
    return {
        "name": "Fear and Greed Index",
        "data": [{"value": str(value), "value_classification": label, "timestamp": "1700000000"}],
        "metadata": {"error": None},
    }


def _coingecko_payload(total_usd=2_000_000_000_000.0, btc_pct=53.0):
    return {
        "data": {
            "active_cryptocurrencies": 12000,
            "total_market_cap": {"usd": total_usd, "btc": total_usd * (btc_pct / 100)},
            "total_volume": {"usd": 50_000_000_000.0},
            "market_cap_percentage": {"btc": btc_pct, "eth": 17.0},
            "market_cap_change_percentage_24h_usd": 1.2,
        }
    }


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache_and_fixtures(monkeypatch):
    macro.clear_cache()
    _FAST_INFO_BY_SYMBOL.clear()

    # Route yfinance.Ticker through our fake so the fetcher never hits
    # the network. The fake's fast_info raises KeyError for symbols
    # not in _FAST_INFO_BY_SYMBOL — tests that want a yfinance failure
    # simply omit the symbol from the fixture.
    monkeypatch.setattr("analysis.macro.fetchers.yf.Ticker", FakeTicker)

    yield
    macro.clear_cache()
    _FAST_INFO_BY_SYMBOL.clear()


# --- Classifier: risk_appetite -----------------------------------------------


class TestRiskAppetite:
    def test_low_vix_is_risk_on(self):
        assert macro._classify_risk_appetite(12.0, 100.0, 3.0) == "RISK_ON"

    def test_mid_vix_is_neutral(self):
        assert macro._classify_risk_appetite(20.0, 100.0, 3.0) == "NEUTRAL"

    def test_high_vix_is_risk_off(self):
        assert macro._classify_risk_appetite(30.0, 100.0, 3.0) == "RISK_OFF"

    def test_extreme_vix_is_crisis(self):
        assert macro._classify_risk_appetite(40.0, 100.0, 3.0) == "CRISIS"

    def test_none_vix_is_neutral(self):
        assert macro._classify_risk_appetite(None, 100.0, 3.0) == "NEUTRAL"

    def test_dxy_cap_pulls_risk_on_to_neutral(self):
        # VIX calm but DXY very strong → cap
        assert macro._classify_risk_appetite(12.0, 106.0, 3.0) == "NEUTRAL"

    def test_us10y_cap_pulls_risk_on_to_neutral(self):
        assert macro._classify_risk_appetite(12.0, 100.0, 5.0) == "NEUTRAL"

    def test_cap_does_not_suppress_risk_off_or_crisis(self):
        # Cap only pulls RISK_ON / NEUTRAL down — risk-off stays risk-off
        assert macro._classify_risk_appetite(30.0, 106.0, 5.0) == "RISK_OFF"
        assert macro._classify_risk_appetite(40.0, 106.0, 5.0) == "CRISIS"


# --- Classifier: liquidity ---------------------------------------------------


class TestLiquidity:
    def test_low_yield_is_easy(self):
        assert macro._classify_liquidity(3.0, 15.0) == "EASY"

    def test_mid_yield_is_tightening(self):
        assert macro._classify_liquidity(4.0, 15.0) == "TIGHTENING"

    def test_high_yield_is_tight(self):
        assert macro._classify_liquidity(5.0, 15.0) == "TIGHT"

    def test_high_yield_plus_high_vix_is_stress(self):
        assert macro._classify_liquidity(5.0, 30.0) == "STRESS"

    def test_easy_yield_never_stress(self):
        # Even with high VIX, easy yield stays EASY
        assert macro._classify_liquidity(2.0, 40.0) == "EASY"

    def test_none_yield_defaults_to_tightening(self):
        assert macro._classify_liquidity(None, 15.0) == "TIGHTENING"

    def test_tightening_plus_high_vix_stays_tightening(self):
        # STRESS is reserved for the joint TIGHT+TIGHTENING + high-vol
        # case; TIGHTENING + high VIX alone doesn't escalate
        assert macro._classify_liquidity(4.0, 30.0) == "TIGHTENING"


# --- Classifier: sentiment ---------------------------------------------------


class TestSentiment:
    def test_extreme_fear(self):
        assert macro._classify_sentiment(10.0) == "EXTREME_FEAR"

    def test_fear(self):
        assert macro._classify_sentiment(35.0) == "FEAR"

    def test_neutral_low(self):
        assert macro._classify_sentiment(50.0) == "NEUTRAL"

    def test_greed(self):
        assert macro._classify_sentiment(65.0) == "GREED"

    def test_extreme_greed(self):
        assert macro._classify_sentiment(80.0) == "EXTREME_GREED"

    def test_none_is_neutral(self):
        assert macro._classify_sentiment(None) == "NEUTRAL"

    def test_boundaries(self):
        assert macro._classify_sentiment(25.0) == "FEAR"  # < 25 was EXTREME_FEAR
        assert macro._classify_sentiment(45.0) == "NEUTRAL"  # < 45 was FEAR
        assert macro._classify_sentiment(55.0) == "GREED"  # < 55 was NEUTRAL
        assert macro._classify_sentiment(75.0) == "EXTREME_GREED"  # < 75 was GREED


# --- _format_regime_note -----------------------------------------------------


class TestRegimeNote:
    def test_known_posture_lookup(self):
        note = macro._format_regime_note("RISK_OFF", "TIGHTENING", "EXTREME_FEAR", {"vix": 28.4, "fng": 22.0})
        assert "risk-off" in note
        assert "tightening" in note
        assert "extreme fear" in note
        assert "defensive" in note
        assert "VIX 28.4" in note
        assert "F&G 22" in note

    def test_risk_on_posture(self):
        note = macro._format_regime_note("RISK_ON", "EASY", "GREED", {})
        assert "constructive" in note
        assert "risk-on" in note

    def test_neutral_posture_is_selective(self):
        note = macro._format_regime_note("NEUTRAL", "EASY", "NEUTRAL", {})
        assert "selective" in note

    def test_unknown_combo_falls_through(self):
        # If for any reason the lookup misses, the fallback string
        # should still be human-readable.
        note = macro._format_regime_note("FOO", "BAR", "BAZ", {})
        assert "FOO" in note or "foo" in note


# --- TTL cache ---------------------------------------------------------------


class TestTtlCache:
    def test_cache_disabled_with_ttl_zero(self):
        # Two calls with ttl=0 should both call the underlying sources.
        # We count requests.get invocations as the proxy.
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(30)),
                FakeResponse(payload=_coingecko_payload()),
                FakeResponse(payload=_fng_payload(30)),
                FakeResponse(payload=_coingecko_payload()),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_100_000_000_000.0)

            macro.fetch_regime(ttl_seconds=0, write_history=False)
            macro.fetch_regime(ttl_seconds=0, write_history=False)
        # F&G + CoinGecko = 2 calls per fetch, x2 fetches = 4
        assert mock_get.call_count == 4

    def test_cache_hit_skips_network(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(30)),
                FakeResponse(payload=_coingecko_payload()),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_100_000_000_000.0)

            a = macro.fetch_regime(ttl_seconds=300, write_history=False)
            b = macro.fetch_regime(ttl_seconds=300, write_history=False)
        assert a is b
        # Only the first call should have hit the network
        assert mock_get.call_count == 2

    def test_clear_cache_forces_refetch(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(30)),
                FakeResponse(payload=_coingecko_payload()),
                FakeResponse(payload=_fng_payload(30)),
                FakeResponse(payload=_coingecko_payload()),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_100_000_000_000.0)

            macro.fetch_regime(ttl_seconds=300, write_history=False)
            macro.clear_cache()
            macro.fetch_regime(ttl_seconds=300, write_history=False)
        assert mock_get.call_count == 4


# --- History store -----------------------------------------------------------


class TestHistoryStore:
    def _tmp_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        return path

    def _sample_signal(self, **overrides):
        signal = {
            "timestamp": "2026-06-25T12:34:56+00:00",
            "inputs": {
                "fng": 22.0,
                "fng_label": "Extreme Fear",
                "vix": 28.4,
                "dxy": 104.1,
                "us10y": 4.32,
                "btc_dominance": 53.8,
                "btc_dominance_source": "yf",
                "total_mcap_usd": 2.41e12,
            },
            "regime": {
                "risk_appetite": "RISK_OFF",
                "liquidity": "TIGHTENING",
                "sentiment": "EXTREME_FEAR",
            },
            "errors": [],
            "regime_note": "test",
        }
        signal.update(overrides)
        return signal

    def test_append_then_load_roundtrip(self):
        path = self._tmp_path()
        try:
            n = macro.append_macro_tick(self._sample_signal(), path=path)
            assert n == 1
            history = macro.load_history(path)
            assert len(history) == 1
            assert history[0]["risk_appetite"] == "RISK_OFF"
            assert history[0]["vix"] == 28.4
            assert "ts" in history[0]
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_load_history_missing_returns_empty(self):
        path = self._tmp_path()
        try:
            assert macro.load_history(path) == []
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_load_history_malformed_returns_empty(self):
        path = self._tmp_path()
        with open(path, "w") as f:
            f.write("not json")
        try:
            assert macro.load_history(path) == []
        finally:
            os.unlink(path)

    def test_append_caps_at_200(self):
        path = self._tmp_path()
        try:
            for i in range(250):
                macro.append_macro_tick(self._sample_signal(), path=path)
            history = macro.load_history(path)
            assert len(history) == 200
            # The cap is a tail-trim — last entry should be the last appended
            assert history[-1]["ts"] == self._sample_signal()["timestamp"]
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_non_dict_signal_returns_zero(self):
        path = self._tmp_path()
        try:
            assert macro.append_macro_tick("not a dict", path=path) == 0
            assert macro.load_history(path) == []
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_default_history_path_uses_xdg_data_home(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
        assert macro.default_history_path() == "/custom/xdg/market-skills/macro_history.json"

    def test_default_history_path_requires_xdg_data_home(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        with pytest.raises(OSError, match="XDG_DATA_HOME"):
            macro.default_history_path()


# --- fetch_regime integration: full happy path -------------------------------


class TestFetchRegimeHappyPath:
    def test_full_success_shape(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(22, "Extreme Fear")),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 55.0)),
            ]
            _set_fast_info("^VIX", last_price=18.5)
            _set_fast_info("DX-Y.NYB", last_price=102.3)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_100_000_000_000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        # Top-level shape
        assert "timestamp" in sig
        assert "inputs" in sig
        assert "regime" in sig
        assert "errors" in sig
        assert "regime_note" in sig
        # Inputs
        assert sig["inputs"]["fng"] == 22.0
        assert sig["inputs"]["fng_label"] == "Extreme Fear"
        assert sig["inputs"]["vix"] == 18.5
        assert sig["inputs"]["dxy"] == 102.3
        assert sig["inputs"]["us10y"] == 4.0
        # btc_dominance derived: 1100 / 2000 * 100 = 55.0
        assert sig["inputs"]["btc_dominance"] == pytest.approx(55.0, abs=0.01)
        assert sig["inputs"]["btc_dominance_source"] == "yf"
        assert sig["inputs"]["total_mcap_usd"] == 2_000_000_000_000.0
        # Regime
        assert sig["regime"]["risk_appetite"] == "NEUTRAL"  # VIX 18.5
        assert sig["regime"]["liquidity"] == "TIGHTENING"  # US10Y 4.0
        assert sig["regime"]["sentiment"] == "EXTREME_FEAR"  # F&G 22
        assert sig["errors"] == []
        assert sig["regime_note"]


# --- fetch_regime: error isolation -------------------------------------------


class TestFetchRegimeErrorIsolation:
    def test_fng_down_still_returns_others(self):
        import requests as _real_requests

        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            # F&G raises (timeout), CoinGecko ok
            mock_get.side_effect = [
                _real_requests.Timeout("simulated"),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 50.0)),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_000_000_000_000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["inputs"]["fng"] is None
        assert sig["inputs"]["vix"] == 18.0
        assert sig["inputs"]["dxy"] == 102.0
        assert sig["inputs"]["us10y"] == 4.0
        # btc_dominance still derivable
        assert sig["inputs"]["btc_dominance"] == pytest.approx(50.0, abs=0.01)
        assert sig["inputs"]["btc_dominance_source"] == "yf"
        # error recorded
        assert any("fng" in e for e in sig["errors"])
        # sentiment falls back to NEUTRAL when fng missing
        assert sig["regime"]["sentiment"] == "NEUTRAL"

    def test_coingecko_down_loses_btc_dominance_but_keeps_others(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(60, "Greed")),
                FakeResponse(status_code=429),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_000_000_000_000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        # Other inputs still returned
        assert sig["inputs"]["fng"] == 60.0
        assert sig["inputs"]["vix"] == 18.0
        # btc_dominance and total_mcap_usd are None (coingecko is the
        # only source for total_mcap_usd, and btc_dominance is derived
        # from total_mcap_usd)
        assert sig["inputs"]["btc_dominance"] is None
        assert sig["inputs"]["btc_dominance_source"] is None
        assert sig["inputs"]["total_mcap_usd"] is None
        assert any("coingecko" in e for e in sig["errors"])

    def test_yfinance_one_ticker_down_partial(self):
        """If VIX fetch fails (yfinance raises), DXY/US10Y/BTC mcap still work.

        ``errors`` is non-empty so ``risk_appetite`` is downgraded to
        UNKNOWN (see ``test_missing_input_marks_incomplete`` for the
        canonical fix-shape fixture). Liquidity + sentiment keep their
        best-effort labels so the LLM can still narrate them.
        """
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 50.0)),
            ]
            # ^VIX has no entry in _FAST_INFO_BY_SYMBOL → FakeTicker raises
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_000_000_000_000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["inputs"]["vix"] is None
        assert sig["inputs"]["dxy"] == 102.0
        assert sig["inputs"]["us10y"] == 4.0
        assert any("vix" in e for e in sig["errors"])
        # VIX missing → errors populated → headline axis downgraded to UNKNOWN.
        assert sig["regime"]["risk_appetite"] == "UNKNOWN"
        assert sig["incomplete"] is True

    def test_btc_dominance_falls_back_to_coingecko_when_yfinance_mcap_missing(self):
        """yfinance often returns None for crypto market_cap; CoinGecko's
        pre-computed market_cap_percentage.btc is the fallback path.

        Per-fix fixture for BUGS-2026-07-13 macro_fallback_success_marks_incomplete:
        primary-source error (btc_mcap) must NOT poison the regime once
        the fallback has populated the canonical input. Specifically:

          - missing_inputs == []
          - incomplete is False
          - risk_appetite keeps its native classifier output (NEUTRAL,
            not downgraded to UNKNOWN)
          - regime_note does NOT start with [REGIME INCOMPLETE
          - errors[] retains the raw primary-source diagnostic so a
            human can still see WHY yfinance failed
        """
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 54.7)),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            # BTC-USD has no market_cap entry → fallback triggered
            _set_fast_info("BTC-USD", last_price=65000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        # No derived BTC.D (need both mcaps)
        # But fallback returns CoinGecko's 54.7
        assert sig["inputs"]["btc_dominance"] == pytest.approx(54.7, abs=0.01)
        assert sig["inputs"]["btc_dominance_source"] == "coingecko"
        assert sig["inputs"]["total_mcap_usd"] == 2_000_000_000_000.0
        # Raw primary-source diagnostic retained for humans
        assert any("btc_mcap" in e for e in sig["errors"])
        # The fix: canonical inputs are non-null → missing_inputs empty
        # and the headline regime keeps its native classification.
        assert sig["missing_inputs"] == []
        assert sig["incomplete"] is False
        # VIX 18 + DXY 102 + US10Y 4.0 → NEUTRAL band; no downgrade.
        assert sig["regime"]["risk_appetite"] == "NEUTRAL"
        # No [REGIME INCOMPLETE] prefix on the note.
        assert not sig["regime_note"].startswith("[REGIME INCOMPLETE")
        assert sig["regime_note"].startswith("Macro: ")

    def test_btc_dominance_missing_when_both_providers_fail(self):
        """Negative counterpart of the fallback-success fixture:
        when BOTH yfinance BTC-USD market_cap AND CoinGecko's
        BTC dominance are unavailable, the canonical input is genuinely
        missing — incomplete must stay True, risk_appetite must be
        downgraded to UNKNOWN, and the [REGIME INCOMPLETE] prefix
        must remain on the regime note.
        """
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            # CoinGecko returns 200 but with total_mcap_usd=0 — that's
            # rejected by the fetcher as "missing total_market_cap.usd".
            # btc_mcap also missing → both paths exhausted.
            cg_payload = {
                "data": {
                    "active_cryptocurrencies": 12000,
                    "total_market_cap": {"usd": None, "btc": None},
                    "total_volume": {"usd": 50_000_000_000.0},
                    "market_cap_percentage": {"btc": None, "eth": 17.0},
                    "market_cap_change_percentage_24h_usd": 1.2,
                }
            }
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=cg_payload),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", last_price=65000.0)  # no market_cap

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["inputs"]["btc_dominance"] is None
        assert sig["inputs"]["btc_dominance_source"] is None
        assert sig["inputs"]["total_mcap_usd"] is None
        # Genuine canonical-input gap → conservative behaviour holds.
        assert "btc_dominance" in sig["missing_inputs"]
        assert "total_mcap_usd" in sig["missing_inputs"]
        assert sig["incomplete"] is True
        assert sig["regime"]["risk_appetite"] == "UNKNOWN"
        assert sig["regime_note"].startswith("[REGIME INCOMPLETE")
        # Raw diagnostics still surfaced.
        assert any("btc_mcap" in e for e in sig["errors"])

    def test_all_sources_down_returns_safe_defaults(self):
        import requests as _real_requests

        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = _real_requests.ConnectionError("nope")
            # No yfinance fixtures either
            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        # All inputs None
        for v in sig["inputs"].values():
            assert v is None
        # Safe-default regime labels — risk_appetite downgraded to UNKNOWN
        # because errors[] is non-empty (the headline axis must not silently
        # read as RISK_ON / RISK_OFF when sources are down).
        assert sig["regime"]["risk_appetite"] == "UNKNOWN"
        assert sig["regime"]["liquidity"] == "TIGHTENING"
        assert sig["regime"]["sentiment"] == "NEUTRAL"
        # Errors populated
        assert len(sig["errors"]) >= 2
        # Note still renders without VIX/F&G suffix
        assert sig["regime_note"]
        # btc_dominance_source explicitly None when neither path produced a value
        assert sig["inputs"]["btc_dominance_source"] is None

    def test_clean_run_is_complete(self):
        """Happy-path fetch → incomplete=False, risk_appetite keeps its
        native classifier output (NEUTRAL or better, never UNKNOWN)."""
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 50.0)),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", market_cap=1_000_000_000_000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["incomplete"] is False
        assert sig["errors"] == []
        # VIX 18 + DXY 102 + US10Y 4.0 → NEUTRAL band; no downgrade.
        assert sig["regime"]["risk_appetite"] == "NEUTRAL"
        assert sig["regime_note"].startswith("Macro: ")

    def test_missing_input_marks_incomplete(self):
        """Per-fix fixture for the missing-input-marks-incomplete shape:

        when one input source returns None (provider outage, rate-limit,
        schema change), ``regime.incomplete`` must be True and
        ``regime.risk_appetite`` must be downgraded to UNKNOWN so naive
        consumers don't trust a partial regime. The regime_consistency
        policy in ``analysis.risk.spot`` reads these fields and fires
        CONCERN when risk_appetite is UNKNOWN.
        """
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            # CoinGecko returns 429 → btc_mcap + total_mcap unavailable.
            # The other 5 inputs (F&G, VIX, DXY, US10Y, plus the
            # yfinance partial mcap path) still populate. Only 1 of 6
            # sources fails here, exactly the brief's evidence shape.
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(60, "Greed")),
                FakeResponse(status_code=429),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", last_price=65000.0)  # no market_cap

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        # The fix: incomplete flag is True and risk_appetite is UNKNOWN.
        assert sig["incomplete"] is True
        assert sig["regime"]["risk_appetite"] == "UNKNOWN"
        # Liquidity + sentiment still surface best-effort labels so the
        # LLM can narrate them; risk_appetite is the headline axis and
        # the one the policy reads.
        assert sig["regime"]["liquidity"] in ("EASY", "TIGHTENING", "TIGHT", "STRESS")
        assert sig["regime"]["sentiment"] in ("EXTREME_FEAR", "FEAR", "NEUTRAL", "GREED", "EXTREME_GREED")
        # Errors list contains the CoinGecko failure label.
        assert any("coingecko" in e for e in sig["errors"])
        # Note prefixed with the incomplete marker.
        assert sig["regime_note"].startswith("[REGIME INCOMPLETE")


class TestBtcDominanceSource:
    """Per-fix fixture for audit 2026-06-25 #1: the history ring buffer
    needs to record which pipeline produced btc_dominance so a backtest
    reading macro_history.json later can distinguish yfinance-derived
    readings from CoinGecko-fallback readings. The earlier test
    ``test_btc_dominance_falls_back_to_coingecko_when_yfinance_mcap_missing``
    covers the value; these tests pin the ``btc_dominance_source`` field
    against all three terminal states."""

    def test_primary_path_records_yf_source(self):
        """yfinance has BTC mcap + CoinGecko has total mcap → primary
        derivation; source must be 'yf'."""
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 50.0)),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", last_price=65000.0, market_cap=1_100_000_000_000.0)

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["inputs"]["btc_dominance_source"] == "yf"
        assert sig["inputs"]["btc_dominance"] == pytest.approx(55.0, abs=0.01)

    def test_fallback_path_records_coingecko_source(self):
        """yfinance BTC mcap missing → fallback to CoinGecko's pre-computed
        BTC.D; source must be 'coingecko'."""
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=_coingecko_payload(2_000_000_000_000.0, 54.7)),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", last_price=65000.0)  # no market_cap

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["inputs"]["btc_dominance_source"] == "coingecko"
        assert sig["inputs"]["btc_dominance"] == pytest.approx(54.7, abs=0.01)

    def test_both_paths_failed_records_none_source(self):
        """Both pipelines missing BTC.D and total_mcap → source None,
        and the field is still present in the payload (not omitted)."""
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            # Both total_mcap_usd and BTC.D absent in the /global payload;
            # yfinance BTC mcap also missing. Source must be None.
            cg_payload = {
                "data": {
                    "active_cryptocurrencies": 12000,
                    "total_market_cap": {"usd": None, "btc": None},
                    "total_volume": {"usd": 50_000_000_000.0},
                    "market_cap_percentage": {"btc": None, "eth": 17.0},
                    "market_cap_change_percentage_24h_usd": 1.2,
                }
            }
            mock_get.side_effect = [
                FakeResponse(payload=_fng_payload(50)),
                FakeResponse(payload=cg_payload),
            ]
            _set_fast_info("^VIX", last_price=18.0)
            _set_fast_info("DX-Y.NYB", last_price=102.0)
            _set_fast_info("^TNX", last_price=4.0)
            _set_fast_info("BTC-USD", last_price=65000.0)  # no market_cap

            sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["inputs"]["btc_dominance_source"] is None
        assert sig["inputs"]["btc_dominance"] is None
        # field present, not omitted — backtests reading the history JSON
        # shouldn't have to do .get() with a default
        assert "btc_dominance_source" in sig["inputs"]

    def test_history_entry_carries_source_field(self):
        """End-to-end: append_tick → load_history preserves the source
        field so a later backtest reading the ring buffer sees it."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        try:
            signal = {
                "timestamp": "2026-06-25T12:34:56+00:00",
                "inputs": {
                    "fng": 22.0,
                    "fng_label": "Extreme Fear",
                    "vix": 28.4,
                    "dxy": 104.1,
                    "us10y": 4.32,
                    "btc_dominance": 54.7,
                    "btc_dominance_source": "coingecko",
                    "total_mcap_usd": 2.41e12,
                },
                "regime": {
                    "risk_appetite": "RISK_OFF",
                    "liquidity": "TIGHTENING",
                    "sentiment": "EXTREME_FEAR",
                },
                "errors": [],
                "regime_note": "test",
            }
            macro.append_macro_tick(signal, path=path)
            history = macro.load_history(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

        assert len(history) == 1
        assert history[0]["btc_dominance_source"] == "coingecko"


# --- _fetch_fng: input validation --------------------------------------------


class TestFetchFng:
    def test_non_200(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.return_value = FakeResponse(status_code=503)
            value, label, err = macro._fetch_fng()
        assert value is None
        assert label is None
        assert "503" in err

    def test_empty_payload(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.return_value = FakeResponse(payload={"data": []})
            value, label, err = macro._fetch_fng()
        assert value is None
        assert "empty" in err

    def test_non_numeric_value(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.return_value = FakeResponse(payload={"data": [{"value": "abc"}]})
            value, label, err = macro._fetch_fng()
        assert value is None
        assert "non-numeric" in err

    def test_invalid_json(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            r = FakeResponse()
            r.json = MagicMock(side_effect=ValueError("bad"))
            mock_get.return_value = r
            value, label, err = macro._fetch_fng()
        assert value is None
        assert "invalid json" in err


# --- _fetch_coingecko: input validation -------------------------------------


class TestFetchCoingecko:
    def test_non_200(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.return_value = FakeResponse(status_code=429)
            total, _, err = macro._fetch_coingecko()
        assert total is None
        assert "429" in err

    def test_missing_total_market_cap(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.return_value = FakeResponse(payload={"data": {"total_market_cap": {}}})
            total, _, err = macro._fetch_coingecko()
        assert total is None
        assert "missing" in err

    def test_total_must_be_positive(self):
        with patch("analysis.macro.fetchers.requests.get") as mock_get:
            mock_get.return_value = FakeResponse(payload={"data": {"total_market_cap": {"usd": -1}}})
            total, _, err = macro._fetch_coingecko()
        assert total is None
        assert "missing" in err


# --- _fetch_yf_* lazy property crash guards ----------------------------------


class TestYfLazyPropertyCrash:
    """Per-fix fixture for BUGS-2026-07-10: yfinance ``fast_info`` is a
    lazy proxy — ``yf.Ticker(symbol).fast_info`` returns a ``FastInfo``
    object without fetching data. The actual network call + metadata
    parse happens when a property like ``.last_price`` or ``.market_cap``
    is accessed, which is a ``@property`` getter that can raise
    ``KeyError('exchangeTimezoneName')`` for symbols like ``^VIX`` whose
    history metadata is incomplete.

    Pre-fix code only guarded ``yf.Ticker(symbol).fast_info`` (the
    constructor), not the subsequent property access — so the
    ``KeyError`` propagated out of ``_fetch_yf_price`` /
    ``_fetch_yf_market_cap`` and crashed every downstream consumer
    (morning-brief, swing-scan, daily-trade-pick, run-all-l3 macro
    envelope). Post-fix code extends the ``try`` block to cover the
    property access so the function honours its error-isolated return
    contract: ``(None, str)`` tuples, never an exception.
    """

    def test_fetch_yf_price_lazy_property_crash(self):
        """``_fetch_yf_price`` catches KeyError raised inside the
        ``last_price`` property getter, returns (None, err)."""

        class BrokenFastInfo:
            @property
            def last_price(self):
                raise KeyError("exchangeTimezoneName")

            @property
            def market_cap(self):
                raise KeyError("exchangeTimezoneName")

        # Register a BrokenFastInfo under a fresh symbol so the
        # autouse-mocked FakeTicker returns it. ``^VIX-BROKEN`` is
        # never touched by the rest of the suite.
        _FAST_INFO_BY_SYMBOL["^VIX-BROKEN"] = BrokenFastInfo()

        price, err = _fetch_yf_price("^VIX-BROKEN", "vix")
        assert price is None
        assert err is not None
        assert "KeyError" in err

        cap, cap_err = _fetch_yf_market_cap("^VIX-BROKEN", "vix")
        assert cap is None
        assert cap_err is not None
        assert "KeyError" in cap_err

    def test_fetch_yf_price_value_error_in_getter(self):
        """``_fetch_yf_price`` catches generic ``ValueError`` raised in
        the property getter — same shape, different exception class."""

        class NaNPropertyFastInfo:
            @property
            def last_price(self):
                raise ValueError("simulated yfinance internal failure")

        _FAST_INFO_BY_SYMBOL["^NAN-BROKEN"] = NaNPropertyFastInfo()
        price, err = _fetch_yf_price("^NAN-BROKEN", "vix")
        assert price is None
        assert "ValueError" in err

    def test_fetch_yf_price_happy_path_unchanged(self):
        """Sanity: post-fix code still returns the price for the
        normal path — only the try/except boundary moved, the happy
        path is byte-identical in behaviour."""
        _set_fast_info("^VIX-OK", last_price=18.5)
        price, err = _fetch_yf_price("^VIX-OK", "vix")
        assert price == 18.5
        assert err is None

    def test_fetch_yf_market_cap_happy_path_unchanged(self):
        _set_fast_info("BTC-USD-OK", market_cap=1_100_000_000_000.0)
        cap, err = _fetch_yf_market_cap("BTC-USD-OK", "btc_mcap")
        assert cap == 1_100_000_000_000.0
        assert err is None
