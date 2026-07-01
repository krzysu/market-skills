"""Tests for analysis/watchlist_format — pure helpers, no I/O."""

import pytest

from analysis.watchlist_format import (
    _bare_aliases,
    all_tickers,
    basket,
    by_category,
    categories,
    get_baskets,
    metadata_for,
    provider_for,
    resolve,
    validate_storage,
)

SAMPLE = {
    "baskets": {
        "crypto_majors": {
            "BTCUSD": {"tier": 2, "source": "kraken", "label": "BTC"},
            "ETHUSD": {"tier": 2, "source": "kraken", "label": "ETH"},
        },
        "crypto_alts": {
            "HYPEUSD": {"tier": 1, "source": "kraken", "label": "Hyperliquid"},
            "hl:LIT": {"tier": 1, "source": "hyperliquid", "label": "Lighter"},
        },
        "macro_refs": {
            "SPYUSD": {"source": "yfinance", "yfinance_ticker": "SPY", "tracking_only": True, "sector": "stocks"},
            "IWMUSD": {
                "source": "yfinance",
                "yfinance_ticker": "IWM",
                "tracking_only": True,
                "hl_proxy": "km:SMALL2000",
            },
        },
    }
}


def test_get_baskets_empty():
    assert get_baskets({}) == {}


def test_get_baskets_not_dict():
    assert get_baskets("nope") == {}


def test_categories():
    assert categories(SAMPLE) == ["crypto_majors", "crypto_alts", "macro_refs"]


def test_by_category():
    assert by_category(SAMPLE, "crypto_majors") == ["BTCUSD", "ETHUSD"]


def test_by_category_missing():
    assert by_category(SAMPLE, "nope") == []


def test_basket_full():
    b = basket(SAMPLE, "crypto_majors")
    assert "BTCUSD" in b
    assert b["BTCUSD"]["source"] == "kraken"


def test_basket_missing():
    assert basket(SAMPLE, "nope") == {}


def test_all_tickers_dedup():
    out = all_tickers(SAMPLE)
    assert "BTCUSD" in out
    assert "ETHUSD" in out
    assert "HYPEUSD" in out
    assert "hl:LIT" in out
    assert "SPYUSD" in out
    assert "IWMUSD" in out
    assert len(out) == len(set(out))


def test_metadata_for_known():
    assert metadata_for(SAMPLE, "BTCUSD")["source"] == "kraken"
    assert metadata_for(SAMPLE, "hl:LIT")["source"] == "hyperliquid"


def test_metadata_for_unknown():
    assert metadata_for(SAMPLE, "NOPE") == {}


def test_provider_for_explicit_prefix():
    assert provider_for(SAMPLE, "hl:LIT") == "hyperliquid"


def test_provider_for_via_source():
    assert provider_for(SAMPLE, "BTCUSD") == "kraken"
    assert provider_for(SAMPLE, "SPYUSD") == "yfinance"


def test_provider_for_unknown():
    assert provider_for(SAMPLE, "NOPE") is None


def test_bare_aliases_btcusd():
    assert "btc" in _bare_aliases("BTCUSD")


def test_bare_aliases_xlexusd():
    aliases = _bare_aliases("XLExUSD")
    assert "xle" in aliases
    assert "xlexusd" in aliases


def test_bare_aliases_hl_prefix():
    aliases = _bare_aliases("hl:LIT")
    assert "hl:lit" in aliases
    assert "lit" in aliases


def test_resolve_btc():
    assert resolve(SAMPLE, "btc") == "BTCUSD"


def test_resolve_eth():
    assert resolve(SAMPLE, "eth") == "ETHUSD"


def test_resolve_xle_unknown_in_sample():
    # XLE not in sample — returns None (no match, no ambiguity)
    assert resolve(SAMPLE, "xle") is None


def test_resolve_lit_matches_hl_lit():
    assert resolve(SAMPLE, "lit") == "hl:LIT"


def test_resolve_unknown():
    assert resolve(SAMPLE, "xyz") is None


def test_resolve_ambiguous_raises():
    """If two tickers share an alias (rare), raise rather than pick."""
    sample = {
        "baskets": {
            "a": {"FOOUSD": {}},
            "b": {"FOOxUSD": {}},
        }
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve(sample, "foo")


def test_validate_storage_ok():
    assert validate_storage(SAMPLE) == []


def test_validate_storage_missing_baskets():
    errs = validate_storage({})
    assert any("baskets" in e for e in errs)


def test_validate_storage_baskets_not_dict():
    errs = validate_storage({"baskets": []})
    assert errs


def test_validate_storage_invalid_source():
    bad = {"baskets": {"x": {"BTCUSD": {"source": "binance"}}}}
    errs = validate_storage(bad)
    assert any("invalid source" in e for e in errs)


def test_validate_storage_tier_must_be_int():
    bad = {"baskets": {"x": {"BTCUSD": {"tier": "high"}}}}
    errs = validate_storage(bad)
    assert any("tier" in e for e in errs)


def test_validate_storage_tracking_only_must_be_bool():
    bad = {"baskets": {"x": {"BTCUSD": {"tracking_only": "yes"}}}}
    errs = validate_storage(bad)
    assert any("tracking_only" in e for e in errs)


def test_validate_storage_root_not_dict():
    assert validate_storage([]) != []


def test_validate_storage_basket_not_dict():
    bad = {"baskets": {"x": "not-a-dict"}}
    errs = validate_storage(bad)
    assert any("must be a dict" in e for e in errs)


def test_validate_storage_metadata_not_dict():
    bad = {"baskets": {"x": {"BTCUSD": "not-a-dict"}}}
    errs = validate_storage(bad)
    assert any("metadata must be a dict" in e for e in errs)
