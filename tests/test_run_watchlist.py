"""Tests for skills/run-watchlist/lib.py — pure analyze_ticker."""

import importlib.util
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB_PATH = os.path.join(_REPO_ROOT, "skills", "run-watchlist", "lib.py")
_spec = importlib.util.spec_from_file_location("run_watchlist_lib", _LIB_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
analyze_ticker = _mod.analyze_ticker


class FakeCandles:
    """Minimal candles stub — long enough for skills that require it."""

    def __init__(self, n: int = 300):
        self.n = n

    def __len__(self) -> int:
        return self.n


def test_analyze_ticker_l2_l3_notes():
    notes_called_with: list[str] = []

    def notes_loader(ticker: str):
        notes_called_with.append(ticker)
        return [{"note": "stub", "expires": None}]

    result = analyze_ticker(
        "BTCUSD",
        FakeCandles(),
        metadata={"tier": 2, "source": "kraken"},
        notes_loader=notes_loader,
    )
    assert result["ticker"] == "BTCUSD"
    assert result["metadata"] == {"tier": 2, "source": "kraken"}
    assert "l2" in result
    assert "l3" in result
    assert result["notes"] == [{"note": "stub", "expires": None}]
    assert notes_called_with == ["BTCUSD"]


def test_analyze_ticker_l2_only():
    result = analyze_ticker(
        "BTCUSD",
        FakeCandles(),
        include_l2=True,
        include_l3=False,
        include_notes=False,
    )
    assert "l2" in result
    assert "l3" not in result
    assert "notes" not in result


def test_analyze_ticker_l3_only():
    result = analyze_ticker(
        "BTCUSD",
        FakeCandles(),
        include_l2=False,
        include_l3=True,
        include_notes=False,
    )
    assert "l2" not in result
    assert "l3" in result


def test_analyze_ticker_no_notes():
    def loader(_t):
        return []

    result = analyze_ticker(
        "BTCUSD",
        FakeCandles(),
        include_notes=True,
        notes_loader=loader,
    )
    assert result["notes"] == []


def test_analyze_ticker_notes_loader_failure_swallowed():
    def bad_loader(_t):
        raise RuntimeError("disk error")

    result = analyze_ticker(
        "BTCUSD",
        FakeCandles(),
        include_notes=True,
        notes_loader=bad_loader,
    )
    assert result["notes"] == []
    assert "disk error" in result.get("notes_error", "")


def test_analyze_ticker_metadata_optional():
    result = analyze_ticker(
        "BTCUSD",
        FakeCandles(),
        metadata=None,
        include_l2=False,
        include_l3=False,
        include_notes=False,
    )
    assert "metadata" not in result
