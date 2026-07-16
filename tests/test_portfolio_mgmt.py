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
