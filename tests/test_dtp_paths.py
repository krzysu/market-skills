"""Unit tests for the daily-trade-pick journal path resolver.

The resolve_journal_path() function in analyze_journal.py is the single
read site for the journal path inside this repo. Per AGENTS.md
("no host-specific defaults in library code"), it must raise on unset
rather than fall back to a known host path.

Consumer-side invocation (scheduler + profile-level CLI wiring) is out
of scope for this repo and is not exercised here — see AGENTS.md "What
to avoid" for the boundary.

Tests:
  - resolve_journal_path raises OSError when flag+env are both missing.
  - resolve_journal_path honors the explicit path argument.
  - resolve_journal_path honors $MARKET_SKILLS_DAILY_TRADE_PICK_PATH
    when set.
"""

from __future__ import annotations

import os
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYZE = REPO_ROOT / "skills" / "daily-trade-pick" / "scripts" / "analyze_journal.py"


def _import_analyze():
    """Import analyze_journal.py as a module without executing main()."""
    spec = spec_from_file_location("analyze_journal_for_test", ANALYZE)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_raises_when_neither_flag_nor_env(monkeypatch):
    """AGENTS.md: env-var unset → raise, no host-default fallback."""
    monkeypatch.delenv("MARKET_SKILLS_DAILY_TRADE_PICK_PATH", raising=False)
    mod = _import_analyze()
    with pytest.raises(OSError, match="MARKET_SKILLS_DAILY_TRADE_PICK_PATH"):
        mod.resolve_journal_path(None)


def test_resolve_uses_explicit_argument(tmp_path):
    """Explicit path argument wins over env var (highest precedence)."""
    explicit = tmp_path / "explicit.json"
    explicit.write_text("[]")
    mod = _import_analyze()
    with patch.dict(os.environ, {"MARKET_SKILLS_DAILY_TRADE_PICK_PATH": "/should/be/ignored"}):
        result = mod.resolve_journal_path(str(explicit))
    assert result == explicit


def test_resolve_uses_env_var(tmp_path):
    """No explicit path → read $MARKET_SKILLS_DAILY_TRADE_PICK_PATH."""
    env_path = tmp_path / "env.json"
    env_path.write_text("[]")
    mod = _import_analyze()
    with patch.dict(os.environ, {"MARKET_SKILLS_DAILY_TRADE_PICK_PATH": str(env_path)}):
        result = mod.resolve_journal_path(None)
    assert result == env_path
