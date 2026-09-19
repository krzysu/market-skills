"""Tests for portfolio.sync — derive the open-positions held file from the ledger.

The fixtures reproduce the three confirmed drift cases that motivated the
sync (card market-skills-8sw): VVV sold out but still monitored, LIT
position_size stale after three sells, ETH staking accrual never
reflected. All numbers are synthetic; the ledger is built in ``tmp_path``
with ``portfolio.db.init_db`` / ``add_portfolio`` / ``add_transaction`` —
the real DB is never read and no venue API is called anywhere in the
path (pinned by TestNoVenueApiCalls).
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from analysis.providers.execution._cli_common import sync_open_positions_after_fill
from portfolio.db import add_portfolio, add_transaction, get_db, init_db
from portfolio.sync import (
    ENV_OPEN_POSITIONS_PATH,
    FLAT_EPSILON,
    POSITION_SIZE_DECIMALS,
    STALE_ZONE_MIN_DISTANCE_PCT,
    plan_open_positions_sync,
    resolve_config_path,
    sync_open_positions,
    sync_open_positions_from_env,
)

NOW = datetime(2026, 9, 18, 13, 22, 5, tzinfo=UTC)
NOW_TS = "2026-09-18T13:22:05Z"

# Synthetic ledger rows reproducing the card's FIFO arithmetic.
_LEDGER_ROWS = [
    ("2026-07-01T10:00:00Z", "BUY", "hl:LIT", 1263.05, 1.583),
    ("2026-07-02T10:00:00Z", "BUY", "hl:LIT", 3049.16, 1.64),
    ("2026-07-03T10:00:00Z", "SELL", "hl:LIT", 3049.16, 1.8467),
    ("2026-07-04T10:00:00Z", "BUY", "hl:LIT", 2154.33124734, 2.32),
    ("2026-07-05T10:00:00Z", "SELL", "hl:LIT", 1925.41, 3.636),
    ("2026-07-06T10:00:00Z", "SELL", "hl:LIT", 692.12, 4.334458),
    ("2026-07-07T10:00:00Z", "SELL", "hl:LIT", 414.26, 4.828808),
    ("2026-08-01T10:00:00Z", "BUY", "hl:VVV", 317.860023518631, 15.7302),
    ("2026-08-02T10:00:00Z", "BUY", "hl:VVV", 431.17, 11.6),
    ("2026-08-03T10:00:00Z", "SELL", "hl:VVV", 569.0, 17.5484),
    ("2026-08-04T10:00:00Z", "BUY", "hl:VVV", 3.82975427, 0.0),
    ("2026-08-05T10:00:00Z", "SELL", "hl:VVV", 183.859777791626, 27.173793),
    ("2026-09-01T10:00:00Z", "BUY", "kraken:ETHEUR", 0.038, 1973.28),
    ("2026-09-02T10:00:00Z", "BUY", "kraken:ETHEUR", 0.0001598383, 0.0),
    ("2026-09-03T10:00:00Z", "BUY", "kraken:PENDLEEUR", 12.5, 4.2),
    ("2026-09-04T10:00:00Z", "BUY", "kraken:SOLEUR", 5.0, 180.0),
    ("2026-09-05T10:00:00Z", "BUY", "yf:QOMP.DE", 72.32423, 10.0),
]


def _build_ledger(db_path: str) -> int:
    init_db(db_path)
    pid = add_portfolio(db_path, "main", base_ccy="EUR")
    for ts, side, asset, qty, price in _LEDGER_ROWS:
        add_transaction(db_path, pid, ts, side, asset, qty=qty, price=price)
    return pid


def _seed_price_cache(db_path: str, asset: str, price: float) -> None:
    conn = get_db(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO price_cache (asset, price, ts, source) VALUES (?, ?, ?, ?)",
        (asset, price, "2026-09-18T13:00:00Z", "test-cache"),
    )
    conn.commit()
    conn.close()


def _held_doc() -> dict:
    """Hand-authored held file covering every drift + preservation shape."""
    return {
        "_comment": (
            "Held positions monitored by position-watchdog — hand-authored levels are "
            "preserved byte-identical by portfolio-mgmt sync-open-positions; only "
            "membership, position_size and a missing entry_price are ledger-derived"
        ),
        "watches": [
            {
                "name": "VVV",
                "enabled": True,
                "monitor_provider": "hl:VVV",
                "format_style": "default",
                "interval": "4h",
                "period": "6mo",
                "entry_price": 15.7302,
                "position_size": 180.03,
                "_comment_zone": "T1 add zone re-grounded 2026-09 after the runner",
                "levels": [{"type": "zone", "low": 7.5, "high": 9.0, "label": "T1 add zone", "emoji": "🟢"}],
                "signals": [{"strategies": ["trend-follow"], "min_conviction": 4, "cooldown_hours": 2}],
            },
            {
                "name": "LIT",
                "enabled": True,
                "monitor_provider": "hl:LIT",
                "format_style": "default",
                "interval": "4h",
                "period": "6mo",
                "entry_price": 2.32,
                "position_size": 3417,
                "levels": [
                    {"type": "stop", "price": 1.9},
                    {"type": "tp", "price": 3.5, "exit_pct": 33},
                    {"type": "tp", "price": 4.2, "exit_pct": 33},
                    {"type": "tp", "price": 5.1, "exit_pct": 34},
                    {"type": "invalidation", "below": 1.6},
                ],
                "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}],
            },
            {
                "name": "ETH",
                "enabled": True,
                "monitor_provider": "kraken:ETHEUR",
                "interval": "1d",
                "period": "1y",
                "entry_price": 1973.28,
                "position_size": 0.038,
                "_comment_staking": "staking accrual lands in the ledger",
                "levels": [
                    {"type": "zone", "low": 1500, "high": 1700, "label": "re-accumulate", "emoji": "🟡"},
                    {"type": "invalidation", "below": 1400},
                ],
            },
            {
                "name": "PENDLE",
                "enabled": True,
                "monitor_provider": "kraken:PENDLEEUR",
                "interval": "4h",
                "period": "3mo",
                "position_size": 10.0,
                "_comment_ledger_sync": "position_size 8.0 -> 10.0 (ledger-derived 2026-09-01T00:00:00Z)",
                "levels": [
                    {"type": "stop", "price": 3.0},
                ],
            },
            {
                "name": "SOL",
                "enabled": True,
                "monitor_provider": "kraken:SOLEUR",
                "interval": "4h",
                "period": "6mo",
                "entry_price": 180.0,
                "position_size": 5.0,
                "levels": [
                    {"type": "zone", "low": 100, "high": 120, "label": "add zone", "emoji": "🟢"},
                ],
            },
            {
                "name": "ZEC",
                "enabled": False,
                "monitor_provider": "kraken:ZECUSD",
                "interval": "4h",
                "period": "6mo",
                "position_size": 24.5,
                "levels": [
                    {"type": "zone", "low": 400, "high": 420, "label": "T2 limit zone", "emoji": "🟠"},
                ],
            },
            {
                "name": "NOPE",
                "enabled": True,
                "monitor_provider": "hl:NOPE",
                "interval": "4h",
                "period": "6mo",
                "position_size": 1.0,
                "levels": [
                    {"type": "stop", "price": 0.5},
                ],
            },
        ],
    }


_RUN_PATH = os.path.join(os.path.dirname(__file__), "..", "skills", "portfolio-mgmt", "scripts", "run.py")


def _load_run_module():
    spec = importlib.util.spec_from_file_location("portfolio_mgmt_run_under_test", _RUN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(*argv, monkeypatch):
    mod = _load_run_module()
    with patch.object(sys, "argv", ["run.py", *argv]):
        return mod.main()


class _SyncFixture:
    def __init__(self, tmp_path):
        self.db_path = str(tmp_path / "portfolio.db")
        self.pid = _build_ledger(self.db_path)
        self.held_path = str(tmp_path / "open-positions.json")
        with open(self.held_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(_held_doc(), indent=2, ensure_ascii=False) + "\n")

    def write(self, text: str) -> None:
        with open(self.held_path, "w", encoding="utf-8") as f:
            f.write(text)

    def original_text(self) -> str:
        with open(self.held_path, encoding="utf-8") as f:
            return f.read()

    def new_text(self) -> str:
        with open(self.held_path, encoding="utf-8") as f:
            return f.read()


@pytest.fixture
def sync_fixture(tmp_path):
    return _SyncFixture(tmp_path)


def _raw_element_blocks(text: str) -> list[str]:
    """Raw byte blocks of each top-level ``watches`` element.

    Elements are located with ``raw_decode`` — independent of any
    ``json.dumps`` call, so an indent/format regression in the writer
    cannot fake byte-identity by regenerating the same shape.
    """
    dec = json.JSONDecoder()
    i = text.index("[", text.index('"watches"')) + 1
    blocks: list[str] = []
    while True:
        while text[i].isspace():
            i += 1
        if text[i] == "]":
            break
        _value, end = dec.raw_decode(text, i)
        blocks.append(text[i:end])
        i = end
        while text[i].isspace():
            i += 1
        if text[i] == ",":
            i += 1
        elif text[i] == "]":
            break
    return blocks


# The repo's documented inline style (cf.
# skills/position-watchdog/examples/watches.example.json): pretty top
# level, but levels/signals items each on one line. LIT's stale
# position_size forces a correction; SOL must survive untouched.
_INLINE_HELD = """{
  "watches": [
    {
      "name": "LIT",
      "enabled": true,
      "monitor_provider": "hl:LIT",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 2.32,
      "position_size": 3417,
      "levels": [
        {"type": "stop", "price": 1.9},
        {"type": "tp", "price": 3.5, "exit_pct": 33},
        {"type": "tp", "price": 4.2, "exit_pct": 33},
        {"type": "invalidation", "below": 1.6}
      ]
    },
    {
      "name": "SOL",
      "enabled": true,
      "monitor_provider": "kraken:SOLEUR",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 180.0,
      "position_size": 5.0,
      "levels": [{"type": "zone", "low": 100, "high": 120, "label": "add zone", "emoji": "🟢"}],
      "signals": [
        {"strategies": ["trend-follow"], "min_conviction": 4, "cooldown_hours": 2}
      ]
    }
  ]
}
"""


# The repo's documented inline style (cf.
# skills/position-watchdog/examples/watches.example.json) with a drifted
# LIT position_size AND hand-authored numeric literals (500.00, 4.20,
# 1.60) plus an inline signals array — a whole-element re-serialization
# would normalize them, value-level splicing must not. VVV is flat
# (removed), SOL is untouched.
_BYTEIDENT_HELD = """{
  "watches": [
    {
      "name": "VVV",
      "enabled": true,
      "monitor_provider": "hl:VVV",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 15.7302,
      "position_size": 180.03,
      "levels": [
        {"type": "zone", "low": 7.5, "high": 9.0, "label": "T1 add zone", "emoji": "🟢"}
      ],
      "signals": [
        {"strategies": ["trend-follow"], "min_conviction": 4, "cooldown_hours": 2}
      ]
    },
    {
      "name": "LIT",
      "enabled": true,
      "monitor_provider": "hl:LIT",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 2.32,
      "position_size": 3417,
      "levels": [
        {"type": "stop", "price": 1.9},
        {"type": "tp", "price": 500.00, "exit_pct": 33},
        {"type": "tp", "price": 4.20, "exit_pct": 33},
        {"type": "invalidation", "below": 1.60}
      ],
      "signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}]
    },
    {
      "name": "SOL",
      "enabled": true,
      "monitor_provider": "kraken:SOLEUR",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 180.0,
      "position_size": 5.0,
      "levels": [{"type": "zone", "low": 100, "high": 500.00, "label": "add zone", "emoji": "🟢"}],
      "signals": [
        {"strategies": ["trend-follow"], "min_conviction": 4, "cooldown_hours": 2}
      ]
    }
  ]
}
"""


# ───────────────────────────────────────────────── dry run — the three cases


# The repo's documented inline style with a surviving entry that has NO
# ``position_size`` member (the parked-ZEC shape in
# skills/position-watchdog/examples/watches.example.json): inline
# levels/signals hand-authored on one line. The ledger holds the
# position, so the sync must APPEND ``position_size`` — never fall back
# to a whole-element re-serialization that would reformat them.
_ZEC_INLINE_LEVELS = '"levels": [{"type": "zone", "low": 400, "high": 420, "label": "T2 limit zone", "emoji": "🟠"}],'
_ZEC_INLINE_SIGNALS = (
    '"signals": [{"strategies": ["mean-reversion", "breakout-confirm"], "min_conviction": 4, "cooldown_hours": 4}]'
)
_ZEC_NO_SIZE_HELD = """{
  "watches": [
    {
      "name": "ZEC",
      "enabled": true,
      "monitor_provider": "kraken:ZECUSD",
      "interval": "4h",
      "period": "6mo",
      "levels": [{"type": "zone", "low": 400, "high": 420, "label": "T2 limit zone", "emoji": "🟠"}],
      "signals": [{"strategies": ["mean-reversion", "breakout-confirm"], "min_conviction": 4, "cooldown_hours": 4}]
    }
  ]
}
"""


class TestMissingPositionSizeAppended:
    """A surviving entry with NO ``position_size`` member (the shape in
    skills/position-watchdog/examples/watches.example.json:24-39) gets
    the member APPENDED to its raw bytes — never a whole-element
    re-serialization that would reformat its inline ``levels``/``signals``
    (acceptance criterion 2)."""

    def _ledger(self, tmp_path, asset, qty, price):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", asset, qty=qty, price=price)
        return db

    def test_appended_after_monitor_provider_keeps_inline_levels_signals_bytes(self, tmp_path):
        db = self._ledger(tmp_path, "kraken:ZECUSD", 24.5, 20.0)
        held = tmp_path / "held.json"
        held.write_text(_ZEC_NO_SIZE_HELD, encoding="utf-8")
        zec_before = _blocks_by_name(_ZEC_NO_SIZE_HELD)["ZEC"]
        audit = (
            f"position_size null -> 24.5 (ledger-derived {NOW_TS})"
            f" | entry_price filled 20.0 (ledger avg cost, ledger-derived {NOW_TS})"
        )

        report = sync_open_positions(db, str(held), now=NOW)

        assert report["changed"] is True
        assert report["written"] is True
        assert not any("re-serialized" in n for n in report["notes"])
        zec_after = _blocks_by_name(held.read_text(encoding="utf-8"))["ZEC"]
        # Only the appended members differ; anchor order: position_size
        # after monitor_provider, then the audit note adjacent to it, and
        # the freshly filled entry_price after the size member.
        expected = zec_before.replace(
            '"monitor_provider": "kraken:ZECUSD",',
            '"monitor_provider": "kraken:ZECUSD",\n      "position_size": 24.5,\n'
            f'      "_comment_ledger_sync": "{audit}",\n      "entry_price": 20.0,',
        )
        assert zec_after == expected
        # Raw substring proof: the entry's inline levels/signals bytes
        # are exactly as authored (no re-dump normalized them).
        assert _ZEC_INLINE_LEVELS in zec_after
        assert _ZEC_INLINE_SIGNALS in zec_after
        # Round-trip: a second run is a byte-identical no-op.
        mtime = os.path.getmtime(str(held))
        second = sync_open_positions(db, str(held), now=NOW)
        assert second["changed"] is False
        assert second["written"] is False
        assert os.path.getmtime(str(held)) == mtime

    def test_appended_after_existing_entry_price(self, tmp_path):
        db = self._ledger(tmp_path, "hl:SUI", 100.0, 3.5)
        held = tmp_path / "held.json"
        doc = {
            "watches": [
                {
                    "name": "SUI",
                    "enabled": True,
                    "monitor_provider": "hl:SUI",
                    "interval": "4h",
                    "period": "6mo",
                    "entry_price": 3.5,
                    "levels": [{"type": "stop", "price": 2.8}],
                }
            ]
        }
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        sui_before = _blocks_by_name(held.read_text(encoding="utf-8"))["SUI"]

        report = sync_open_positions(db, str(held), now=NOW)

        assert report["written"] is True
        assert not any("re-serialized" in n for n in report["notes"])
        sui_after = _blocks_by_name(held.read_text(encoding="utf-8"))["SUI"]
        expected = sui_before.replace(
            '"entry_price": 3.5,',
            '"entry_price": 3.5,\n      "position_size": 100.0,\n'
            f'      "_comment_ledger_sync": "position_size null -> 100.0 (ledger-derived {NOW_TS})",',
        )
        assert sui_after == expected
        assert '"position_size": 100.0,' in sui_after


class TestDryRunThreeCases:
    def test_dry_run_reproduces_vvv_lit_eth_and_leaves_file_untouched(self, sync_fixture):
        _seed_price_cache(sync_fixture.db_path, "kraken:ETHEUR", 2600.0)
        before = sync_fixture.original_text()

        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, dry_run=True, now=NOW)

        assert report["dry_run"] is True
        assert report["written"] is False
        assert report["changed"] is True
        assert [r["name"] for r in report["removed"]] == ["VVV"]
        updates = {(u["name"], u["field"]): (u["was"], u["now"]) for u in report["updated"]}
        assert updates[("LIT", "position_size")] == (3417, 385.59124734)
        assert updates[("ETH", "position_size")] == (0.038, 0.03815984)
        assert sync_fixture.original_text() == before

    def test_removed_vvv_is_a_watchlist_candidate_carrying_its_zone(self, sync_fixture):
        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, dry_run=True, now=NOW)

        removed = report["removed"][0]
        assert removed["name"] == "VVV"
        assert removed["asset"] == "hl:VVV"
        assert removed["belongs_in"] == "watchlist"
        assert removed["levels"] == [{"type": "zone", "low": 7.5, "high": 9.0, "label": "T1 add zone", "emoji": "🟢"}]
        assert removed["comments"] == {"_comment_zone": "T1 add zone re-grounded 2026-09 after the runner"}
        assert "watchlist" in removed["note"]
        assert report["candidates"] == ["VVV"]

    def test_net_zero_float_residue_counts_as_flat(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-08-01T10:00:00Z", "BUY", "hl:VVV", qty=317.860023518631, price=15.7302)
        add_transaction(db, pid, "2026-08-02T10:00:00Z", "BUY", "hl:VVV", qty=431.17, price=11.6)
        add_transaction(db, pid, "2026-08-03T10:00:00Z", "SELL", "hl:VVV", qty=569.0, price=17.5484)
        add_transaction(db, pid, "2026-08-04T10:00:00Z", "BUY", "hl:VVV", qty=3.82975427, price=0.0)
        add_transaction(db, pid, "2026-08-05T10:00:00Z", "SELL", "hl:VVV", qty=183.859777791626, price=27.173793)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "VVV", "enabled": True, "monitor_provider": "hl:VVV", "position_size": 180.03}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), now=NOW)

        assert report["removed"][0]["name"] == "VVV"
        assert report["removed"][0]["net"] <= FLAT_EPSILON


# ───────────────────────────────────────────── byte-identical preservation


def _blocks_by_name(text: str) -> dict[str, str]:
    """Raw byte block of each ``watches`` element, keyed by name."""
    doc = json.loads(text)
    return {w["name"]: block for w, block in zip(doc["watches"], _raw_element_blocks(text))}


class TestByteIdenticalPreservation:
    def test_surviving_entries_keep_handauthored_fields_byte_identical(self, sync_fixture):
        _seed_price_cache(sync_fixture.db_path, "kraken:ETHEUR", 2600.0)
        original = sync_fixture.original_text()
        original_doc = json.loads(original)
        raw_by_name = dict(zip([w["name"] for w in original_doc["watches"]], _raw_element_blocks(original)))

        sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        new_text = sync_fixture.new_text()
        assert new_text.endswith("\n")
        assert "🟡" in new_text  # raw emoji round-trips (ensure_ascii detection)
        assert "\\ud83d" not in new_text

        # Untouched entries (SOL unchanged, ZEC disabled, NOPE unmatched)
        # survive as their exact original raw bytes — raw substring
        # containment, not line-stripped comparison.
        for name in ("SOL", "ZEC", "NOPE"):
            assert raw_by_name[name] in new_text, f"{name}: raw block not byte-identical"
        # The bytes before the array (incl. the top-level _comment) and
        # the trailing bytes after the array close are preserved verbatim.
        assert new_text.startswith(original[: original.index("[", original.index('"watches"'))])
        assert new_text.endswith(original[original.rindex("]") + 1 :])

        new_doc = json.loads(new_text)
        assert [w["name"] for w in new_doc["watches"]] == ["LIT", "ETH", "PENDLE", "SOL", "ZEC", "NOPE"]
        assert new_doc["_comment"] == original_doc["_comment"]
        orig_by_name = {w["name"]: w for w in original_doc["watches"]}
        new_by_name = {w["name"]: w for w in new_doc["watches"]}
        for name in ("LIT", "ETH", "PENDLE", "SOL", "ZEC", "NOPE"):
            orig_keys = list(orig_by_name[name].keys())
            new_keys = list(new_by_name[name].keys())
            # Value-level splicing inserts the audit note (and a filled
            # entry_price) adjacent to position_size, so new keys may
            # interleave — but the original keys keep their order.
            assert [k for k in new_keys if k in orig_keys] == orig_keys
            assert new_by_name[name]["levels"] == orig_by_name[name]["levels"]
        for fragment in (
            '"low": 1500,',
            '"high": 1700,',
            '"label": "re-accumulate",',
            '"price": 1.9',
            '"price": 3.5,',
            '"price": 4.2,',
            '"below": 1400',
            '"format_style": "default"',
            '"interval": "1d"',
            '"period": "3mo"',
            '"monitor_provider": "kraken:PENDLEEUR"',
            '"_comment_staking": "staking accrual lands in the ledger"',
        ):
            assert fragment in new_text, f"verbatim fragment missing: {fragment}"

    def test_inline_style_file_splices_only_the_corrected_entry(self, sync_fixture):
        sync_fixture.write(_INLINE_HELD)
        original = sync_fixture.original_text()
        original_blocks = _raw_element_blocks(original)
        sol_block = original_blocks[1]

        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        assert report["changed"] is True
        assert report["written"] is True
        assert report["notes"] == []
        new_text = sync_fixture.new_text()
        # The untouched entry keeps its inline levels/signals formatting
        # byte-identical, and so do the bytes around the array.
        assert sol_block in new_text, "untouched inline entry was reformatted"
        assert new_text.startswith(original[: original.index("[", original.index('"watches"'))])
        assert new_text.endswith(original[original.rindex("]") + 1 :])
        # The corrected entry is still valid JSON with the new size.
        by_name = {w["name"]: w for w in json.loads(new_text)["watches"]}
        assert by_name["LIT"]["position_size"] == pytest.approx(385.59124734)
        assert by_name["LIT"]["entry_price"] == 2.32
        assert by_name["LIT"]["_comment_ledger_sync"].startswith("position_size 3417.0 -> 385.59124734")
        # Round-trip: a second run is a byte-identical no-op.
        mtime = os.path.getmtime(sync_fixture.held_path)
        second = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)
        assert second["changed"] is False
        assert second["written"] is False
        assert sync_fixture.new_text() == new_text
        assert os.path.getmtime(sync_fixture.held_path) == mtime

    def test_entry_price_filled_when_absent_and_handauthored_untouched(self, sync_fixture):
        sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        by_name = {w["name"]: w for w in json.loads(sync_fixture.new_text())["watches"]}
        assert by_name["PENDLE"]["entry_price"] == pytest.approx(4.2)
        assert by_name["ETH"]["entry_price"] == 1973.28
        assert by_name["LIT"]["entry_price"] == 2.32

    def test_entry_price_fill_is_reported(self, sync_fixture):
        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, dry_run=True, now=NOW)

        fills = [u for u in report["updated"] if u["field"] == "entry_price"]
        assert fills == [
            {"name": "PENDLE", "asset": "kraken:PENDLEEUR", "field": "entry_price", "was": None, "now": 4.2}
        ]

    def test_zero_cost_basis_position_does_not_fill_entry_price(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:AIRDROP", qty=25.0, price=0.0)
        held = tmp_path / "held.json"
        doc = {
            "watches": [
                {
                    "name": "AIRDROP",
                    "enabled": True,
                    "monitor_provider": "hl:AIRDROP",
                    "position_size": 1.0,
                    "levels": [{"type": "drop", "pct": -5}],
                }
            ]
        }
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), now=NOW)

        # position_size is corrected, but entry_price is NOT filled from a
        # zero ledger avg cost: position-watchdog divides by entry in the
        # drop path, so a filled 0.0 would ZeroDivisionError on next tick.
        assert [(u["field"], u["now"]) for u in report["updated"]] == [("position_size", 25.0)]
        by_name = {w["name"]: w for w in json.loads(held.read_text())["watches"]}
        assert by_name["AIRDROP"].get("entry_price") is None
        assert any("zero-cost-basis" in note for note in report["notes"])


class TestValueLevelSpliceByteIdentity:
    """The corrected entry's raw bytes must change ONLY at the patched
    value tokens — hand-authored ``levels``/``signals`` survive
    byte-identically (git-diff-style before/after raw comparison)."""

    def test_corrected_entry_splices_only_value_tokens(self, sync_fixture):
        sync_fixture.write(_BYTEIDENT_HELD)
        before_blocks = _blocks_by_name(sync_fixture.original_text())
        lit_before = before_blocks["LIT"]
        assert lit_before.count('"position_size": 3417,') == 1
        audit = f"position_size 3417.0 -> 385.59124734 (ledger-derived {NOW_TS})"

        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        assert report["changed"] is True
        assert report["written"] is True
        assert not any("re-serialized" in n for n in report["notes"])
        after_blocks = _blocks_by_name(sync_fixture.new_text())
        lit_after = after_blocks["LIT"]
        # Git-diff-style raw comparison: the ONLY textual changes in the
        # corrected entry are the position_size token and the appended
        # _comment_ledger_sync member — levels/signals bytes identical.
        expected_lit = lit_before.replace(
            '"position_size": 3417,',
            f'"position_size": 385.59124734,\n      "_comment_ledger_sync": "{audit}",',
        )
        assert lit_after == expected_lit
        # Hand-authored literals were not normalized (500.00 -> 500.0
        # would be a whole-element re-dump).
        assert '"price": 500.00' in lit_after
        assert '"price": 4.20' in lit_after
        assert '"below": 1.60' in lit_after
        assert '"price": 500.00' in sync_fixture.new_text()
        assert '"signals": [{"strategies": ["trend-follow"], "min_conviction": 3, "cooldown_hours": 2}]' in lit_after
        # The untouched SOL entry keeps its exact original raw bytes.
        assert after_blocks["SOL"] == before_blocks["SOL"]
        # The removed VVV entry is gone.
        assert "VVV" not in after_blocks

        # Round-trip: a second run is a byte-identical no-op.
        mtime = os.path.getmtime(sync_fixture.held_path)
        second = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)
        assert second["changed"] is False
        assert second["written"] is False
        assert sync_fixture.new_text() == sync_fixture.original_text()
        assert os.path.getmtime(sync_fixture.held_path) == mtime

    def test_entry_price_inserted_adjacent_to_position_size_without_redump(self, sync_fixture):
        held = {
            "watches": [
                {
                    "name": "PENDLE",
                    "enabled": True,
                    "monitor_provider": "kraken:PENDLEEUR",
                    "position_size": 10.0,
                    "_comment_ledger_sync": "position_size 8.0 -> 10.0 (ledger-derived 2026-09-01T00:00:00Z)",
                    "levels": [{"type": "stop", "price": 3.0}],
                }
            ]
        }
        sync_fixture.write(json.dumps(held, indent=2) + "\n")
        pendle_before = _blocks_by_name(sync_fixture.original_text())["PENDLE"]

        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        assert report["written"] is True
        assert not any("re-serialized" in n for n in report["notes"])
        pendle_after = _blocks_by_name(sync_fixture.new_text())["PENDLE"]
        expected = pendle_before.replace(
            '"position_size": 10.0,',
            '"position_size": 12.5,\n      "entry_price": 4.2,',
        ).replace(
            '"_comment_ledger_sync": "position_size 8.0 -> 10.0 (ledger-derived 2026-09-01T00:00:00Z)",',
            '"_comment_ledger_sync": "position_size 8.0 -> 10.0 (ledger-derived 2026-09-01T00:00:00Z)'
            f" | position_size 10.0 -> 12.5 (ledger-derived {NOW_TS})"
            f' | entry_price filled 4.2 (ledger avg cost, ledger-derived {NOW_TS})",',
        )
        assert pendle_after == expected


# ────────────────────────────────────────────── audit note + idempotency


class TestAuditNoteAndIdempotency:
    def test_audit_note_records_old_to_new_with_injected_now(self, sync_fixture):
        sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        by_name = {w["name"]: w for w in json.loads(sync_fixture.new_text())["watches"]}
        assert by_name["LIT"]["_comment_ledger_sync"] == (
            f"position_size 3417.0 -> 385.59124734 (ledger-derived {NOW_TS})"
        )
        assert by_name["ETH"]["_comment_ledger_sync"] == (
            f"position_size 0.038 -> 0.03815984 (ledger-derived {NOW_TS})"
        )
        assert by_name["PENDLE"]["_comment_ledger_sync"] == (
            "position_size 8.0 -> 10.0 (ledger-derived 2026-09-01T00:00:00Z)"
            f" | position_size 10.0 -> 12.5 (ledger-derived {NOW_TS})"
            f" | entry_price filled 4.2 (ledger avg cost, ledger-derived {NOW_TS})"
        )
        assert "_comment_ledger_sync" not in by_name["SOL"]
        assert by_name["ETH"]["_comment_staking"] == "staking accrual lands in the ledger"

    def test_idempotent_second_run_is_a_noop(self, sync_fixture):
        first = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)
        assert first["changed"] is True
        assert first["written"] is True
        after_first = sync_fixture.new_text()
        mtime_first = os.path.getmtime(sync_fixture.held_path)

        second = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        assert second["changed"] is False
        assert second["written"] is False
        assert second["removed"] == []
        assert second["updated"] == []
        assert set(second["unchanged"]) == {"LIT", "ETH", "PENDLE", "SOL"}
        assert sync_fixture.new_text() == after_first
        assert os.path.getmtime(sync_fixture.held_path) == mtime_first

    def test_planner_never_writes(self, sync_fixture):
        before = sync_fixture.original_text()

        report = plan_open_positions_sync(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        assert report["dry_run"] is True
        assert report["written"] is False
        assert report["changed"] is True
        assert sync_fixture.original_text() == before


# ────────────────────────────────────── unwatched / unmatched / ambiguous


class TestMembershipReporting:
    def test_unwatched_unmatched_disabled_reported_and_kept_untouched(self, sync_fixture):
        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, dry_run=True, now=NOW)

        assert report["unwatched"] == [{"asset": "yf:QOMP.DE", "ledger_net": 72.32423, "portfolios": ["main"]}]
        assert report["unmatched"] == [{"name": "NOPE", "reason": "no ledger asset matches monitor_provider 'hl:NOPE'"}]
        assert report["ambiguous"] == []
        assert report["skipped_disabled"] == ["ZEC"]
        by_name = {w["name"]: w for w in json.loads(sync_fixture.original_text())["watches"]}
        assert by_name["NOPE"]["position_size"] == 1.0
        assert by_name["ZEC"]["position_size"] == 24.5

    def test_ambiguous_two_assets_normalize_to_same_ticker(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "kraken:FOOUSD", qty=3.0, price=1.0)
        add_transaction(db, pid, "2026-09-02T10:00:00Z", "BUY", "hl:FOO", qty=2.0, price=1.0)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "FOO", "enabled": True, "monitor_provider": "yf:FOO", "position_size": 99.0}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), dry_run=True, now=NOW)

        assert len(report["ambiguous"]) == 1
        assert report["ambiguous"][0]["name"] == "FOO"
        assert "normalize to 'FOO'" in report["ambiguous"][0]["reason"]
        # The watch names the held assets (both normalize to FOO) — the
        # ambiguous line already reports them; no duplicate "! unwatched".
        assert report["unwatched"] == []
        assert json.loads(held.read_text())["watches"][0]["position_size"] == 99.0

    def test_ambiguous_same_asset_held_in_two_portfolios(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        p1 = add_portfolio(db, "a")
        p2 = add_portfolio(db, "b")
        add_transaction(db, p1, "2026-09-01T10:00:00Z", "BUY", "kraken:BTCUSD", qty=0.5, price=60000.0)
        add_transaction(db, p2, "2026-09-02T10:00:00Z", "BUY", "kraken:BTCUSD", qty=0.25, price=61000.0)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "BTC", "enabled": True, "monitor_provider": "kraken:BTCUSD", "position_size": 1.0}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), dry_run=True, now=NOW)

        assert report["ambiguous"][0]["name"] == "BTC"
        assert "more than one portfolio" in report["ambiguous"][0]["reason"]
        # The ambiguous watch names the held asset — no duplicate
        # "! unwatched" line for it.
        assert report["unwatched"] == []

    def test_normalized_ticker_match_fills_position_size(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:FOO", qty=7.5, price=2.0)
        held = tmp_path / "held.json"
        doc = {
            "watches": [
                {
                    "name": "FOO",
                    "enabled": True,
                    "monitor_provider": "yf:FOO",
                    "levels": [{"type": "stop", "price": 1.0}],
                }
            ]
        }
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), now=NOW)

        assert [(u["field"], u["now"]) for u in report["updated"]] == [
            ("position_size", 7.5),
            ("entry_price", 2.0),
        ]
        assert json.loads(held.read_text())["watches"][0]["position_size"] == 7.5


# ──────────────────────────────────── cross-provider flat/negative nets


class TestCrossProviderCandidateClassification:
    """A unique normalized-ticker candidate is classified by its ledger
    net exactly like an exact-key match — a genuinely flat cross-provider
    watch is removed and a negative one is reported — instead of being
    reported ``unmatched`` as 'not held (ledger net flat)'."""

    def _ledger(self, tmp_path, sold_qty):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-08-01T10:00:00Z", "BUY", "hl:VVV", qty=27.173793, price=15.0)
        add_transaction(db, pid, "2026-08-02T10:00:00Z", "SELL", "hl:VVV", qty=sold_qty, price=16.0)
        return db

    def _held(self, tmp_path):
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "VVV", "enabled": True, "monitor_provider": "yf:VVV", "position_size": 27.17}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return held

    def test_flat_cross_provider_watch_removed_as_watchlist_candidate(self, tmp_path):
        db = self._ledger(tmp_path, sold_qty=27.173793)
        held = self._held(tmp_path)

        report = sync_open_positions(db, str(held), now=NOW)

        assert [r["name"] for r in report["removed"]] == ["VVV"]
        assert report["removed"][0]["asset"] == "hl:VVV"
        assert report["removed"][0]["belongs_in"] == "watchlist"
        assert report["candidates"] == ["VVV"]
        assert report["unmatched"] == []
        assert report["negative_net"] == []
        assert json.loads(held.read_text())["watches"] == []
        # A second run is a no-op.
        second = sync_open_positions(db, str(held), now=NOW)
        assert second["changed"] is False
        assert second["written"] is False

    def test_negative_cross_provider_net_kept_and_reported(self, tmp_path):
        db = self._ledger(tmp_path, sold_qty=30.0)
        held = self._held(tmp_path)
        before = held.read_text(encoding="utf-8")

        report = sync_open_positions(db, str(held), now=NOW)

        assert [e["name"] for e in report["negative_net"]] == ["VVV"]
        assert report["negative_net"][0]["asset"] == "hl:VVV"
        assert report["negative_net"][0]["net"] == pytest.approx(-2.826207)
        assert report["removed"] == []
        assert report["candidates"] == []
        assert report["unmatched"] == []
        assert report["changed"] is False
        assert report["written"] is False
        assert held.read_text(encoding="utf-8") == before
        # A second run is a no-op.
        second = sync_open_positions(db, str(held), now=NOW)
        assert second["changed"] is False
        assert second["written"] is False


# ───────────────────────────────────────────── parked watches (falsy enabled)


class TestParkedWatchGate:
    """Any falsy ``enabled`` (missing, null) parks a watch exactly like
    position-watchdog's own ``if not watch.get("enabled"): continue``
    skip — the watchdog never runs such an entry, so the sync must not
    rewrite or remove it either."""

    def test_missing_and_null_enabled_flat_ledger_kept_byte_identical(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:PARKED", qty=5.0, price=1.0)
        add_transaction(db, pid, "2026-09-02T10:00:00Z", "SELL", "hl:PARKED", qty=5.0, price=1.2)
        add_transaction(db, pid, "2026-09-03T10:00:00Z", "BUY", "hl:NULLOFF", qty=2.0, price=3.0)
        add_transaction(db, pid, "2026-09-04T10:00:00Z", "SELL", "hl:NULLOFF", qty=2.0, price=3.3)
        add_transaction(db, pid, "2026-09-05T10:00:00Z", "BUY", "hl:LIT", qty=10.0, price=2.0)
        held = tmp_path / "held.json"
        doc = {
            "watches": [
                {
                    "name": "PARKED",
                    "monitor_provider": "hl:PARKED",
                    "position_size": 5.0,
                    "levels": [{"type": "stop", "price": 0.8}],
                },
                {
                    "name": "NULLOFF",
                    "enabled": None,
                    "monitor_provider": "hl:NULLOFF",
                    "position_size": 2.0,
                    "levels": [{"type": "stop", "price": 2.5}],
                },
                {
                    "name": "LIT",
                    "enabled": True,
                    "monitor_provider": "hl:LIT",
                    "entry_price": 2.0,
                    "position_size": 3417,
                    "levels": [{"type": "stop", "price": 1.9}],
                },
            ]
        }
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        before_blocks = _blocks_by_name(held.read_text(encoding="utf-8"))

        report = sync_open_positions(db, str(held), now=NOW)

        # The drifted ENABLED watch was corrected, so the run wrote — the
        # parked entries must still survive byte-identical.
        assert report["changed"] is True
        assert report["written"] is True
        assert report["skipped_disabled"] == ["PARKED", "NULLOFF"]
        assert report["removed"] == []
        assert report["candidates"] == []
        assert not any("re-serialized" in n for n in report["notes"])
        after_blocks = _blocks_by_name(held.read_text(encoding="utf-8"))
        assert after_blocks["PARKED"] == before_blocks["PARKED"]
        assert after_blocks["NULLOFF"] == before_blocks["NULLOFF"]
        assert after_blocks["LIT"] != before_blocks["LIT"]
        by_name = {w["name"]: w for w in json.loads(held.read_text())["watches"]}
        assert by_name["PARKED"]["position_size"] == 5.0
        assert by_name["NULLOFF"]["position_size"] == 2.0
        assert by_name["LIT"]["position_size"] == 10.0

    def test_parked_watch_does_not_unwatch_its_held_asset(self, tmp_path):
        # An entry with ``enabled`` MISSING whose asset is held: the
        # watchdog never runs it, but the entry already names the asset,
        # so the report must not also print "! unwatched" for it (a
        # duplicate line invites a duplicate watch entry).
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:HELDNO", qty=4.0, price=2.0)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "HELDNO", "monitor_provider": "hl:HELDNO", "position_size": 1.0}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), dry_run=True, now=NOW)

        assert report["skipped_disabled"] == ["HELDNO"]
        assert report["unwatched"] == []


# ────────────────────────────────────────────────────── negative ledger net

# Spot-shaped over-sell: buys then a larger SELL nets -11.5 (a perps
# short records the same SELL-only shape on the shared kraken:<PAIR>
# key). Not flat — the watch must be kept byte-identical, never removed.
_NEGATIVE_NET_HELD = """{
  "watches": [
    {
      "name": "DOGE",
      "enabled": true,
      "monitor_provider": "hl:DOGE",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 0.2,
      "position_size": 100.0,
      "levels": [
        {"type": "stop", "price": 0.15}
      ]
    },
    {
      "name": "LIT",
      "enabled": true,
      "monitor_provider": "hl:LIT",
      "interval": "4h",
      "period": "6mo",
      "entry_price": 2.32,
      "position_size": 3417,
      "levels": [
        {"type": "stop", "price": 1.9}
      ]
    }
  ]
}
"""


class TestNegativeNet:
    def _ledger_with_oversold(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        for ts, side, asset, qty, price in [
            ("2026-07-01T10:00:00Z", "BUY", "hl:LIT", 1263.05, 1.583),
            ("2026-07-02T10:00:00Z", "BUY", "hl:LIT", 3049.16, 1.64),
            ("2026-07-03T10:00:00Z", "SELL", "hl:LIT", 3049.16, 1.8467),
            ("2026-07-04T10:00:00Z", "BUY", "hl:LIT", 2154.33124734, 2.32),
            ("2026-07-05T10:00:00Z", "SELL", "hl:LIT", 1925.41, 3.636),
            ("2026-07-06T10:00:00Z", "SELL", "hl:LIT", 692.12, 4.334458),
            ("2026-07-07T10:00:00Z", "SELL", "hl:LIT", 414.26, 4.828808),
            ("2026-08-01T10:00:00Z", "BUY", "hl:DOGE", 100.0, 0.2),
            ("2026-08-02T10:00:00Z", "SELL", "hl:DOGE", 111.5, 0.25),
        ]:
            add_transaction(db, pid, ts, side, asset, qty=qty, price=price)
        return db

    def test_negative_net_watch_kept_byte_identical_and_reported(self, tmp_path):
        db = self._ledger_with_oversold(tmp_path)
        held = tmp_path / "held.json"
        held.write_text(_NEGATIVE_NET_HELD, encoding="utf-8")
        before_blocks = _blocks_by_name(_NEGATIVE_NET_HELD)

        report = sync_open_positions(db, str(held), now=NOW)

        # LIT was corrected, so the run wrote — but the negative-net
        # DOGE entry must not be touched by it.
        assert report["changed"] is True
        assert [e["name"] for e in report["negative_net"]] == ["DOGE"]
        entry = report["negative_net"][0]
        assert entry["asset"] == "hl:DOGE"
        assert entry["net"] == pytest.approx(-11.5)
        assert "short or over-sold" in entry["note"]
        assert "DOGE" not in report["candidates"]
        assert report["removed"] == []
        assert any("negative" in note and "DOGE" in note for note in report["notes"])
        after_blocks = _blocks_by_name(held.read_text(encoding="utf-8"))
        assert after_blocks["DOGE"] == before_blocks["DOGE"]
        assert after_blocks["LIT"] != before_blocks["LIT"]
        by_name = {w["name"]: w for w in json.loads(held.read_text())["watches"]}
        assert by_name["DOGE"]["position_size"] == 100.0

        # A second run is a no-op (DOGE still reported, nothing written).
        text_after_first = held.read_text(encoding="utf-8")
        mtime = os.path.getmtime(str(held))
        second = sync_open_positions(db, str(held), now=NOW)
        assert second["changed"] is False
        assert second["written"] is False
        assert [e["name"] for e in second["negative_net"]] == ["DOGE"]
        assert held.read_text(encoding="utf-8") == text_after_first
        assert os.path.getmtime(str(held)) == mtime

    def test_human_report_line_is_distinct_from_removals(self, tmp_path, monkeypatch, capsys):
        db = self._ledger_with_oversold(tmp_path)
        held = tmp_path / "held.json"
        held.write_text(_NEGATIVE_NET_HELD, encoding="utf-8")
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", db)
        monkeypatch.delenv(ENV_OPEN_POSITIONS_PATH, raising=False)

        rc = _run_cli("sync-open-positions", "--config", str(held), "--dry-run", monkeypatch=monkeypatch)

        assert rc is None
        out = capsys.readouterr().out
        assert "  + DOGE     ledger net negative (-11.5) — short or over-sold; watch kept untouched" in out
        assert "ledger flat -> removed" not in out
        assert "  note: DOGE: ledger net is negative (-11.5)" in out

    def test_sell_only_short_ledger_also_reported_negative(self, tmp_path):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "SELL", "kraken:ETHEUR", qty=0.5, price=2600.0)
        held = tmp_path / "held.json"
        doc = {
            "watches": [
                {
                    "name": "ETH",
                    "enabled": True,
                    "monitor_provider": "kraken:ETHEUR",
                    "position_size": 0.5,
                    "levels": [{"type": "stop", "price": 2400.0}],
                }
            ]
        }
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        report = sync_open_positions(db, str(held), now=NOW)

        assert [e["name"] for e in report["negative_net"]] == ["ETH"]
        assert report["negative_net"][0]["net"] == pytest.approx(-0.5)
        assert report["changed"] is False
        assert held.read_text(encoding="utf-8") == json.dumps(doc, indent=2) + "\n"


# ────────────────────────────────────────────────────── stale level flags


class TestStaleLevels:
    def test_stale_zones_flagged_with_distance_levels_unchanged(self, sync_fixture):
        _seed_price_cache(sync_fixture.db_path, "kraken:ETHEUR", 2600.0)
        original = sync_fixture.original_text()

        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, dry_run=True, now=NOW)

        assert [s["name"] for s in report["stale_levels"]] == ["ETH"]
        stale = report["stale_levels"][0]
        assert stale["price"] == 2600.0
        assert stale["price_source"] == "price_cache"
        assert stale["distance_pct"] == pytest.approx(-34.6)
        assert stale["zones"][0]["low"] == 1500
        assert stale["zones"][0]["high"] == 1700
        assert sync_fixture.original_text() == original

    def test_price_override_flags_removed_entry_at_26_04(self, sync_fixture):
        report = sync_open_positions(
            sync_fixture.db_path,
            sync_fixture.held_path,
            dry_run=True,
            price_overrides={"hl:VVV": 26.04},
            now=NOW,
        )

        stale = {s["name"]: s for s in report["stale_levels"]}
        assert stale["VVV"]["price"] == 26.04
        assert stale["VVV"]["price_source"] == "price-override"
        assert stale["VVV"]["distance_pct"] == pytest.approx(-65.4)
        assert "ETH" not in stale

    def test_price_unavailable_listed_without_crash(self, sync_fixture):
        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, dry_run=True, now=NOW)

        assert set(report["price_unavailable"]) == {"VVV", "SOL", "ETH"}
        assert report["stale_levels"] == []


# ───────────────────────────────────────────────── no venue API anywhere


def _boom(*args, **kwargs):
    raise AssertionError("venue API must not be called by the open-positions sync path")


class TestNoVenueApiCalls:
    def test_library_and_cli_run_without_venue_calls_or_sockets(self, sync_fixture, monkeypatch, capsys):
        import subprocess

        import analysis.data
        import portfolio.db

        monkeypatch.setattr(analysis.data, "fetch_spot_price", _boom)
        monkeypatch.setattr(analysis.data, "fetch_ohlc", _boom)
        monkeypatch.setattr(portfolio.db, "refresh_prices", _boom)
        monkeypatch.setattr(socket.socket, "__init__", _boom)
        # The repo's venue path shells out to the `kraken` CLI via
        # subprocess (analysis/providers/execution/kraken_spot.py
        # _run_kraken) — a regression calling an execution provider must
        # be blocked where the CLI is installed too.
        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr(subprocess, "Popen", _boom)
        _seed_price_cache(sync_fixture.db_path, "kraken:ETHEUR", 2600.0)

        report = sync_open_positions(sync_fixture.db_path, sync_fixture.held_path, now=NOW)
        assert report["changed"] is True
        assert report["written"] is True

        held2 = sync_fixture.held_path + ".second"
        with open(held2, "w", encoding="utf-8") as f:
            f.write(json.dumps(_held_doc(), indent=2, ensure_ascii=False) + "\n")
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", sync_fixture.db_path)
        with patch.object(sys, "argv", ["run.py", "sync-open-positions", "--config", held2, "--dry-run", "--json"]):
            _load_run_module().main()
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["errors"] == []
        assert envelope["data"]["dry_run"] is True

    def test_price_lookup_goes_through_local_cache_hook(self, sync_fixture, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "portfolio.sync.get_cached_prices", lambda db_path: calls.append(db_path) or {"hl:LIT": 4.9}
        )

        report = plan_open_positions_sync(sync_fixture.db_path, sync_fixture.held_path, now=NOW)

        assert calls == [sync_fixture.db_path]
        assert report["changed"] is True


# ────────────────────────────────────────────────────────────── CLI surface


class TestCli:
    def _env(self, monkeypatch, db_path):
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", db_path)
        monkeypatch.delenv(ENV_OPEN_POSITIONS_PATH, raising=False)

    def test_json_envelope(self, sync_fixture, monkeypatch, capsys):
        self._env(monkeypatch, sync_fixture.db_path)
        _seed_price_cache(sync_fixture.db_path, "kraken:ETHEUR", 2600.0)

        rc = _run_cli("sync-open-positions", "--config", sync_fixture.held_path, "--json", monkeypatch=monkeypatch)

        assert rc is None
        envelope = json.loads(capsys.readouterr().out)
        assert set(envelope) == {"data", "count", "errors", "help"}
        assert envelope["errors"] == []
        assert envelope["count"] == 6
        assert envelope["help"]
        assert envelope["data"]["changed"] is True

    def test_dry_run_json_writes_nothing(self, sync_fixture, monkeypatch, capsys):
        self._env(monkeypatch, sync_fixture.db_path)
        before = sync_fixture.original_text()

        rc = _run_cli(
            "sync-open-positions", "--config", sync_fixture.held_path, "--dry-run", "--json", monkeypatch=monkeypatch
        )

        assert rc is None
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["data"]["dry_run"] is True
        assert envelope["data"]["written"] is False
        assert sync_fixture.original_text() == before

    def test_missing_config_and_env_exits_2_naming_env_var(self, sync_fixture, monkeypatch, capsys):
        self._env(monkeypatch, sync_fixture.db_path)

        with pytest.raises(SystemExit) as exc:
            _run_cli("sync-open-positions", monkeypatch=monkeypatch)

        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert ENV_OPEN_POSITIONS_PATH in err
        assert "--config" in err

    def test_config_flag_wins_over_env_var(self, sync_fixture, tmp_path, monkeypatch):
        self._env(monkeypatch, sync_fixture.db_path)
        other = tmp_path / "other-held.json"
        other.write_text(json.dumps(_held_doc(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(other))
        before_other = other.read_text()

        rc = _run_cli("sync-open-positions", "--config", sync_fixture.held_path, monkeypatch=monkeypatch)

        assert rc is None
        assert "385.59124734" in sync_fixture.new_text()
        assert other.read_text() == before_other

    def test_human_diff_lines(self, sync_fixture, monkeypatch, capsys):
        self._env(monkeypatch, sync_fixture.db_path)
        _seed_price_cache(sync_fixture.db_path, "kraken:ETHEUR", 2600.0)
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, sync_fixture.held_path)

        rc = _run_cli("sync-open-positions", monkeypatch=monkeypatch)

        assert rc is None
        out = capsys.readouterr().out
        assert "  - VVV" in out
        assert "ledger flat -> removed" in out
        assert "zones 7.5-9.0 are a watchlist candidate" in out
        assert "~ LIT" in out
        assert "position_size 3417.0 -> 385.59124734" in out
        assert "position_size 0.038 -> 0.03815984" in out
        assert "~ ETH" in out
        assert "= SOL" in out
        assert "unchanged" in out
        assert "! unwatched  yf:QOMP.DE (72.32423) — no watch entry" in out
        assert "[WARN] ETH zones sit 34.6% below the last cached price 2600" in out
        assert "  [SKIP] VVV      no cached price — stale-zone check skipped" in out
        assert "  [SKIP] SOL      no cached price — stale-zone check skipped" in out
        assert "  note: VVV: ledger net is flat" in out


# ───────────────────────────────────────── env resolution + post-fill hook


class TestResolveConfigPath:
    def test_cli_path_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, "/env/path.json")
        assert resolve_config_path("/cli/path.json") == "/cli/path.json"

    def test_env_var_used_when_no_cli_path(self, monkeypatch):
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, "/env/path.json")
        assert resolve_config_path() == "/env/path.json"

    def test_unset_env_and_no_cli_path_raises_actionable_oserror(self, monkeypatch):
        monkeypatch.delenv(ENV_OPEN_POSITIONS_PATH, raising=False)
        with pytest.raises(OSError) as exc:
            resolve_config_path()
        msg = str(exc.value)
        assert ENV_OPEN_POSITIONS_PATH in msg
        assert "--config PATH" in msg
        assert "~" not in msg


class TestSyncOpenPositionsFromEnv:
    def test_env_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv(ENV_OPEN_POSITIONS_PATH, raising=False)
        assert sync_open_positions_from_env("/tmp/does-not-matter.db") is None

    def test_env_set_syncs_held_file(self, tmp_path, monkeypatch):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:FOO", qty=7.5, price=2.0)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "FOO", "enabled": True, "monitor_provider": "hl:FOO", "position_size": 2.5}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(held))

        report = sync_open_positions_from_env(db, now=NOW)

        assert report is not None
        assert report["changed"] is True
        assert report["written"] is True
        assert json.loads(held.read_text())["watches"][0]["position_size"] == 7.5


class TestAfterFillHook:
    def test_env_unset_prints_exact_warning_and_returns_none(self, monkeypatch, capsys):
        monkeypatch.delenv(ENV_OPEN_POSITIONS_PATH, raising=False)

        result = sync_open_positions_after_fill("/tmp/does-not-matter.db")

        assert result is None
        captured = capsys.readouterr()
        assert captured.err == (
            "warning: MARKET_SKILLS_OPEN_POSITIONS_PATH not set — open-positions drift not checked after fill\n"
        )
        assert captured.out == ""

    def test_successful_sync_prints_one_concise_stderr_line(self, tmp_path, monkeypatch, capsys):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:FOO", qty=7.5, price=2.0)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "FOO", "enabled": True, "monitor_provider": "hl:FOO", "position_size": 2.5}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(held))

        result = sync_open_positions_after_fill(db)

        assert result is not None
        assert result["changed"] is True
        captured = capsys.readouterr()
        assert "open-positions: FOO position_size 2.5 -> 7.5" in captured.err
        assert captured.out == ""

    def test_broken_config_path_warns_and_returns_none_never_fatal(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(tmp_path / "missing.json"))

        result = sync_open_positions_after_fill(str(tmp_path / "l.db"))

        assert result is None
        captured = capsys.readouterr()
        assert captured.err.startswith("warning: open-positions sync failed: ")
        assert captured.out == ""

    def test_flat_removal_line_carries_zone_bounds_and_watchlist_hint(self, tmp_path, monkeypatch, capsys):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-08-01T10:00:00Z", "BUY", "hl:VVV", qty=15.0, price=20.0)
        add_transaction(db, pid, "2026-08-02T10:00:00Z", "SELL", "hl:VVV", qty=15.0, price=22.0)
        held = tmp_path / "held.json"
        doc = {
            "watches": [
                {
                    "name": "VVV",
                    "enabled": True,
                    "monitor_provider": "hl:VVV",
                    "position_size": 15.0,
                    "_comment_zone": "T1 add zone re-grounded 2026-09",
                    "levels": [
                        {"type": "zone", "low": 7.5, "high": 9.0, "label": "T1 add zone", "emoji": "🟢"},
                        {"type": "stop", "price": 6.0},
                    ],
                }
            ]
        }
        held.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(held))

        result = sync_open_positions_after_fill(db)

        assert result is not None
        assert result["changed"] is True
        captured = capsys.readouterr()
        # The stderr line must carry the zone bounds + the watchlist hint,
        # not just the removal — the hand-authored zones leave the held
        # file and must be re-grounded in the watchlist config.
        assert "VVV ledger flat -> removed (zones 7.5-9.0 — watchlist candidate)" in captured.err
        assert captured.out == ""

    def test_second_run_reports_no_drift(self, tmp_path, monkeypatch, capsys):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "BUY", "hl:FOO", qty=7.5, price=2.0)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "FOO", "enabled": True, "monitor_provider": "hl:FOO", "position_size": 2.5}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(held))
        sync_open_positions_after_fill(db)
        capsys.readouterr()

        result = sync_open_positions_after_fill(db)

        assert result is not None
        assert result["changed"] is False
        assert "open-positions: no drift" in capsys.readouterr().err

    def test_negative_net_fill_surfaces_on_stderr_without_drift(self, tmp_path, monkeypatch, capsys):
        db = str(tmp_path / "l.db")
        init_db(db)
        pid = add_portfolio(db, "p")
        add_transaction(db, pid, "2026-09-01T10:00:00Z", "SELL", "hl:DOGE", qty=1.5, price=0.25)
        held = tmp_path / "held.json"
        doc = {"watches": [{"name": "DOGE", "enabled": True, "monitor_provider": "hl:DOGE", "position_size": 1.0}]}
        held.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OPEN_POSITIONS_PATH, str(held))

        result = sync_open_positions_after_fill(db)

        # changed is False (nothing written) — the negative-net line must
        # not be hidden behind a bare "no drift".
        assert result is not None
        assert result["changed"] is False
        captured = capsys.readouterr()
        assert "DOGE ledger net negative (-1.5) — watch left untouched" in captured.err
        assert captured.out == ""


# ─────────────────────────────────────────────────────────────── constants


class TestConstants:
    def test_position_size_decimals_matches_card_expectation(self):
        assert POSITION_SIZE_DECIMALS == 8
        assert round(0.0381598383, POSITION_SIZE_DECIMALS) == 0.03815984

    def test_stale_zone_threshold(self):
        assert STALE_ZONE_MIN_DISTANCE_PCT == 25.0
