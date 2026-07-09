"""CLI surface tests for market-notes (scripts/run.py).

Tests subcommand dispatch, flag handling, and envelope output rather
than the notes library logic (already covered in test_notes.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_mod():
    run_path = os.path.join(REPO_ROOT, "skills", "market-notes", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("market_notes_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(mod, *argv):
    full = ["run.py", *argv]
    with patch.object(sys, "argv", full):
        try:
            return mod.main()
        except SystemExit as e:
            return e.code


def _envelope_of(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out)


@pytest.fixture
def notes_path(tmp_path):
    return tmp_path / "notes.json"


class TestNotesCliInit:
    def test_list_empty(self, notes_path, capsys):
        mod = _load_mod()
        rc = _run_cli(mod, "--json", f"--config={notes_path}", "list")
        assert rc in (0, None)
        env = _envelope_of(capsys)
        assert env["count"] == 0
        assert set(env.keys()) == {"data", "count", "errors", "help"}

    def test_add_then_list(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "test thesis")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "list")
        env = _envelope_of(capsys)
        assert env["count"] == 1
        assert "BTCUSD" in env["data"]["pairs"]

    def test_add_with_expires(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "ETHUSD", "short term", "--expires=1d")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "list")
        env = _envelope_of(capsys)
        assert env["count"] == 1

    def test_add_with_status_type(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(
            mod,
            f"--config={notes_path}",
            "add",
            "BTCUSD",
            "thesis note",
            "--status=thesis",
            "--type=thesis",
        )
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "list")
        env = _envelope_of(capsys)
        note = env["data"]["pairs"]["BTCUSD"][0]
        assert note["status"] == "thesis"
        assert note["type"] == "thesis"

    def test_remove_by_index(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "first")
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "second")
        _run_cli(mod, f"--config={notes_path}", "remove", "BTCUSD", "0")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "list")
        env = _envelope_of(capsys)
        notes = env["data"]["pairs"]["BTCUSD"]
        assert len(notes) == 1
        assert notes[0]["note"] == "second"

    def test_remove_out_of_range(self, notes_path, capsys):
        mod = _load_mod()
        rc = _run_cli(mod, f"--config={notes_path}", "remove", "BTCUSD", "99")
        assert rc == 1

    def test_prune(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "expiring", "--expires=1d")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "prune")
        result = json.loads(capsys.readouterr().out)
        assert "removed" in result

    def test_validate_valid(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "fine")
        capsys.readouterr()
        _run_cli(mod, f"--config={notes_path}", "validate")
        out = capsys.readouterr().out
        assert "OK" in out

    def test_list_with_all_flag(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "present")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "list", "--all")
        env = _envelope_of(capsys)
        assert env["count"] >= 1

    def test_add_empty_text_rejected(self, notes_path, capsys):
        mod = _load_mod()
        rc = _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "")
        assert rc == 2

    def test_help_lines_in_envelope(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "hi")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "list")
        env = _envelope_of(capsys)
        joined = " ".join(env["help"])
        assert "add" in joined

    def test_migrate_dry_run_on_fresh(self, notes_path, capsys):
        mod = _load_mod()
        _run_cli(mod, f"--config={notes_path}", "add", "BTCUSD", "typed")
        capsys.readouterr()
        _run_cli(mod, "--json", f"--config={notes_path}", "migrate", "--dry-run")
        result = json.loads(capsys.readouterr().out)
        assert result["notes_migrated"] == 0
