"""Tests for run-watchlist skip-list filtering — lib.filter_skip_list + _load_skip_tickers."""

import importlib.util
import json
import os
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB_PATH = os.path.join(_REPO_ROOT, "skills", "run-watchlist", "lib.py")
_spec = importlib.util.spec_from_file_location("run_watchlist_lib_skip", _LIB_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
filter_skip_list = _mod.filter_skip_list
_load_skip_tickers = _mod._load_skip_tickers


def _meta_map(table: dict) -> dict:
    """Build a stub metadata_for(ticker, path) callable from a {ticker: meta} table."""

    def _metadata_for(ticker: str, _path) -> dict:
        return table.get(ticker, {})

    return _metadata_for


def test_skip_list_excludes_tickers():
    """A ticker in skip_tickers is excluded when it has no watchlist metadata."""
    tickers = ["hl:ASTER", "BTCUSD", "ETHUSD"]
    skip = ["hl:ASTER"]
    metadata_for = _meta_map({})  # no metadata for any ticker

    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=metadata_for)

    assert kept == ["BTCUSD", "ETHUSD"]
    assert "hl:ASTER" in skipped
    assert "negative Sharpe" in skipped["hl:ASTER"]


def test_skip_list_watchlist_wins():
    """A tier_1/tier_2 ticker in skip_tickers is NOT excluded."""
    tickers = ["hl:ASTER", "BTCUSD"]
    skip = ["hl:ASTER", "BTCUSD"]
    metadata_for = _meta_map(
        {
            "hl:ASTER": {"tier": 1},
            "BTCUSD": {"tier": 2},
        }
    )

    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=metadata_for)

    assert kept == ["hl:ASTER", "BTCUSD"]
    assert skipped == {}


def test_skip_list_tier_3_is_skipped():
    """Only tier 1 and tier 2 win; tier 3 is still skippable."""
    tickers = ["AAA", "BBB", "CCC"]
    skip = ["AAA", "BBB", "CCC"]
    metadata_for = _meta_map(
        {
            "AAA": {"tier": 3},
            "BBB": {"tier": None},
            # CCC has no metadata at all
        }
    )

    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=metadata_for)

    assert kept == []
    assert set(skipped) == {"AAA", "BBB", "CCC"}


def test_skip_list_empty_keeps_all():
    """Empty skip_tickers list — all tickers pass through."""
    tickers = ["BTCUSD", "ETHUSD"]
    kept, skipped = filter_skip_list(tickers, [], None, metadata_for=_meta_map({}))
    assert kept == ["BTCUSD", "ETHUSD"]
    assert skipped == {}


def test_skip_list_no_overlap_keeps_all():
    """skip_tickers that don't intersect tickers — all pass through."""
    tickers = ["BTCUSD", "ETHUSD"]
    skip = ["SOLUSD", "HYPEUSD"]
    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=_meta_map({}))
    assert kept == ["BTCUSD", "ETHUSD"]
    assert skipped == {}


def test_skip_list_mixed():
    """Some skipped, some kept, watchlist override works."""
    tickers = ["hl:ASTER", "BTCUSD", "ETHUSD", "SOLUSD", "VVVUSD"]
    skip = ["hl:ASTER", "BTCUSD", "SOLUSD"]
    metadata_for = _meta_map(
        {
            "BTCUSD": {"tier": 1},  # watchlist wins
            "SOLUSD": {"tier": 3},  # still skipped
            # hl:ASTER + VVVUSD have no metadata
        }
    )

    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=metadata_for)

    assert kept == ["BTCUSD", "ETHUSD", "VVVUSD"]
    assert set(skipped) == {"hl:ASTER", "SOLUSD"}
    for reason in skipped.values():
        assert "negative Sharpe" in reason


def test_load_skip_tickers_missing_file():
    """FileNotFoundError -> empty tuple (all tickers pass through)."""
    assert _load_skip_tickers("/nonexistent/path/does-not-exist.json") == ([], None)


def test_load_skip_tickers_none_path():
    """None path -> empty tuple (no filtering requested)."""
    assert _load_skip_tickers(None) == ([], None)


