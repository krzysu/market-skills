"""Tests for skills/portfolio-mgmt path resolution.

Regression for the AGENTS.md rule: when ``$MARKET_SKILLS_PORTFOLIO_DB``
is unset, the library MUST raise — never fall back to a host-specific
default. The previous ``DB_DEFAULT = ~/.market-skills/portfolio.db``
violated this and silently diverged from the three downstream skills
(``execution-kraken-spot``, ``execution-kraken-perps``, ``risk-engine``)
that already read the env var.

The fixture also pins the surface area of the rule against future
contributors: a grep over library code, scripts, and SKILL.md files
must not contain ``~/.market-skills/portfolio.db`` or any other
hardcoded user-home path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.skill_loader import load_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def portfolio_lib(monkeypatch):
    monkeypatch.delenv("MARKET_SKILLS_PORTFOLIO_DB", raising=False)
    return load_skill("portfolio-mgmt")


def test_default_db_path_uses_env(monkeypatch):
    monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", "/custom/db/portfolio.db")
    lib = load_skill("portfolio-mgmt")
    assert lib.default_db_path() == "/custom/db/portfolio.db"


def test_default_db_path_requires_env(monkeypatch):
    monkeypatch.delenv("MARKET_SKILLS_PORTFOLIO_DB", raising=False)
    lib = load_skill("portfolio-mgmt")
    with pytest.raises(OSError, match="MARKET_SKILLS_PORTFOLIO_DB"):
        lib.default_db_path()


def test_analyze_raises_without_env(monkeypatch):
    monkeypatch.delenv("MARKET_SKILLS_PORTFOLIO_DB", raising=False)
    lib = load_skill("portfolio-mgmt")
    with pytest.raises(OSError, match="MARKET_SKILLS_PORTFOLIO_DB"):
        lib.analyze()


def test_default_db_path_unset_message_is_actionable(monkeypatch):
    """Regression: 2026-07-12 cold-start agent hit bare OSError with no
    hint of how to recover. The error must (a) name the env var and
    (b) suggest a concrete next step (export or --db=PATH) without
    embedding a literal host path that triggers the scrub guard.
    """
    monkeypatch.delenv("MARKET_SKILLS_PORTFOLIO_DB", raising=False)
    lib = load_skill("portfolio-mgmt")
    with pytest.raises(OSError) as exc_info:
        lib.default_db_path()
    msg = str(exc_info.value)
    # Names the env var so the agent knows which one is missing.
    assert "MARKET_SKILLS_PORTFOLIO_DB" in msg
    # Tells the agent what to do (export / --db).
    assert "--db" in msg
    # Scrub guard compatibility: no literal `~/.market-skills` in code.
    assert "~/.market-skills" not in msg


def test_analyze_with_explicit_path_works(monkeypatch, tmp_path):
    monkeypatch.delenv("MARKET_SKILLS_PORTFOLIO_DB", raising=False)
    db = tmp_path / "p.db"
    from portfolio.db import init_db

    init_db(str(db))
    lib = load_skill("portfolio-mgmt")
    summary = lib.analyze(str(db))
    assert summary["by_portfolio"] == []


def test_cli_raises_without_env(monkeypatch, tmp_path):
    """`portfolio-mgmt list` must not silently fall back when env is unset."""
    monkeypatch.delenv("MARKET_SKILLS_PORTFOLIO_DB", raising=False)
    result = subprocess.run(
        [sys.executable, "skills/portfolio-mgmt/scripts/run.py", "portfolio", "list"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={k: v for k, v in os.environ.items() if k != "MARKET_SKILLS_PORTFOLIO_DB"},
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "MARKET_SKILLS_PORTFOLIO_DB" in combined


# ───────────────────────────────────────────────────── static scrub guard ────


FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"~\/\.market-skills"),
    re.compile(r"\bDB_DEFAULT\b.*=.*os\.path\.expanduser"),
)


def _scan_for_host_specific_paths() -> list[tuple[Path, int, str]]:
    """Walk library code + SKILL.md files; report any forbidden references."""
    roots = [REPO_ROOT / "skills", REPO_ROOT / "analysis", REPO_ROOT / "portfolio"]
    offenders: list[tuple[Path, int, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".md"}:
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                for pat in FORBIDDEN_PATH_PATTERNS:
                    if pat.search(line):
                        offenders.append((path, n, line.strip()))
                        break
    return offenders


def test_no_host_specific_portfolio_paths_in_code_or_docs():
    """No library/script/SKILL.md may reference ``~/.market-skills/...``
    or recreate the old ``DB_DEFAULT = os.path.expanduser(...)`` pattern.

    AGENTS.md "What to avoid": library code must not embed host-specific
    filesystem paths. When the env var is unset, raise — don't fall back.
    """
    offenders = _scan_for_host_specific_paths()
    assert not offenders, (
        "Host-specific portfolio paths detected (AGENTS.md violation). "
        "Use $MARKET_SKILLS_PORTFOLIO_DB and raise on unset:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{n}: {line}" for p, n, line in offenders)
    )


# ───────────────────────────── portfolio id-or-name CLI resolution ──────────


def _run_cli(db_path: Path, *argv: str) -> subprocess.CompletedProcess:
    """Drive the REAL portfolio-mgmt CLI against a scratch DB."""
    return subprocess.run(
        [sys.executable, "skills/portfolio-mgmt/scripts/run.py", *argv],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
        env={**os.environ, "MARKET_SKILLS_PORTFOLIO_DB": str(db_path)},
    )


@pytest.fixture
def cli_db(tmp_path):
    """Initialized DB holding one synthetic portfolio (``alpha``).

    Returns ``(db_path, id_as_str)`` — the id comes from the CLI's own
    ``portfolio create --json`` output, never a hardcoded value.
    """
    from portfolio.db import init_db

    db = tmp_path / "p.db"
    init_db(str(db))
    created = _run_cli(db, "portfolio", "create", "--json", "--name", "alpha")
    assert created.returncode == 0, created.stderr
    return db, str(json.loads(created.stdout)["id"])


class TestPortfolioIdOrNameCli:
    """``portfolio show`` / ``rename`` / ``delete`` must resolve the same
    id-or-name rule as the ``--portfolio`` flag — a digit-only token as an
    id when such an id exists, else the exact name — and a miss must exit
    non-zero, never as a silent success. Synthetic portfolios only.

    Pre-fix, (a) ``show <id>`` printed ``No portfolio matching`` with exit 0,
    and (b)/(c) ``rename`` / ``delete`` by name died in argparse with
    ``invalid int value`` (exit 2) — each test below fails on that shape.
    Two further tests pin the fail-soft id conversion: a digit token that
    cannot be a SQLite id (too large, or non-decimal like ``²``) must miss
    with the friendly stderr line and exit 1, never a traceback.
    """

    def test_show_by_numeric_id(self, cli_db):
        db, pid = cli_db
        proc = _run_cli(db, "portfolio", "show", pid)
        assert proc.returncode == 0, proc.stderr
        assert "alpha" in proc.stdout

    def test_show_by_name(self, cli_db):
        db, _pid = cli_db
        proc = _run_cli(db, "portfolio", "show", "alpha")
        assert proc.returncode == 0, proc.stderr
        assert "alpha" in proc.stdout

    def test_show_unknown_exits_nonzero_with_no_stdout(self, cli_db):
        db, _pid = cli_db
        proc = _run_cli(db, "portfolio", "show", "nosuch")
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert "No portfolio matching 'nosuch'" in proc.stderr

    def test_show_oversized_digit_token_misses_softly(self, cli_db):
        db, _pid = cli_db
        token = "99999999999999999999"
        proc = _run_cli(db, "portfolio", "show", token)
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert f"No portfolio matching '{token}'" in proc.stderr
        assert "Traceback" not in proc.stdout
        assert "Traceback" not in proc.stderr

    def test_show_non_decimal_digit_token_misses_softly(self, cli_db):
        db, _pid = cli_db
        proc = _run_cli(db, "portfolio", "show", "²")
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert "No portfolio matching '²'" in proc.stderr
        assert "Traceback" not in proc.stdout
        assert "Traceback" not in proc.stderr

    def test_delete_by_name(self, cli_db):
        db, _pid = cli_db
        proc = _run_cli(db, "portfolio", "delete", "alpha", "--yes")
        assert proc.returncode == 0, proc.stderr
        assert "invalid int value" not in proc.stderr
        listing = _run_cli(db, "portfolio", "list", "--json")
        assert json.loads(listing.stdout) == []

    def test_rename_by_name(self, cli_db):
        db, pid = cli_db
        proc = _run_cli(db, "portfolio", "rename", "alpha", "beta")
        assert proc.returncode == 0, proc.stderr
        assert "invalid int value" not in proc.stderr
        shown = _run_cli(db, "portfolio", "show", pid)
        assert "beta" in shown.stdout

    def test_delete_by_numeric_id(self, cli_db):
        db, pid = cli_db
        proc = _run_cli(db, "portfolio", "delete", pid, "--yes")
        assert proc.returncode == 0, proc.stderr
        listing = _run_cli(db, "portfolio", "list", "--json")
        assert json.loads(listing.stdout) == []

    def test_rename_by_numeric_id(self, cli_db):
        db, pid = cli_db
        proc = _run_cli(db, "portfolio", "rename", pid, "beta")
        assert proc.returncode == 0, proc.stderr
        shown = _run_cli(db, "portfolio", "show", pid)
        assert "beta" in shown.stdout

    def test_delete_unknown_exits_nonzero_with_no_stdout(self, cli_db):
        db, _pid = cli_db
        proc = _run_cli(db, "portfolio", "delete", "nosuch", "--yes")
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert "No portfolio matching 'nosuch'" in proc.stderr

    def test_rename_unknown_exits_nonzero_with_no_stdout(self, cli_db):
        db, _pid = cli_db
        proc = _run_cli(db, "portfolio", "rename", "nosuch", "beta")
        assert proc.returncode == 1
        assert proc.stdout == ""
        assert "No portfolio matching 'nosuch'" in proc.stderr
