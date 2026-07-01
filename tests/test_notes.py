"""Tests for analysis/notes — I/O + library functions."""

import pytest

from analysis import notes as notes_mod


@pytest.fixture
def tmp_notes_path(tmp_path, monkeypatch):
    """Redirect the notes file to a tmp path."""
    path = tmp_path / "notes.json"
    monkeypatch.setattr(notes_mod, "_resolve_path", lambda p=None: path if p is None else p)
    return path


def test_load_raw_missing_file(tmp_notes_path):
    assert notes_mod.load_raw() == {}


def test_save_and_load_raw(tmp_notes_path):
    data = {"BTCUSD": [{"note": "hi", "added": "2026-01-01", "expires": None}]}
    notes_mod.save_raw(data)
    assert tmp_notes_path.exists()
    assert notes_mod.load_raw() == data


def test_add_note_returns_entry(tmp_notes_path):
    e = notes_mod.add_note("BTCUSD", "test thesis", expires="7d")
    assert e["note"] == "test thesis"
    assert e["expires"] is not None


def test_add_note_persists(tmp_notes_path):
    notes_mod.add_note("BTCUSD", "first")
    notes_mod.add_note("BTCUSD", "second")
    data = notes_mod.load_raw()
    assert len(data["BTCUSD"]) == 2


def test_add_note_multiple_pairs(tmp_notes_path):
    notes_mod.add_note("BTCUSD", "btc note")
    notes_mod.add_note("ETHUSD", "eth note")
    data = notes_mod.load_raw()
    assert "BTCUSD" in data
    assert "ETHUSD" in data


def test_add_note_with_meta(tmp_notes_path):
    """Legacy --meta is auto-translated to typed fields; the new entry has no 'meta' key."""
    e = notes_mod.add_note("BTCUSD", "thesis", meta={"tags": ["thesis", "wait"], "fng": 12})
    assert "meta" not in e
    assert e["type"] == "thesis"
    assert e["status"] == "thesis" or e["status"] is None  # 'wait' has no canonical axis
    # unknown keys (fng) drop to tags? — no, only leftover TAGS drop. fng is a meta-only key, lost.
    assert e["invalidates_on"] is None


def test_add_note_with_typed_fields(tmp_notes_path):
    e = notes_mod.add_note(
        "BTCUSD",
        "thesis text",
        expires="30d",
        status="thesis",
        type_="thesis",
        state="coiled_range_intact",
        active_timeframe="1d",
        dependencies=["ETHUSD"],
        price_refs={"stop": 100, "target": 200},
        invalidates_on="close_below_90",
        tags=["wait"],
    )
    assert e["status"] == "thesis"
    assert e["type"] == "thesis"
    assert e["state"] == "coiled_range_intact"
    assert e["active_timeframe"] == "1d"
    assert e["dependencies"] == ["ETHUSD"]
    assert e["price_refs"] == {"stop": 100.0, "target": 200.0}
    assert e["invalidates_on"] == "close_below_90"
    assert e["tags"] == ["wait"]


def test_add_note_rejects_invalid_status(tmp_notes_path):
    import pytest

    with pytest.raises(ValueError):
        notes_mod.add_note("BTCUSD", "thesis", status="bogus")


def test_add_note_rejects_invalid_price_refs(tmp_notes_path):
    import pytest

    with pytest.raises(ValueError):
        notes_mod.add_note("BTCUSD", "thesis", price_refs={"bogus": 1.0})
    with pytest.raises(ValueError):
        notes_mod.add_note("BTCUSD", "thesis", price_refs={"stop": "not-a-number"})


def test_migrate_legacy_meta_translates_typed_fields(tmp_notes_path):
    """migrate_entry converts a legacy entry in-place."""
    legacy = {
        "note": "old",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {
            "tags": ["open", "setup"],
            "stop": 2.54,
            "target": 3.12,
            "invalidates_on": "close_below_2.0",
        },
    }
    migrated = notes_mod.migrate_entry(legacy)
    assert migrated["status"] == "open"
    assert migrated["type"] == "setup"
    assert migrated["price_refs"] == {"stop": 2.54, "target": 3.12}
    assert migrated["invalidates_on"] == "close_below_2.0"
    assert "meta" not in migrated


def test_migrate_promotes_timeframe_tag(tmp_notes_path):
    """1d_only / 4h_only tags become active_timeframe."""
    legacy = {
        "note": "x",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {"tags": ["1d_only"]},
    }
    migrated = notes_mod.migrate_entry(legacy)
    assert migrated["active_timeframe"] == "1d"


def test_migrate_promotes_tf_suffix_tag_and_keeps_it(tmp_notes_path):
    """Tags like '4h_pullback' promote to active_timeframe AND stay in tags.

    Regression: previously the leftover filter dropped any tag starting with
    '4h_' but the detector only matched exact tf / tf_only, so the tag was
    lost and active_timeframe stayed None.
    """
    legacy = {
        "note": "x",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {"tags": ["4h_pullback"]},
    }
    migrated = notes_mod.migrate_entry(legacy)
    assert migrated["active_timeframe"] == "4h"
    assert migrated.get("tags") == ["4h_pullback"]


