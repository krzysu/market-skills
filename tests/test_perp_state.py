"""Tests for analysis/perp_state.py — read-only perps state fetchers.

Covers: get_open_positions, get_funding_rate, get_mm_rate, and the
subprocess-error envelope handling (auth missing, CLI missing, malformed
JSON, timeout). All kraken CLI calls are mocked — no network.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from analysis.perp_state import (
    KRAKEN_FUTURES_MAP,
    get_funding_rate,
    get_mm_rate,
    get_open_positions,
)


def _kraken_result(payload: dict, returncode: int = 0) -> MagicMock:
    """Build a MagicMock that looks like a completed subprocess.run."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = json.dumps(payload) if payload is not None else ""
    m.stderr = ""
    return m


class TestGetOpenPositions:
    def test_returns_list_of_dicts(self) -> None:
        envelope = {
            "positions": [
                {"symbol": "PF_SOLUSD", "size": -10.5},
                {"symbol": "PF_BTCUSD", "size": 0.001},
            ]
        }
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_open_positions()
        assert result == [
            {"symbol": "PF_SOLUSD", "size": -10.5},
            {"symbol": "PF_BTCUSD", "size": 0.001},
        ]

    def test_filters_zero_size_positions(self) -> None:
        envelope = {
            "positions": [
                {"symbol": "PF_SOLUSD", "size": 0},
                {"symbol": "PF_BTCUSD", "size": 0.001},
                {"symbol": "PF_ETHUSD", "size": -2.0},
            ]
        }
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_open_positions()
        assert result == [
            {"symbol": "PF_BTCUSD", "size": 0.001},
            {"symbol": "PF_ETHUSD", "size": -2.0},
        ]

    def test_handles_dict_keyed_envelope(self) -> None:
        # Some CLI versions return a flat dict keyed by symbol.
        envelope = {
            "positions": {
                "PF_SOLUSD": {"size": -10.5},
                "PF_BTCUSD": {"size": 0.001},
            }
        }
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_open_positions()
        assert len(result) == 2
        symbols = {p["symbol"] for p in result}
        assert symbols == {"PF_SOLUSD", "PF_BTCUSD"}

    def test_returns_none_on_auth_error(self) -> None:
        # Auth error envelope: stdout has {"error": "auth"} on rc=0.
        auth_resp = {"error": "auth", "message": "no creds"}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(auth_resp)):
            result = get_open_positions()
        assert result is None

    def test_returns_none_on_cli_missing(self) -> None:
        with patch("analysis.perp_state.subprocess.run", side_effect=FileNotFoundError):
            result = get_open_positions()
        assert result is None

    def test_returns_empty_list_when_envelope_lacks_positions_key(self) -> None:
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result({})):
            result = get_open_positions()
        assert result == []

    def test_raises_on_timeout(self) -> None:
        with patch(
            "analysis.perp_state.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="kraken", timeout=30),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                get_open_positions()

    def test_skips_malformed_position_entries(self) -> None:
        envelope = {
            "positions": [
                {"symbol": "PF_SOLUSD", "size": -10.5},
                {"size": -5.0},  # missing symbol
                {"symbol": "PF_BTCUSD"},  # missing size
                "not a dict",
                {"symbol": "PF_ETHUSD", "size": "bad"},  # non-numeric size
            ]
        }
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_open_positions()
        assert result == [{"symbol": "PF_SOLUSD", "size": -10.5}]


class TestGetFundingRate:
    def test_long_gets_positive_sign(self) -> None:
        envelope = {
            "rates": [
                {"fundingRate": 0.001, "timestamp": "2025-01-01T00:00:00Z"},
                {"fundingRate": 0.0005, "timestamp": "2026-06-24T08:00:00Z"},
            ]
        }
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "buy")
        assert result == 0.0005

    def test_short_flips_sign(self) -> None:
        envelope = {"rates": [{"fundingRate": 0.0005, "timestamp": "2026-06-24T08:00:00Z"}]}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "sell")
        assert result == -0.0005

    def test_negative_raw_rate_for_long(self) -> None:
        envelope = {"rates": [{"fundingRate": -0.0003, "timestamp": "2026-06-24T08:00:00Z"}]}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "buy")
        assert result == -0.0003

    def test_negative_raw_rate_for_short(self) -> None:
        envelope = {"rates": [{"fundingRate": -0.0003, "timestamp": "2026-06-24T08:00:00Z"}]}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "sell")
        assert result == 0.0003  # flips: shorts receive

    def test_uses_most_recent_rate(self) -> None:
        envelope = {
            "rates": [
                {"fundingRate": 0.001, "timestamp": "2025-01-01T00:00:00Z"},
                {"fundingRate": 0.0002, "timestamp": "2025-06-01T00:00:00Z"},
                {"fundingRate": 0.0008, "timestamp": "2026-06-24T08:00:00Z"},
            ]
        }
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "buy")
        assert result == 0.0008

    def test_returns_none_on_auth_error(self) -> None:
        auth_resp = {"error": "auth", "message": "no creds"}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(auth_resp)):
            result = get_funding_rate("SOLUSD", "buy")
        assert result is None

    def test_returns_none_when_pair_unmapped(self) -> None:
        # No subprocess call should happen — the symbol resolution fails first.
        result = get_funding_rate("XYZUSD", "buy")
        assert result is None

    def test_returns_none_when_rates_missing(self) -> None:
        envelope = {"not_rates": []}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "buy")
        assert result is None

    def test_returns_none_when_last_entry_lacks_funding_rate(self) -> None:
        envelope = {"rates": [{"timestamp": "2026-06-24T08:00:00Z"}]}
        with patch("analysis.perp_state.subprocess.run", return_value=_kraken_result(envelope)):
            result = get_funding_rate("SOLUSD", "buy")
        assert result is None

    def test_invalid_side_raises(self) -> None:
        with pytest.raises(ValueError, match="side must be"):
            get_funding_rate("SOLUSD", "hold")  # type: ignore[arg-type]


class TestGetMmRate:
    def test_returns_mapped_rate(self) -> None:
        assert get_mm_rate("BTCUSD") == 0.005
        assert get_mm_rate("SOLUSD") == 0.01
        assert get_mm_rate("HYPEUSD") == 0.01

    def test_xbt_alias(self) -> None:
        assert get_mm_rate("XBTUSD") == 0.005

    def test_returns_none_for_unmapped(self) -> None:
        assert get_mm_rate("XYZUSD") is None

    def test_case_insensitive(self) -> None:
        assert get_mm_rate("solusd") == 0.01
        assert get_mm_rate("btcusd") == 0.005


class TestKrakenFuturesMapReExport:
    def test_map_present(self) -> None:
        assert "SOLUSD" in KRAKEN_FUTURES_MAP
        assert KRAKEN_FUTURES_MAP["SOLUSD"] == "PF_SOLUSD"
