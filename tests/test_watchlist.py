"""Tests for analysis/watchlist — I/O + library functions."""

import json

import pytest

from analysis import watchlist as wl_mod
from analysis.watchlist import WatchlistUnavailableError


@pytest.fixture
def tmp_watchlist_path(tmp_path, monkeypatch):
    """Redirect the watchlist file to a tmp path."""
    path = tmp_path / "watchlist.json"
    monkeypatch.setattr(wl_mod, "_resolve_path", lambda p=None: path if p is None else p)
    return path


SAMPLE = {
    "baskets": {
        "crypto_majors": {
            "BTCUSD": {"tier": 2, "source": "kraken", "label": "BTC"},
            "ETHUSD": {"tier": 2, "source": "kraken", "label": "ETH"},
        },
        "crypto_alts": {
            "hl:<PRIVATE_PERP>": {"tier": 1, "source": "hyperliquid"},
        },
        "macro_refs": {
            "SPYUSD": {"source": "yfinance", "yfinance_ticker": "SPY", "tracking_only": True},
            "XLExUSD": {"source": "yfinance", "yfinance_ticker": "XLE", "tracking_only": True},
        },
    }
}


def test_load_raw_missing_raises(tmp_watchlist_path):
    """Bead market-skills-kwu: a missing watchlist file is fatal, never {}."""
    with pytest.raises(WatchlistUnavailableError):
        wl_mod.load_raw()


def test_save_load_round_trip(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.load_raw() == SAMPLE


def test_all_tickers(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    out = wl_mod.all_tickers()
    assert "BTCUSD" in out
    assert "hl:<PRIVATE_PERP>" in out
    assert "XLExUSD" in out


def test_categories(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.categories() == ["crypto_majors", "crypto_alts", "macro_refs"]


def test_by_category(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.by_category("crypto_majors") == ["BTCUSD", "ETHUSD"]


def test_basket(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    b = wl_mod.basket("crypto_majors")
    assert "BTCUSD" in b


def test_metadata_for(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.metadata_for("BTCUSD")["source"] == "kraken"
    assert wl_mod.metadata_for("NOPE") == {}


def test_provider_for_explicit_prefix(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.provider_for("hl:<PRIVATE_PERP>") == "hyperliquid"


def test_provider_for_via_metadata(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.provider_for("BTCUSD") == "kraken"
    assert wl_mod.provider_for("SPYUSD") == "yfinance"


def test_resolve(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.resolve("btc") == "BTCUSD"
    assert wl_mod.resolve("eth") == "ETHUSD"
    assert wl_mod.resolve("<private_perp>") == "hl:<PRIVATE_PERP>"
    assert wl_mod.resolve("xle") == "XLExUSD"


def test_resolve_unknown(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.resolve("xyz") is None


def test_resolve_ambiguous_raises(tmp_watchlist_path):
    wl_mod.save_raw(
        {
            "baskets": {
                "a": {"FOOUSD": {}},
                "b": {"FOOxUSD": {}},
            }
        }
    )
    with pytest.raises(ValueError):
        wl_mod.resolve("foo")


def test_expand_tickers_passthrough_unknown(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    out = wl_mod.expand_tickers(["btc", "ETHUSD", "hl:<PRIVATE_PERP>", "NOPE"])
    # NOPE doesn't resolve → passes through unchanged
    assert "BTCUSD" in out
    assert "ETHUSD" in out
    assert "hl:<PRIVATE_PERP>" in out
    assert "NOPE" in out


def test_expand_tickers_dedup(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    out = wl_mod.expand_tickers(["btc", "BTC", "btcusd"])
    assert out == ["BTCUSD"]


def test_env_var_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.json"
    monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(custom))
    wl_mod.save_raw(SAMPLE)
    assert custom.exists()
    assert wl_mod.all_tickers() == [
        "BTCUSD",
        "ETHUSD",
        "hl:<PRIVATE_PERP>",
        "SPYUSD",
        "XLExUSD",
    ]


def test_default_path_points_to_skill_data_dir():
    p = wl_mod.default_path()
    assert p.name == "watchlist.json"
    assert "skills" in p.parts
    assert "market-watchlist" in p.parts
    assert "data" in p.parts


def test_atomic_write(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert not tmp_watchlist_path.with_suffix(tmp_watchlist_path.suffix + ".tmp").exists()


# ── fail-loud registry contract (bead market-skills-kwu) ──────────


class TestWatchlistUnavailable:
    """A missing, unreadable, malformed, or empty watchlist must raise —
    an empty result silently collapses every downstream batch/artifact
    to zero while callers report success."""

    def test_missing_file_at_explicit_path_raises(self, tmp_path):
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.load_raw(tmp_path / "nope.json")

    def test_missing_file_at_env_path_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(tmp_path / "nope.json"))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.all_tickers()

    def test_env_unset_and_default_absent_raises_with_env_var_named(self, tmp_path, monkeypatch):
        """env unset + default file absent → raise; the message names
        MARKET_SKILLS_WATCHLIST_PATH and states the in-repo fallback."""
        monkeypatch.delenv("MARKET_SKILLS_WATCHLIST_PATH", raising=False)
        monkeypatch.setattr(wl_mod, "default_path", lambda: tmp_path / "default" / "watchlist.json")
        with pytest.raises(WatchlistUnavailableError) as excinfo:
            wl_mod.categories()
        msg = str(excinfo.value)
        assert "MARKET_SKILLS_WATCHLIST_PATH" in msg
        assert "falls back" in msg
        assert str(tmp_path / "default" / "watchlist.json") in msg

    def test_invalid_json_raises(self, tmp_path, monkeypatch):
        p = tmp_path / "watchlist.json"
        p.write_text("{not json")
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(p))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.load_raw()

    def test_non_utf8_file_raises(self, tmp_path, monkeypatch):
        """A non-UTF-8/corrupt registry makes json.load raise UnicodeDecodeError
        (a ValueError, not json.JSONDecodeError) — it must surface as
        WatchlistUnavailableError, not a raw traceback through the caller."""
        p = tmp_path / "watchlist.json"
        p.write_bytes(b"\xff\xfe\x00\x00\xff")
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(p))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.load_raw()

    def test_non_object_root_raises(self, tmp_path, monkeypatch):
        p = tmp_path / "watchlist.json"
        p.write_text(json.dumps(["not", "an", "object"]))
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(p))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.load_raw()

    def test_zero_baskets_raises(self, tmp_path, monkeypatch):
        p = tmp_path / "watchlist.json"
        p.write_text(json.dumps({"baskets": {}}))
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(p))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.load_raw()

    def test_basket_with_zero_tickers_raises(self, tmp_path, monkeypatch):
        p = tmp_path / "watchlist.json"
        p.write_text(json.dumps({"baskets": {"empty_basket": {}}}))
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(p))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.load_raw()

    def test_accessors_inherit_the_raise(self, tmp_path, monkeypatch):
        """Every accessor funnels through load_raw, so they all raise."""
        p = tmp_path / "watchlist.json"
        p.write_text(json.dumps({"baskets": {}}))
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(p))
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.all_tickers()
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.categories()
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.by_category("crypto_majors")
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.basket("crypto_majors")
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.metadata_for("BTCUSD")
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.provider_for("BTCUSD")
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.resolve("btc")
        with pytest.raises(WatchlistUnavailableError):
            wl_mod.expand_tickers(["btc"])
