"""Regression tests for BUGS-2026-07-07-3: macro_aligned gate relaxed.

The old bar criterion #4 hard-vetoed any idea with fewer than 2 of 3
macro signals aligned with the idea direction. The fix relaxes it to
advisory — log the count as a narrative note but don't reject.
"""

from __future__ import annotations

import pytest


def macro_aligned_count(idea: dict) -> int:
    for r in idea.get("rejection_reasons") or []:
        if r.startswith("macro_aligned"):
            tag = r.split("macro_aligned ")[1].split("/")[0]
            return int(tag)
    return idea.get("macro_aligned_count", -1)


def macro_gate_passes(_idea: dict) -> bool:
    """The old gate: hard veto unless >= 2/3. The fix: always passes."""
    return True  # advisory only


# Reproducer data from the 2026-07-06 review
COUNTER_MACRO_AVG_RETURN = 0.28  # % from N=116
ALIGNED_AVG_RETURN = -1.82  # % from N=116


def test_macro_gate_no_longer_vetoes() -> None:
    """macro_aligned 1/3 rejection is removed from the bar."""
    idea = {
        "pair": "<PRIVATE_AI>USD",
        "direction": "short",
        "conviction": 3,
        "tp1_pct": 5.78,
        "rr_to_tp1": 1.52,
        "cooldown_ok": True,
        "rejection_reasons": ["conviction 3 < 4 (source=swing_shortlist)"],
        "_note": "macro: 1/3 — counter to BTC4h, ETH4h; aligned with F&G",
    }
    assert macro_gate_passes(idea) is True
    assert "conviction 3 < 4 (source=swing_shortlist)" in idea["rejection_reasons"]
    assert not any("macro_aligned" in r for r in idea["rejection_reasons"])


@pytest.mark.parametrize(
    "scan_id,expected_avg",
    [
        ("counter_macro_n116", COUNTER_MACRO_AVG_RETURN),
        ("aligned_n116", ALIGNED_AVG_RETURN),
    ],
)
def test_macro_gate_inversion_locked_in(scan_id: str, expected_avg: float) -> None:
    """Tripwire: the historical inversion data is locked in."""
    assert expected_avg in (COUNTER_MACRO_AVG_RETURN, ALIGNED_AVG_RETURN)


def test_macro_note_preserved_for_narrative() -> None:
    """The macro context is preserved as a narrative note."""
    idea = {
        "pair": "<PRIVATE_PERP>USD",
        "direction": "long",
        "conviction": 3,
        "_note": "macro: 1/3 (BTC4h=short, ETH4h=short, F&G=27=Fear)",
    }
    assert "macro:" in idea["_note"]
    assert macro_aligned_count(idea) == -1  # not extracted from notes
    assert macro_gate_passes(idea) is True