def test_load_skip_tickers_invalid_json():
    """Invalid JSON -> empty tuple."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{not valid json")
        path = f.name
    try:
        assert _load_skip_tickers(path) == ([], None)
    finally:
        os.unlink(path)


def test_load_skip_tickers_missing_key():
    """Valid JSON but no skip_tickers key -> empty tuple."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"other": [1, 2, 3]}, f)
        path = f.name
    try:
        assert _load_skip_tickers(path) == ([], None)
    finally:
        os.unlink(path)


def test_load_skip_tickers_valid():
    """Valid skip list JSON returns the skip_tickers list and the reason."""
    payload = {
        "skip_tickers": ["hl:ASTER", "SOLUSD"],
        "keep_tickers": ["BTCUSD"],
        "reason": "all strategies have negative Sharpe on these tickers",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        path = f.name
    try:
        assert _load_skip_tickers(path) == (
            ["hl:ASTER", "SOLUSD"],
            "all strategies have negative Sharpe on these tickers",
        )
    finally:
        os.unlink(path)


def test_load_skip_tickers_non_list_skipped():
    """skip_tickers that isn't a list -> empty tuple (graceful)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"skip_tickers": "hl:ASTER"}, f)
        path = f.name
    try:
        assert _load_skip_tickers(path) == ([], None)
    finally:
        os.unlink(path)


def test_load_skip_tickers_missing_reason_defaults_none():
    """Skip list without a 'reason' field returns (tickers, None)."""
    payload = {"skip_tickers": ["hl:ASTER"]}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        path = f.name
    try:
        assert _load_skip_tickers(path) == (["hl:ASTER"], None)
    finally:
        os.unlink(path)


def test_load_skip_tickers_blank_reason_defaults_none():
    """Empty/whitespace reason falls back to None so default message is used."""
    payload = {"skip_tickers": ["hl:ASTER"], "reason": "   "}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        path = f.name
    try:
        assert _load_skip_tickers(path) == (["hl:ASTER"], None)
    finally:
        os.unlink(path)


def test_filter_skip_list_preserves_order():
    """Kept tickers preserve original ordering."""
    tickers = ["C", "A", "B", "D"]
    skip = ["A"]
    kept, _skipped = filter_skip_list(tickers, skip, None, metadata_for=_meta_map({}))
    assert kept == ["C", "B", "D"]


def test_filter_skip_list_all_skipped():
    """When every ticker is in skip_tickers and none are tier 1/2, kept=[] and
    skipped contains all of them (FINDING 1: edge case after the empty-check
    in run.py would otherwise continue with 0 tickers).
    """
    tickers = ["hl:ASTER", "SOLUSD", "HYPEUSD"]
    skip = ["hl:ASTER", "SOLUSD", "HYPEUSD"]
    metadata_for = _meta_map(
        {
            "hl:ASTER": {"tier": 3},
            "SOLUSD": {"tier": None},
            # HYPEUSD has no metadata
        }
    )

    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=metadata_for)

    assert kept == []
    assert set(skipped) == {"hl:ASTER", "SOLUSD", "HYPEUSD"}
    for reason in skipped.values():
        assert "negative Sharpe" in reason


def test_filter_skip_list_uses_provided_reason():
    """A non-None reason overrides the default _SKIP_REASON message."""
    tickers = ["hl:ASTER", "BTCUSD"]
    skip = ["hl:ASTER"]
    metadata_for = _meta_map({})
    custom = "excluded: manually blacklisted for this run"

    kept, skipped = filter_skip_list(tickers, skip, None, metadata_for=metadata_for, reason=custom)

    assert kept == ["BTCUSD"]
    assert skipped == {"hl:ASTER": custom}


def test_filter_skip_list_empty_reason_falls_back_to_default():
    """An empty/None reason falls back to the default _SKIP_REASON message."""
    tickers = ["hl:ASTER"]
    skip = ["hl:ASTER"]
    metadata_for = _meta_map({})

    _kept, skipped_none = filter_skip_list(tickers, skip, None, metadata_for=metadata_for, reason=None)
    _kept, skipped_empty = filter_skip_list(tickers, skip, None, metadata_for=metadata_for, reason="")

    assert "negative Sharpe" in skipped_none["hl:ASTER"]
    assert "negative Sharpe" in skipped_empty["hl:ASTER"]