def test_validate_flags_legacy_meta(tmp_notes_path):
    """validate_entry emits a legacy-meta error until migration runs."""
    from analysis.notes_format import validate_entry

    legacy = {
        "note": "old",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {"tags": ["thesis"]},
    }
    errs = validate_entry(legacy)
    assert any("legacy 'meta'" in e for e in errs)


def test_load_active_filters_expired(tmp_notes_path):
    past_iso = "2020-01-01T00:00:00+00:00"
    future_iso = "2099-01-01T00:00:00+00:00"
    data = {
        "BTCUSD": [
            {"note": "old", "added": "2020-01-01", "expires": past_iso},
            {"note": "new", "added": "2026-01-01", "expires": future_iso},
            {"note": "perm", "added": "2026-01-01"},
        ]
    }
    notes_mod.save_raw(data)
    active = notes_mod.load_active("BTCUSD")
    assert len(active) == 2
    assert active[0]["note"] == "new"
    assert active[1]["note"] == "perm"


def test_load_active_unknown_pair(tmp_notes_path):
    assert notes_mod.load_active("NOPAIR") == []


def test_remove_note_by_index(tmp_notes_path):
    notes_mod.add_note("BTCUSD", "first")
    notes_mod.add_note("BTCUSD", "second")
    removed = notes_mod.remove_note("BTCUSD", 0)
    assert removed["note"] == "first"
    remaining = notes_mod.load_active("BTCUSD")
    assert len(remaining) == 1
    assert remaining[0]["note"] == "second"


def test_remove_note_out_of_range(tmp_notes_path):
    notes_mod.add_note("BTCUSD", "only")
    with pytest.raises(IndexError):
        notes_mod.remove_note("BTCUSD", 5)


def test_remove_last_note_cleans_up_pair_key(tmp_notes_path):
    notes_mod.add_note("BTCUSD", "only")
    notes_mod.remove_note("BTCUSD", 0)
    data = notes_mod.load_raw()
    assert "BTCUSD" not in data


def test_prune_expired_removes_only_expired(tmp_notes_path):
    past_iso = "2020-01-01T00:00:00+00:00"
    future_iso = "2099-01-01T00:00:00+00:00"
    data = {
        "BTCUSD": [
            {"note": "old", "added": "2020-01-01", "expires": past_iso},
            {"note": "new", "added": "2026-01-01", "expires": future_iso},
        ]
    }
    notes_mod.save_raw(data)
    removed = notes_mod.prune_expired()
    assert removed == 1
    data = notes_mod.load_raw()
    assert len(data["BTCUSD"]) == 1
    assert data["BTCUSD"][0]["note"] == "new"


def test_prune_expired_cleans_empty_pair(tmp_notes_path):
    past_iso = "2020-01-01T00:00:00+00:00"
    notes_mod.save_raw({"BTCUSD": [{"note": "old", "added": "2020-01-01", "expires": past_iso}]})
    notes_mod.prune_expired()
    assert "BTCUSD" not in notes_mod.load_raw()


def test_load_all_pairs_sorted(tmp_notes_path):
    notes_mod.add_note("ETHUSD", "eth")
    notes_mod.add_note("BTCUSD", "btc")
    notes_mod.add_note("ADAUSD", "ada")
    assert notes_mod.load_all_pairs() == ["ADAUSD", "BTCUSD", "ETHUSD"]


def test_atomic_write_no_partial_files(tmp_notes_path):
    """Verify write uses tmp+rename (no .tmp leftover on success)."""
    notes_mod.add_note("BTCUSD", "test")
    assert not tmp_notes_path.with_suffix(tmp_notes_path.suffix + ".tmp").exists()


def test_env_var_override(monkeypatch, tmp_path):
    """MARKET_SKILLS_NOTES_PATH env var should override default path."""
    custom = tmp_path / "custom.json"
    monkeypatch.setenv("MARKET_SKILLS_NOTES_PATH", str(custom))
    notes_mod.add_note("BTCUSD", "from env")
    assert custom.exists()
    actual = notes_mod.load_raw()
    assert actual["BTCUSD"][0]["note"] == "from env"
    assert actual["BTCUSD"][0]["expires"] is None
    # No legacy `meta` field — new entries use typed fields.
    assert "meta" not in actual["BTCUSD"][0]
    # All typed fields are present (None for unset).
    for k in ("status", "type", "state", "active_timeframe", "dependencies", "price_refs", "invalidates_on", "tags"):
        assert k in actual["BTCUSD"][0]


def test_default_path_points_to_skill_data_dir():
    """Default path must point inside skills/market-notes/data/."""
    p = notes_mod.default_path()
    assert p.name == "notes.json"
    assert "skills" in p.parts
    assert "market-notes" in p.parts
    assert "data" in p.parts
