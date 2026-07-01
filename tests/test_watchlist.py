"""Tests for analysis/watchlist — I/O + library functions."""

import pytest

from analysis import watchlist as wl_mod


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
            "HYPEUSD": {"tier": 1, "source": "kraken"},
            "hl:LIT": {"tier": 1, "source": "hyperliquid"},
        },
        "macro_refs": {
            "SPYUSD": {"source": "yfinance", "yfinance_ticker": "SPY", "tracking_only": True},
            "XLExUSD": {"source": "yfinance", "yfinance_ticker": "XLE", "tracking_only": True},
        },
    }
}


def test_load_raw_missing(tmp_watchlist_path):
    assert wl_mod.load_raw() == {}


def test_save_load_round_trip(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.load_raw() == SAMPLE


def test_all_tickers(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    out = wl_mod.all_tickers()
    assert "BTCUSD" in out
    assert "hl:LIT" in out
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
    assert wl_mod.provider_for("hl:LIT") == "hyperliquid"


def test_provider_for_via_metadata(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.provider_for("BTCUSD") == "kraken"
    assert wl_mod.provider_for("SPYUSD") == "yfinance"


def test_resolve(tmp_watchlist_path):
    wl_mod.save_raw(SAMPLE)
    assert wl_mod.resolve("btc") == "BTCUSD"
    assert wl_mod.resolve("eth") == "ETHUSD"
    assert wl_mod.resolve("lit") == "hl:LIT"
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
    out = wl_mod.expand_tickers(["btc", "ETHUSD", "hl:LIT", "NOPE"])
    # NOPE doesn't resolve → passes through unchanged
    assert "BTCUSD" in out
    assert "ETHUSD" in out
    assert "hl:LIT" in out
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
        "HYPEUSD",
        "hl:LIT",
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
