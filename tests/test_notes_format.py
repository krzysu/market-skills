"""Tests for analysis/notes_format — pure helpers, no I/O."""

import datetime as dt

from analysis.notes_format import (
    filter_active,
    format_note,
    is_active,
    make_entry,
    now_utc,
    parse_expires,
    validate_entry,
    validate_storage,
)


def test_parse_expires_shorthand():
    n = now_utc()
    out = parse_expires("14d")
    parsed = dt.datetime.fromisoformat(out)
    assert parsed.tzinfo is not None
    delta = parsed - n
    assert dt.timedelta(days=13, hours=23) < delta < dt.timedelta(days=14, hours=1)


def test_parse_expires_units():
    n = now_utc()
    assert (dt.datetime.fromisoformat(parse_expires("6h")) - n).total_seconds() > 6 * 3600 - 5
    assert (dt.datetime.fromisoformat(parse_expires("2w")) - n).total_seconds() > 14 * 86400 - 5
    assert (dt.datetime.fromisoformat(parse_expires("1m")) - n).total_seconds() > 29 * 86400 - 5


def test_parse_expires_iso():
    out = parse_expires("2030-01-01")
    assert out.startswith("2030-01-01")


def test_parse_expires_none():
    assert parse_expires(None) is None


def test_parse_expires_invalid_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_expires("forever")


def test_is_active_no_expiry():
    assert is_active({"note": "hi"})


def test_is_active_future():
    future = (now_utc() + dt.timedelta(days=7)).isoformat()
    assert is_active({"note": "hi", "expires": future})


def test_is_active_past():
    past = (now_utc() - dt.timedelta(days=1)).isoformat()
    assert not is_active({"note": "hi", "expires": past})


def test_is_active_garbage_expiry_treated_as_active():
    assert is_active({"note": "hi", "expires": "not-a-date"})


def test_filter_active():
    future = (now_utc() + dt.timedelta(days=7)).isoformat()
    past = (now_utc() - dt.timedelta(days=1)).isoformat()
    notes = [
        {"note": "a", "added": "2026-01-01", "expires": future},
        {"note": "b", "added": "2026-01-01", "expires": past},
        {"note": "c", "added": "2026-01-01"},
    ]
    out = filter_active(notes)
    assert len(out) == 2
    assert out[0]["note"] == "a"
    assert out[1]["note"] == "c"


def test_make_entry_shape():
    e = make_entry("hello", expires="7d", meta={"tags": ["x"]})
    assert e["note"] == "hello"
    assert e["expires"] is not None
    assert e["updated"] is None
    # meta gets translated via migrate_entry; output has typed fields
    assert "meta" not in e
    assert "tags" in e


def test_make_entry_typed_shape():
    e = make_entry(
        "hello",
        expires="7d",
        status="thesis",
        type_="thesis",
        state="coiled_range_intact",
        active_timeframe="1d",
        price_refs={"stop": 100, "target": 200},
        invalidates_on="close_below_90",
        tags=["wait"],
    )
    assert e["status"] == "thesis"
    assert e["type"] == "thesis"
    assert e["state"] == "coiled_range_intact"
    assert e["active_timeframe"] == "1d"
    assert e["price_refs"] == {"stop": 100.0, "target": 200.0}
    assert e["invalidates_on"] == "close_below_90"
    assert e["tags"] == ["wait"]


def test_make_entry_rejects_bad_status():
    import pytest

    with pytest.raises(ValueError):
        make_entry("x", status="bogus")


def test_migrate_translates_meta_tags_to_triple():
    from analysis.notes_format import migrate_entry

    e = {
        "note": "x",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {"tags": ["open", "setup", "wait"]},
    }
    out = migrate_entry(e)
    assert out["status"] == "open"
    assert out["type"] == "setup"
    assert "meta" not in out


def test_migrate_translates_meta_prices_to_price_refs():
    from analysis.notes_format import migrate_entry

    e = {
        "note": "x",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {"stop": 2.54, "target": 3.12, "notes": "ignore me"},
    }
    out = migrate_entry(e)
    assert out["price_refs"] == {"stop": 2.54, "target": 3.12}


def test_migrate_setup_invalidated_composite():
    from analysis.notes_format import migrate_entry

    e = {
        "note": "x",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "meta": {"tags": ["setup_invalidated"]},
    }
    out = migrate_entry(e)
    assert out["status"] == "invalidated"
    assert out["type"] == "setup"


def test_migrate_idempotent_on_already_typed():
    from analysis.notes_format import migrate_entry

    e = {
        "note": "x",
        "added": "2026-01-01",
        "expires": None,
        "updated": None,
        "status": "thesis",
        "type": "thesis",
    }
    out = migrate_entry(e)
    assert out == e


def test_make_entry_no_expiry():
    e = make_entry("hello", expires=None)
    assert e["expires"] is None


def test_format_note_human_readable():
    e = make_entry("test", expires="7d")
    s = format_note(e)
    assert "test" in s
    assert "expires in" in s


def test_format_note_expired():
    past = (now_utc() - dt.timedelta(days=2)).isoformat()
    e = {"note": "old", "added": "2026-01-01", "expires": past}
    s = format_note(e)
    assert "expired" in s


def test_format_note_no_expiry():
    e = {"note": "perm", "added": "2026-01-01"}
    s = format_note(e)
    assert "no expiry" in s


def test_validate_entry_ok():
    e = make_entry("ok", expires="7d")
    assert validate_entry(e) == []


def test_validate_entry_missing_note():
    errs = validate_entry({"added": "2026-01-01"})
    assert any("note" in e for e in errs)


def test_validate_entry_empty_note():
    errs = validate_entry({"note": "  ", "added": "2026-01-01"})
    assert any("note" in e for e in errs)


def test_validate_storage_ok():
    e = make_entry("x", expires="7d")
    data = {"BTCUSD": [e]}
    assert validate_storage(data) == []


def test_validate_storage_pair_not_list():
    data = {"BTCUSD": "not-a-list"}
    errs = validate_storage(data)
    assert any("must be a list" in e for e in errs)


def test_validate_storage_root_not_dict():
    errs = validate_storage([])
    assert errs  # non-empty


def test_validate_storage_nested_bad_note():
    data = {"BTCUSD": [{"added": "2026-01-01"}]}
    errs = validate_storage(data)
    assert any("note" in e for e in errs)
