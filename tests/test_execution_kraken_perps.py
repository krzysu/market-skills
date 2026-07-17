"""Tests for execution-kraken-perps.

Covers:
  - BracketSpec + BracketFill TypedDicts (importable)
  - validate_intent: perps-specific validation (leverage, bracket, venue)
  - ExecutionProvider registry: kraken-perps registered, supports check
  - KrakenPerpsExecutionProvider: place_order with mocked subprocess
    (set-leverage, market open, stop, take-profit; happy path +
     stop-fails-rollback + TP-fails-warning + auth error + idempotency),
    get_balance, get_open_orders, get_positions, cancel_order, supports
  - Perps risk policies: leverage_cap, liquidation_distance, stop_distance,
    funding_drag, duplicate_perps_position — pure functions, no I/O
  - select_policies auto-routes perps intents through PERPS_POLICIES
  - skills/execution-kraken-perps/lib.py: load_intent_file,
    intent_from_direct_args, render_intent_summary, render_confirmation,
    write_fill_to_portfolio
  - CLI surface: submit (paper / live / rejected / missing), balance,
    positions, cancel
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# Trigger provider auto-registration on import.
from analysis.providers.execution import kraken_perps as _execution_kraken_perps  # noqa: F401
from analysis.providers.execution.base import (
    ExecutionProvider,
    FillConfirmation,
    Intent,
    get_execution_provider,
    registered_venues,
    validate_intent,
)
from analysis.providers.execution.kraken_perps import (
    DEFAULT_LEVERAGE_CAP,
    LEVERAGE_CAPS,
    KrakenPerpsExecutionProvider,
    leverage_cap_for_pair,
    resolve_futures_symbol,
)

# ─────────────────────────────────────────────────────── Intent validation


class TestValidateIntentPerps:
    def test_minimal_perps_intent(self):
        intent = {
            "intent_id": "abc-123",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        validated = validate_intent(intent)
        assert validated["venue"] == "kraken-perps"
        assert validated["leverage"] == 2
        assert validated["bracket"]["stop_loss"] == 76.66

    def test_missing_leverage_is_ok_spot_intent(self):
        # Spot intents don't need leverage.
        intent = {
            "intent_id": "x",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "limit",
            "volume": 0.01,
            "limit_price": 65000,
        }
        validated = validate_intent(intent)
        assert validated["venue"] == "kraken"

    def test_leverage_must_be_positive_int(self):
        with pytest.raises(ValueError, match="leverage must be a positive int"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken-perps",
                    "pair": "SOLUSD",
                    "side": "sell",
                    "order_type": "market",
                    "volume": 11.5,
                    "leverage": 0,
                    "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
                }
            )

    def test_leverage_must_be_int_not_float(self):
        with pytest.raises(ValueError, match="leverage must be a positive int"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken-perps",
                    "pair": "SOLUSD",
                    "side": "sell",
                    "order_type": "market",
                    "volume": 11.5,
                    "leverage": 2.5,
                    "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
                }
            )

    def test_bracket_requires_stop_loss(self):
        with pytest.raises(ValueError, match="bracket.stop_loss"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken-perps",
                    "pair": "SOLUSD",
                    "side": "sell",
                    "order_type": "market",
                    "volume": 11.5,
                    "leverage": 2,
                    "bracket": {"take_profit": 58.07},
                }
            )

    def test_bracket_requires_take_profit(self):
        with pytest.raises(ValueError, match="bracket.take_profit"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken-perps",
                    "pair": "SOLUSD",
                    "side": "sell",
                    "order_type": "market",
                    "volume": 11.5,
                    "leverage": 2,
                    "bracket": {"stop_loss": 76.66},
                }
            )


# ─────────────────────────────────────────────────────────── Registry


class TestExecutionRegistry:
    def test_kraken_perps_is_registered_on_import(self):
        venues = registered_venues()
        assert "kraken-perps" in venues

    def test_get_known_provider(self):
        p = get_execution_provider("kraken-perps")
        assert isinstance(p, ExecutionProvider)
        assert p.name == "kraken-perps"


# ─────────────────────────────────────────────────────────── Symbol mapping


class TestSymbolMapping:
    def test_spot_pair_to_futures(self):
        assert resolve_futures_symbol("SOLUSD") == "PF_SOLUSD"
        assert resolve_futures_symbol("BTCUSD") == "PF_XBTUSD"
        assert resolve_futures_symbol("ETHUSD") == "PF_ETHUSD"

    def test_case_insensitive(self):
        assert resolve_futures_symbol("solusd") == "PF_SOLUSD"

    def test_unmapped_pair_raises(self):
        with pytest.raises(ValueError, match="no Kraken futures symbol"):
            resolve_futures_symbol("DOGEUSD")

    def test_supports_pair(self):
        provider = KrakenPerpsExecutionProvider()
        assert provider.supports("SOLUSD") is True
        assert provider.supports("DOGEUSD") is False
        assert provider.supports("SOLUSD", venue="kraken-perps") is True
        assert provider.supports("SOLUSD", venue="kraken") is False


class TestLeverageCap:
    def test_majors_capped_at_2x(self):
        assert leverage_cap_for_pair("BTCUSD") == 2
        assert leverage_cap_for_pair("ETHUSD") == 2
        assert leverage_cap_for_pair("SOLUSD") == 2

    def test_alts_default_to_5x(self):
        assert leverage_cap_for_pair("<PRIVATE_PERP>USD") == DEFAULT_LEVERAGE_CAP
        assert leverage_cap_for_pair("NEARUSD") == DEFAULT_LEVERAGE_CAP

    def test_leverage_caps_table(self):
        assert LEVERAGE_CAPS["BTCUSD"] == 2
        assert LEVERAGE_CAPS["ETHUSD"] == 2
        assert LEVERAGE_CAPS["SOLUSD"] == 2


# ──────────────────────────────────────────────────────── Kraken provider


def _make_completed(stdout="", stderr="", returncode=0):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def _kraken_resp(payload):
    return _make_completed(stdout=json.dumps(payload))


class TestKrakenPerpsPlaceOrder:
    def _make_short_intent(self, **overrides) -> Intent:
        intent: dict = {
            "intent_id": "test-001",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        intent.update(overrides)
        return validate_intent(intent)

    def _captured_runner(self, responses: list[dict]):
        """Return a runner that pops responses in order."""
        responses = list(responses)

        def runner(cmd, *args, **kwargs):
            if not responses:
                raise AssertionError(f"too many subprocess calls: {cmd}")
            return _kraken_resp(responses.pop(0))

        return runner, responses

    def test_short_bracket_full_lifecycle(self):
        """Happy path: set-leverage + open sell + stop buy + tp buy → filled."""
        set_lev = {"result": "success"}
        open_resp = {"result": "success", "order_id": "OFILL-OPEN-001", "sendStatus": {"order_id": "OFILL-OPEN-001"}}
        stop_resp = {"result": "success", "order_id": "OFILL-STOP-001"}
        tp_resp = {"result": "success", "order_id": "OFILL-TP-001"}
        runner, remaining = self._captured_runner([set_lev, open_resp, stop_resp, tp_resp])

        with patch("subprocess.run", side_effect=runner):
            provider = get_execution_provider("kraken-perps")
            fill = provider.place_order(self._make_short_intent(), wait=False, timeout_s=2.0)

        assert fill["status"] == "submitted"
        assert fill["order_id"] == "OFILL-OPEN-001"
        assert fill["bracket"]["open_order_id"] == "OFILL-OPEN-001"
        assert fill["bracket"]["stop_order_id"] == "OFILL-STOP-001"
        assert fill["bracket"]["take_profit_order_id"] == "OFILL-TP-001"
        assert fill["side"] == "sell"
        assert fill["venue"] == "kraken-perps"
        assert remaining == []

    def test_long_bracket_uses_buy_side(self):
        """Long intent: open_buy, stop_sell, tp_sell."""
        intent = self._make_short_intent(side="buy")
        runner, _ = self._captured_runner(
            [
                {"result": "success"},
                {"result": "success", "order_id": "OPEN-X"},
                {"result": "success", "order_id": "STOP-X"},
                {"result": "success", "order_id": "TP-X"},
            ]
        )
        captured: list[list[str]] = []

        def capturing_runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return runner(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=capturing_runner):
            provider = get_execution_provider("kraken-perps")
            provider.place_order(intent, wait=False)

        # Open: kraken futures order buy PF_SOLUSD 11.5 --type market
        open_cmd = next(c for c in captured if "order" in c and "buy" in c and "market" in c)
        assert "PF_SOLUSD" in open_cmd
        assert "buy" in open_cmd
        # Stop: sell + type stop
        stop_cmd = next(c for c in captured if "order" in c and "sell" in c and "stop" in c)
        assert "stop" in stop_cmd
        assert "--reduce-only" in stop_cmd
        # TP: sell + type take-profit
        tp_cmd = next(c for c in captured if "take-profit" in c)
        assert "sell" in tp_cmd
        assert "--reduce-only" in tp_cmd
        assert "58.07" in tp_cmd

    def test_client_order_id_passed_through(self):
        runner, _ = self._captured_runner(
            [
                {"result": "success"},
                {"result": "success", "order_id": "OID-OPEN"},
                {"result": "success", "order_id": "OID-STOP"},
                {"result": "success", "order_id": "OID-TP"},
            ]
        )
        captured: list[list[str]] = []

        def capturing_runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return runner(cmd, *args, **kwargs)

        intent = self._make_short_intent(intent_id="perps-uuid-abc")
        with patch("subprocess.run", side_effect=capturing_runner):
            provider = get_execution_provider("kraken-perps")
            provider.place_order(intent, wait=False)

        open_cmd = next(c for c in captured if "order" in c and "market" in c)
        assert "--client-order-id" in open_cmd
        idx = open_cmd.index("--client-order-id")
        assert open_cmd[idx + 1] == "perps-uuid-abc"

    def test_missing_leverage_rejected_before_subprocess(self):
        # Build an Intent without leverage — needs to bypass validate_intent
        # since that's the gate. The provider's own check is the second
        # line of defence.
        bad: dict = {
            "intent_id": "x",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        # validate_intent allows None leverage — provider should still refuse.
        intent = validate_intent(bad)
        with patch("subprocess.run") as mock_run:
            provider = get_execution_provider("kraken-perps")
            fill = provider.place_order(intent, wait=False)
        assert fill["status"] == "error"
        assert "leverage required" in fill["reason"]
        mock_run.assert_not_called()

    def test_unmapped_pair_rejected(self):
        bad = {
            "intent_id": "x",
            "venue": "kraken-perps",
            "pair": "DOGEUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 1,
            "leverage": 2,
            "bracket": {"stop_loss": 0.5, "take_profit": 0.3},
        }
        with patch("subprocess.run") as mock_run:
            provider = get_execution_provider("kraken-perps")
            fill = provider.place_order(validate_intent(bad), wait=False)
        assert fill["status"] == "error"
        assert "no Kraken futures symbol" in fill["reason"]
        mock_run.assert_not_called()

    def test_stop_failure_rolls_back_position(self):
        """Stop fails → position auto-closed → status=error with rollback reason."""
        runner, _ = self._captured_runner(
            [
                {"result": "success"},  # set-leverage
                {"result": "success", "order_id": "OFILL-OPEN"},  # open OK
                {"error": ["EOrder:insufficient margin"]},  # stop fails
                {"result": "success", "order_id": "OFILL-CLOSE"},  # close
            ]
        )
        with patch("subprocess.run", side_effect=runner):
            provider = get_execution_provider("kraken-perps")
            fill = provider.place_order(self._make_short_intent(), wait=False)

        assert fill["status"] == "error"
        assert "auto-closed" in fill["reason"]
        assert fill["order_id"] == "OFILL-OPEN"
        assert fill["bracket"]["open_order_id"] == "OFILL-OPEN"
        assert fill["bracket"].get("stop_order_id", "") == ""

    def test_tp_failure_keeps_stop_protection(self):
        """TP fails after stop succeeded → status=submitted with reason warning."""
        runner, _ = self._captured_runner(
            [
                {"result": "success"},
                {"result": "success", "order_id": "OFILL-OPEN"},
                {"result": "success", "order_id": "OFILL-STOP"},
                {"error": ["EOrder:TP failed"]},
            ]
        )
        with patch("subprocess.run", side_effect=runner):
            provider = get_execution_provider("kraken-perps")
            fill = provider.place_order(self._make_short_intent(), wait=False)

        assert fill["status"] == "submitted"
        assert fill["bracket"]["stop_order_id"] == "OFILL-STOP"
        assert fill["bracket"]["take_profit_order_id"] == ""
        assert fill["reason"] and "TP placement failed" in fill["reason"]

    def test_open_failure_returns_error_immediately(self):
        runner, _ = self._captured_runner(
            [
                {"result": "success"},  # set-leverage
                {"error": ["EOrder:Insufficient funds"]},  # open fails
            ]
        )
        with patch("subprocess.run", side_effect=runner):
            provider = get_execution_provider("kraken-perps")
            fill = provider.place_order(self._make_short_intent(), wait=False)

        assert fill["status"] == "error"
        assert "Insufficient funds" in fill["reason"]
        assert fill["bracket"] is None


# ───────────────────────────────────────────────────────── Read / manage


class TestKrakenPerpsReadOps:
    def test_get_balance_parses_accounts_envelope(self):
        payload = {
            "accounts": [
                {"currency": "USD", "availableMargin": "1234.56"},
                {"currency": "BTC", "balance": "0.5"},
            ]
        }
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken-perps")
            balances = provider.get_balance()
        assert balances["USD"] == pytest.approx(1234.56)
        assert balances["BTC"] == pytest.approx(0.5)

    def test_get_balance_handles_wrapped_envelope(self):
        payload = {"result": {"accounts": [{"currency": "EUR", "balanceValue": 500}]}}
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken-perps")
            assert provider.get_balance()["EUR"] == pytest.approx(500)

    def test_get_open_orders_parses_envelope(self):
        payload = {
            "openOrders": [
                {
                    "order_id": "OFILL-1",
                    "symbol": "PF_SOLUSD",
                    "side": "l",  # Kraken sometimes encodes as "l"
                    "orderType": "stop",
                    "size": "11.5",
                    "filled": "0",
                    "stopPrice": "76.66",
                }
            ]
        }
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken-perps")
            orders = provider.get_open_orders()
        assert len(orders) == 1
        assert orders[0]["order_id"] == "OFILL-1"
        assert orders[0]["side"] == "buy"  # "l" -> "buy"
        assert orders[0]["limit_price"] == pytest.approx(76.66)

    def test_get_positions_filters_zero_size(self):
        payload = [
            {"symbol": "PF_SOLUSD", "size": -3.0},
            {"symbol": "PF_XBTUSD", "size": 0.1},
            {"symbol": "PF_DOGEUSD", "size": 0},  # filtered
        ]
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken-perps")
            positions = provider.get_positions()
        syms = [p["symbol"] for p in positions]
        assert "PF_SOLUSD" in syms
        assert "PF_XBTUSD" in syms
        assert "PF_DOGEUSD" not in syms
        # Sign → side mapping.
        assert next(p for p in positions if p["symbol"] == "PF_SOLUSD")["side"] == "short"
        assert next(p for p in positions if p["symbol"] == "PF_XBTUSD")["side"] == "long"

    def test_cancel_order_success(self):
        with patch("subprocess.run", return_value=_kraken_resp({"result": "success"})):
            provider = get_execution_provider("kraken-perps")
            assert provider.cancel_order("OFILL-1") is True

    def test_cancel_order_error_returns_false(self):
        with patch("subprocess.run", return_value=_kraken_resp({"error": ["EOrder:Unknown order"]})):
            provider = get_execution_provider("kraken-perps")
            assert provider.cancel_order("OFILL-1") is False

    def test_cancel_order_cli_failure_returns_false(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            provider = get_execution_provider("kraken-perps")
            assert provider.cancel_order("OFILL-1") is False


# ───────────────────────────────────────────────────── Perps risk policies


from analysis.risk import (  # noqa: E402
    DEFAULT_POLICIES,
    PERPS_POLICIES,
    RiskContext,
    duplicate_perps_position_policy,
    funding_drag_policy,
    is_perps_intent,
    leverage_cap_policy,
    liquidation_distance_policy,
    select_policies,
    stop_distance_policy,
    vet,
)


def _empty_ctx(**overrides) -> RiskContext:
    ctx = RiskContext()
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestPerpsRiskPolicies:
    def _short_sol_intent(self, **overrides) -> Intent:
        intent: dict = {
            "intent_id": "x",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
            "extras": {"reference_entry": 69.22, "position_value": 800.0},
        }
        intent.update(overrides)
        return validate_intent(intent)

    # ---- leverage cap ----

    def test_leverage_cap_spot_intent_is_noop(self):
        intent = validate_intent(
            {
                "intent_id": "x",
                "venue": "kraken",
                "pair": "BTCUSD",
                "side": "buy",
                "order_type": "limit",
                "volume": 0.01,
                "limit_price": 65000,
            }
        )
        frag = leverage_cap_policy(intent, _empty_ctx())
        assert frag["status"] == "APPROVED"

    def test_leverage_cap_majors_2x_rejects_3x(self):
        intent = self._short_sol_intent(leverage=3)
        frag = leverage_cap_policy(intent, _empty_ctx())
        assert frag["status"] == "REJECT"
        assert "2x cap" in frag["reason"]

    def test_leverage_cap_alts_5x_passes(self):
        intent = validate_intent(
            {
                "intent_id": "x",
                "venue": "kraken-perps",
                "pair": "<PRIVATE_PERP>USD",
                "side": "sell",
                "order_type": "market",
                "volume": 100,
                "leverage": 5,
                "bracket": {"stop_loss": 60, "take_profit": 40},
            }
        )
        assert leverage_cap_policy(intent, _empty_ctx())["status"] == "APPROVED"

    # ---- liquidation distance ----

    def test_liquidation_distance_short_passes_with_2x(self):
        # short entry 69.22, MM 0.01, lev 2 → move_to_liq = 1/2 + 0.01 = 0.51.
        # liq at entry * 1.51 = 104.52 → distance ≈ 51%, well above the 30% floor.
        intent = self._short_sol_intent()
        frag = liquidation_distance_policy(intent, _empty_ctx(maintenance_margin_rate=0.01))
        assert frag["status"] == "APPROVED"

    def test_liquidation_distance_short_lev_10_rejects(self):
        # move_to_liq = 0.1 + 0.01 = 0.11 → distance 11%, below 30% floor.
        intent = self._short_sol_intent(leverage=10)
        frag = liquidation_distance_policy(intent, _empty_ctx(maintenance_margin_rate=0.01))
        assert frag["status"] == "REJECT"
        assert "below the 30% floor" in frag["reason"]
        assert "suggested_leverage" in frag["detail"]

    def test_liquidation_distance_long_lev_10_rejects(self):
        intent = validate_intent(
            {
                "intent_id": "x",
                "venue": "kraken-perps",
                "pair": "BTCUSD",
                "side": "buy",
                "order_type": "market",
                "volume": 0.01,
                "leverage": 10,
                "bracket": {"stop_loss": 60000, "take_profit": 75000},
                "extras": {"reference_entry": 65000},
            }
        )
        frag = liquidation_distance_policy(intent, _empty_ctx(maintenance_margin_rate=0.01))
        assert frag["status"] == "REJECT"

    def test_liquidation_distance_unmapped_pair_is_concern(self):
        # SOLUSD has a static MM_RATES entry (0.01), so the policy resolves
        # the rate and doesn't return CONCERN. Use an unmapped pair instead.
        intent = Intent(
            intent_id="test",
            venue="kraken-perps",
            pair="XYZUSD",
            side="sell",
            order_type="market",
            volume=1.0,
            leverage=2,
            bracket={"stop_loss": 90.0, "take_profit": 50.0},
            extras={"reference_entry": 100.0},
        )
        frag = liquidation_distance_policy(intent, _empty_ctx(maintenance_margin_rate=None))
        assert frag["status"] == "CONCERN"
        assert "not loaded" in frag["reason"]

    def test_liquidation_distance_solusd_uses_static_mm_rates(self):
        # SOLUSD is in MM_RATES (0.01). Even without ctx.maintenance_margin_rate
        # set, the policy resolves via the static lookup and evaluates the
        # distance check (not the "no info" CONCERN).
        frag = liquidation_distance_policy(self._short_sol_intent(), _empty_ctx())
        assert frag["status"] in ("APPROVED", "REJECT")
        assert "not loaded" not in frag["reason"]

    # ---- stop distance ----

    def test_stop_distance_within_bucket_passes(self):
        intent = self._short_sol_intent()  # stop 76.66, entry 69.22 → 10.75%
        frag = stop_distance_policy(intent, _empty_ctx())
        assert frag["status"] == "APPROVED"

    def test_stop_distance_too_tight_rejects(self):
        intent = self._short_sol_intent()
        intent["bracket"]["stop_loss"] = 69.8  # 0.84% above entry
        frag = stop_distance_policy(intent, _empty_ctx())
        assert frag["status"] == "REJECT"
        assert "noise risk" in frag["reason"]

    def test_stop_distance_too_wide_rejects(self):
        intent = self._short_sol_intent()
        intent["bracket"]["stop_loss"] = 100.0  # 44.5% above entry
        frag = stop_distance_policy(intent, _empty_ctx())
        assert frag["status"] == "REJECT"
        assert "too wide" in frag["reason"]

    # ---- funding drag ----

    def test_funding_drag_high_rate_concern(self):
        # 0.0016/8h × 9 charges = 1.44%, above 1% floor.
        frag = funding_drag_policy(self._short_sol_intent(), _empty_ctx(funding_rate_per_8h=0.0016))
        assert frag["status"] == "CONCERN"
        assert "exceeds 1%" in frag["reason"]
        assert frag["detail"]["charges"] == 9

    def test_funding_drag_low_rate_passes(self):
        frag = funding_drag_policy(self._short_sol_intent(), _empty_ctx(funding_rate_per_8h=0.0001))
        assert frag["status"] == "APPROVED"

    def test_funding_drag_no_rate_is_concern(self):
        frag = funding_drag_policy(self._short_sol_intent(), _empty_ctx(funding_rate_per_8h=None))
        assert frag["status"] == "CONCERN"
        assert "not loaded" in frag["reason"]

    # ---- duplicate position ----

    def test_duplicate_short_position_rejects(self):
        intent = self._short_sol_intent()
        ctx = _empty_ctx(
            open_perps_positions=[{"symbol": "PF_SOLUSD", "size": -3.0}],
        )
        frag = duplicate_perps_position_policy(intent, ctx)
        assert frag["status"] == "REJECT"
        assert "already on SOLUSD" in frag["reason"]

    def test_duplicate_long_position_rejects(self):
        intent = self._short_sol_intent(side="buy")
        ctx = _empty_ctx(open_perps_positions=[{"symbol": "PF_SOLUSD", "size": 1.5}])
        frag = duplicate_perps_position_policy(intent, ctx)
        assert frag["status"] == "REJECT"

    def test_opposite_direction_position_passes(self):
        # short SOL intent + existing long SOL position → not a duplicate.
        intent = self._short_sol_intent(side="sell")
        ctx = _empty_ctx(open_perps_positions=[{"symbol": "PF_SOLUSD", "size": 5.0}])
        frag = duplicate_perps_position_policy(intent, ctx)
        assert frag["status"] == "APPROVED"

    def test_no_open_positions_passes(self):
        intent = self._short_sol_intent()
        frag = duplicate_perps_position_policy(intent, _empty_ctx(open_perps_positions=[]))
        assert frag["status"] == "APPROVED"

    def test_spot_intent_skips_duplicate_check(self):
        intent = validate_intent(
            {
                "intent_id": "x",
                "venue": "kraken",
                "pair": "SOLUSD",
                "side": "buy",
                "order_type": "limit",
                "volume": 1,
                "limit_price": 70,
            }
        )
        ctx = _empty_ctx(open_perps_positions=[{"symbol": "PF_SOLUSD", "size": 1}])
        frag = duplicate_perps_position_policy(intent, ctx)
        assert frag["status"] == "APPROVED"


# ─────────────────────────────────────────────────────────── Policy selection


class TestPolicySelection:
    def test_is_perps_by_venue(self):
        assert is_perps_intent({"venue": "kraken-perps"})
        assert is_perps_intent({"venue": "hl-perps"})
        assert not is_perps_intent({"venue": "kraken"})

    def test_is_perps_by_leverage_field(self):
        assert is_perps_intent({"venue": "kraken", "leverage": 2})
        assert not is_perps_intent({"venue": "kraken"})

    def test_select_policies_includes_perps_for_perps_intent(self):
        intent = {
            "venue": "kraken-perps",
            "intent_id": "x",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        chosen = select_policies(intent)
        assert len(chosen) == len(DEFAULT_POLICIES) + len(PERPS_POLICIES)
        # Spot policies are still in the set.
        assert any(p is leverage_cap_policy for p in chosen)
        assert any(p is duplicate_perps_position_policy for p in chosen)

    def test_select_policies_excludes_perps_for_spot_intent(self):
        intent = {
            "venue": "kraken",
            "intent_id": "x",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "limit",
            "volume": 0.01,
            "limit_price": 65000,
        }
        chosen = select_policies(intent)
        assert len(chosen) == len(DEFAULT_POLICIES)
        assert all(p not in PERPS_POLICIES for p in chosen)

    def test_explicit_policies_arg_wins(self):
        intent = {
            "venue": "kraken-perps",
            "intent_id": "x",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        chosen = select_policies(intent, policies=[leverage_cap_policy])
        assert chosen == [leverage_cap_policy]


class TestVetAutoSelectsPerps:
    def test_vet_runs_perps_policies_for_perps_intent(self):
        intent = {
            "intent_id": "x",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 3,  # exceeds 2x major cap
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
            "extras": {"reference_entry": 69.22, "position_value": 800.0},
        }
        ctx = _empty_ctx()
        verdict = vet(intent, ctx)
        assert verdict["status"] == "REJECT"
        policy_names = {f["policy"] for f in verdict["fragments"]}
        assert "leverage_cap" in policy_names

    def test_vet_runs_only_spot_policies_for_spot_intent(self):
        intent = {
            "intent_id": "x",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "limit",
            "volume": 0.01,
            "limit_price": 65000,
        }
        ctx = _empty_ctx()
        verdict = vet(intent, ctx)
        policy_names = {f["policy"] for f in verdict["fragments"]}
        assert "leverage_cap" not in policy_names
        assert "duplicate_perps_position" not in policy_names


# ─────────────────────────────────────── skills/execution-kraken-perps/lib.py


_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")
_LIB_PATH = os.path.join(_SKILLS_DIR, "execution-kraken-perps", "lib.py")


def _load_lib():
    spec = importlib.util.spec_from_file_location("execution_kraken_perps_lib_under_test", _LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLibIntentLoading:
    def test_load_intent_file_happy(self, tmp_path):
        load_intent_file = _load_lib().load_intent_file

        p = tmp_path / "intent.json"
        p.write_text(
            json.dumps(
                {
                    "intent_id": "test-1",
                    "venue": "kraken-perps",
                    "pair": "SOLUSD",
                    "side": "sell",
                    "order_type": "market",
                    "volume": 11.5,
                    "leverage": 2,
                    "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
                }
            )
        )
        intent = load_intent_file(str(p))
        assert intent["intent_id"] == "test-1"
        assert intent["leverage"] == 2

    def test_load_intent_file_missing(self, tmp_path):
        load_intent_file = _load_lib().load_intent_file

        with pytest.raises(ValueError, match="not found"):
            load_intent_file(str(tmp_path / "does-not-exist.json"))

    def test_load_intent_file_malformed_json(self, tmp_path):
        load_intent_file = _load_lib().load_intent_file

        p = tmp_path / "bad.json"
        p.write_text("{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_intent_file(str(p))

    def test_intent_from_direct_args(self):
        intent_from_direct_args = _load_lib().intent_from_direct_args

        args = {
            "pair": "SOLUSD",
            "side": "sell",
            "volume": 11.5,
            "leverage": 2,
            "stop_loss": 76.66,
            "take_profit": 58.07,
        }
        intent = intent_from_direct_args(args, intent_id="direct-1")
        assert intent["intent_id"] == "direct-1"
        assert intent["venue"] == "kraken-perps"
        assert intent["bracket"]["stop_loss"] == 76.66

    def test_intent_from_direct_args_with_extras(self):
        intent_from_direct_args = _load_lib().intent_from_direct_args

        args = {
            "pair": "BTCUSD",
            "side": "buy",
            "volume": 0.01,
            "leverage": 2,
            "stop_loss": 60000,
            "take_profit": 75000,
            "position_value": 1300.0,
            "reference_entry": 65000.0,
        }
        intent = intent_from_direct_args(args, intent_id="x")
        assert intent["extras"]["position_value"] == 1300.0
        assert intent["extras"]["reference_entry"] == 65000.0

    def test_intent_from_direct_args_missing_required(self):
        intent_from_direct_args = _load_lib().intent_from_direct_args

        with pytest.raises(ValueError, match="missing required args"):
            intent_from_direct_args({"pair": "BTCUSD"}, intent_id="x")

    def test_intent_from_direct_args_invalid_side(self):
        intent_from_direct_args = _load_lib().intent_from_direct_args

        with pytest.raises(ValueError, match="side must be"):
            intent_from_direct_args(
                {
                    "pair": "BTCUSD",
                    "side": "long",
                    "volume": 0.01,
                    "leverage": 2,
                    "stop_loss": 60000,
                    "take_profit": 75000,
                },
                intent_id="x",
            )


class TestLibRenderers:
    def test_render_intent_summary_includes_bracket(self):
        render_intent_summary = _load_lib().render_intent_summary

        intent: Intent = {
            "intent_id": "abc-123",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
            "extras": {"position_value": 800.0, "reference_entry": 69.22},
            "thesis": "Breakdown retest",
            "strategy": "trend-follow",
            "conviction": 4,
        }
        out = render_intent_summary(intent)
        assert "SOLUSD" in out
        assert "SELL" in out
        assert "2x" in out
        assert "76.6600" in out
        assert "58.0700" in out
        assert "trend-follow" in out

    def test_render_confirmation_with_bracket(self):
        render_confirmation = _load_lib().render_confirmation

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OFILL-OPEN-001",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 11.5,
            "fill_price": 69.22,
            "cost_quote": 796.03,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
            "bracket": {
                "open_order_id": "OFILL-OPEN-001",
                "stop_order_id": "OFILL-STOP-001",
                "take_profit_order_id": "OFILL-TP-001",
            },
        }
        out = render_confirmation(conf)
        assert "OFILL-OPEN-001" in out
        assert "OFILL-STOP-001" in out
        assert "OFILL-TP-001" in out
        assert "FILLED" in out


class TestLibPortfolioWiring:
    def _setup_db(self, tmp_path):
        from portfolio.db import add_portfolio, init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        pid = add_portfolio(db_path, "perps", base_ccy="USD")
        return db_path, pid

    def test_write_short_fill_creates_sell_row(self, tmp_path):
        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OFILL-OPEN-001",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 11.5,
            "fill_price": 69.22,
            "cost_quote": 796.03,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
            "bracket": {
                "open_order_id": "OFILL-OPEN-001",
                "stop_order_id": "OFILL-STOP-001",
                "take_profit_order_id": "OFILL-TP-001",
            },
        }
        intent: Intent = {
            "intent_id": "abc",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
            "strategy": "trend-follow",
            "thesis": "Breakdown retest",
        }
        tx_id = write_fill_to_portfolio(
            conf,
            portfolio_id=pid,
            db_path=db_path,
            intent=intent,
        )
        assert tx_id > 0

        from portfolio.db import list_transactions

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1
        row = rows[0]
        assert row["side"] == "SELL"
        assert row["asset"] == "kraken:SOLUSD"
        assert row["qty"] == pytest.approx(11.5)
        assert row["price"] == pytest.approx(69.22)
        assert row["tx_hash"] == "OFILL-OPEN-001"
        assert row["source"] == "execution-kraken-perps"

        notes = json.loads(row["notes"])
        assert notes["venue"] == "kraken-perps"
        assert notes["bracket"]["open_order_id"] == "OFILL-OPEN-001"
        assert notes["stop_loss"] == 76.66
        assert notes["take_profit"] == 58.07
        assert notes["leverage"] == 2
        assert notes["strategy"] == "trend-follow"
        assert notes["thesis"] == "Breakdown retest"
        assert notes["intent_id"] == "abc"
        # decision_context direction is canonical (not raw side)
        assert notes["decision_context"]["l3_idea"]["direction"] == "short"

    def test_write_long_fill_creates_buy_row(self, tmp_path):
        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OFILL-LONG",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
            "bracket": {
                "open_order_id": "OFILL-LONG",
                "stop_order_id": "OFILL-STOP",
                "take_profit_order_id": "OFILL-TP",
            },
        }
        intent: Intent = {
            "intent_id": "abc",
            "venue": "kraken-perps",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "leverage": 2,
            "bracket": {"stop_loss": 60000, "take_profit": 75000},
        }
        write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)
        from portfolio.db import list_transactions

        rows = list_transactions(db_path, portfolio_id=pid)
        assert rows[0]["side"] == "BUY"
        assert rows[0]["asset"] == "kraken:BTCUSD"
        # decision_context direction is canonical (not raw side)
        import json

        notes = json.loads(rows[0]["notes"])
        assert notes["decision_context"]["l3_idea"]["direction"] == "long"

    def test_write_fill_rejects_non_positive_status(self, tmp_path):
        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "X",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 0.0,
            "status": "rejected",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
        }
        with pytest.raises(ValueError, match="non-positive fill"):
            write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path)

    def test_write_fill_rejects_zero_volume(self, tmp_path):
        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "X",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 0.0,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
        }
        with pytest.raises(ValueError, match="zero-volume fill"):
            write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path)

    def test_write_fill_retry_same_intent_id_keeps_first_decision(self, tmp_path):
        """Retry path: same intent_id, add_decision is a no-op, original
        decision_context is preserved (first call wins)."""
        from portfolio.db import get_decision, list_transactions

        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "retry-1",
            "order_id": "OFILL-1",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 11.5,
            "fill_price": 69.22,
            "cost_quote": 796.03,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
            "bracket": {
                "open_order_id": "OFILL-1",
                "stop_order_id": "OFILL-STOP-1",
                "take_profit_order_id": "OFILL-TP-1",
            },
        }
        intent: Intent = {
            "intent_id": "retry-1",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
            "strategy": "trend-follow",
            "thesis": "Breakdown retest",
        }
        first_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)
        first_decision = get_decision(db_path, "retry-1")
        assert first_decision is not None
        first_dc = first_decision["decision_context_json"]

        # Retry — same intent_id, must not raise on the decisions write.
        second_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)
        assert second_id != first_id
        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 2
        retry_decision = get_decision(db_path, "retry-1")
        assert retry_decision["decision_context_json"] == first_dc

    def test_write_fill_merges_decision_decoration(self, tmp_path):
        """decision_decoration from the Intent is merged into the
        auto-built DecisionContext and written to the decisions table."""
        from portfolio.db import get_decision

        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "decor-1",
            "order_id": "OFILL-D",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 11.5,
            "fill_price": 69.22,
            "cost_quote": 796.03,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
            "bracket": {
                "open_order_id": "OFILL-D",
                "stop_order_id": "OFILL-STOP-D",
                "take_profit_order_id": "OFILL-TP-D",
            },
        }
        intent: Intent = {
            "intent_id": "decor-1",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
            "strategy": "trend-follow",
            "thesis": "Breakdown retest",
            "decision_decoration": {
                "regime_label": "RISK_OFF",
                "regime_fng": 22.0,
                "regime_btc_dominance": 52.1,
                "macro_signals": ["fear_panic", "btc_below_ema21"],
                "risk_status": "CONCERN",
                "risk_position_size_pct": 8.0,
                "risk_concerns": ["high regime risk"],
                "override_from_suggestion": False,
            },
        }
        write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)

        decision = get_decision(db_path, "decor-1")
        assert decision is not None
        dc = json.loads(decision["decision_context_json"])
        assert dc["regime"]["label"] == "RISK_OFF"
        assert dc["regime"]["fng"] == 22.0
        assert dc["regime"]["btc_dominance"] == 52.1
        assert dc["macro_signals"] == ["fear_panic", "btc_below_ema21"]
        assert dc["risk_verdict"]["status"] == "CONCERN"
        assert dc["risk_verdict"]["position_size_pct"] == 8.0
        assert dc["risk_verdict"]["concerns"] == ["high regime risk"]
        assert dc["override"]["from_suggestion"] is False

    def test_write_fill_decoration_absent_yields_builder_defaults(self, tmp_path):
        """No decision_decoration on the Intent: regime/risk/override
        fields stay at builder defaults (None / [] / False)."""
        from portfolio.db import get_decision

        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "no-decor-1",
            "order_id": "OFILL-ND",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 11.5,
            "fill_price": 69.22,
            "cost_quote": 796.03,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
        }
        intent: Intent = {
            "intent_id": "no-decor-1",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)

        decision = get_decision(db_path, "no-decor-1")
        dc = json.loads(decision["decision_context_json"])
        assert dc["regime"]["label"] is None
        assert dc["regime"]["fng"] is None
        assert dc["macro_signals"] == []
        assert dc["risk_verdict"]["status"] is None
        assert dc["risk_verdict"]["concerns"] == []
        assert dc["override"]["from_suggestion"] is False

    def test_write_fill_invalid_side_raises(self, tmp_path):
        """Empty / unknown side in the FillConfirmation now raises
        rather than silently being recorded as a short trade."""
        db_path, pid = self._setup_db(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "bad-side-1",
            "order_id": "X",
            "pair": "SOLUSD",
            "side": "",  # direction_from_side() raises on empty
            "order_type": "market",
            "requested_volume": 11.5,
            "filled_volume": 11.5,
            "fill_price": 69.22,
            "cost_quote": 796.03,
            "status": "filled",
            "timestamp": "2026-06-22T15:00:00+00:00",
            "venue": "kraken-perps",
        }
        intent: Intent = {
            "intent_id": "bad-side-1",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "sell",
            "order_type": "market",
            "volume": 11.5,
            "leverage": 2,
            "bracket": {"stop_loss": 76.66, "take_profit": 58.07},
        }
        with pytest.raises(ValueError, match="side must be"):
            write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)


# ──────────────────────────────────────────────────── _vet_afk_gate


class TestVetAfkGateIntentMutation:
    """Regression: _vet_afk_gate must not mutate the caller's intent.

    Bug: the previous implementation popped ``intent["limit_price"]`` in
    the finally block whenever the (synthetic or real) value equalled the
    pre-computed ``limit_price`` and ``extras.position_value`` was set.
    With a real limit_price that also happened to be the synthetic
    default (1.0) or any other match, the field was silently deleted —
    bad today (the user-visible "Limit price" line in the confirm prompt
    disappears) and a real order-submission bug the moment perps supports
    a limit-order open.
    """

    def _load_run_module(self):
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-perps",
            "scripts",
            "run.py",
        )
        spec = importlib.util.spec_from_file_location("execution_kraken_perps_run_under_test_afk", run_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _intent_with_limit_price(self):
        return {
            "intent_id": "test-afk-1",
            "venue": "kraken-perps",
            "pair": "SOLUSD",
            "side": "buy",
            "order_type": "limit",
            "volume": 11.5,
            "limit_price": 60.0,
            "extras": {"position_value": 800.0},
        }

    def test_limit_price_preserved_when_already_set(self):
        """A real limit_price must survive _vet_afk_gate unchanged."""
        mod = self._load_run_module()
        with patch(
            "analysis.risk.afk.vet_afk",
            return_value={"gate": "passed", "status": "APPROVED", "reason": "no objection", "detail": {}},
        ):
            intent = self._intent_with_limit_price()
            mod._vet_afk_gate(intent)
        assert intent["limit_price"] == 60.0

    def test_volume_restored_after_gate(self):
        """volume is temporarily overridden with position_value; must be restored."""
        mod = self._load_run_module()
        with patch(
            "analysis.risk.afk.vet_afk",
            return_value={"gate": "passed", "status": "APPROVED", "reason": "no objection", "detail": {}},
        ):
            intent = self._intent_with_limit_price()
            mod._vet_afk_gate(intent)
        assert intent["volume"] == 11.5

    def test_synthetic_limit_price_removed_when_original_absent(self):
        """When the caller didn't supply limit_price, the synthetic 1.0 must be popped."""
        mod = self._load_run_module()
        with patch(
            "analysis.risk.afk.vet_afk",
            return_value={"gate": "passed", "status": "APPROVED", "reason": "no objection", "detail": {}},
        ):
            intent = {
                "intent_id": "test-afk-2",
                "venue": "kraken-perps",
                "pair": "SOLUSD",
                "side": "sell",
                "order_type": "market",
                "volume": 11.5,
                "extras": {"position_value": 800.0},
            }
            assert "limit_price" not in intent
            mod._vet_afk_gate(intent)
        assert "limit_price" not in intent

    def test_limit_price_preserved_when_extras_absent(self):
        """No position_value branch: original limit_price (if any) untouched."""
        mod = self._load_run_module()
        with patch(
            "analysis.risk.afk.vet_afk",
            return_value={"gate": "passed", "status": "APPROVED", "reason": "no objection", "detail": {}},
        ):
            intent = {
                "intent_id": "test-afk-3",
                "venue": "kraken-perps",
                "pair": "SOLUSD",
                "side": "buy",
                "order_type": "limit",
                "volume": 11.5,
                "limit_price": 60.0,
            }
            mod._vet_afk_gate(intent)
        assert intent["limit_price"] == 60.0

    def test_gate_error_still_restores_intent(self):
        """Even on gate exception, the intent must be left as the caller passed it."""
        mod = self._load_run_module()
        with patch(
            "analysis.risk.afk.vet_afk",
            side_effect=ValueError("boom"),
        ):
            intent = self._intent_with_limit_price()
            mod._vet_afk_gate(intent)
        assert intent["limit_price"] == 60.0
        assert intent["volume"] == 11.5


# ──────────────────────────────────────────────────── CLI surface


class TestCLIArgparse:
    """Smoke tests for scripts/run.py argparse."""

    def _run_cli(self, *argv, monkeypatch):
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", "/tmp/test-execution-kraken-perps-portfolio.db")
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-perps",
            "scripts",
            "run.py",
        )
        spec = importlib.util.spec_from_file_location("execution_kraken_perps_run_under_test", run_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(sys, "argv", ["run.py", *argv]):
            return mod.main()

    def test_no_subcommand_shows_help(self, capsys, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._run_cli("--help", monkeypatch=monkeypatch)
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "submit" in captured.out
        assert "balance" in captured.out

    def test_balance_calls_provider(self, monkeypatch):
        with patch(
            "analysis.providers.execution.kraken_perps.KrakenPerpsExecutionProvider.get_balance",
            return_value={"USD": 1000.0},
        ) as mock_balance:
            rc = self._run_cli("balance", monkeypatch=monkeypatch)
        assert rc == 0
        mock_balance.assert_called_once()

    def test_positions_calls_provider(self, monkeypatch):
        with patch(
            "analysis.providers.execution.kraken_perps.KrakenPerpsExecutionProvider.get_positions",
            return_value=[{"symbol": "PF_SOLUSD", "size": -3.0, "side": "short"}],
        ) as mock_positions:
            rc = self._run_cli("positions", monkeypatch=monkeypatch)
        assert rc == 0
        mock_positions.assert_called_once()

    def test_cancel_calls_provider(self, monkeypatch):
        with patch(
            "analysis.providers.execution.kraken_perps.KrakenPerpsExecutionProvider.cancel_order",
            return_value=True,
        ) as mock_cancel:
            rc = self._run_cli("cancel", "OFILL-1", monkeypatch=monkeypatch)
        assert rc == 0
        mock_cancel.assert_called_once_with("OFILL-1")

    def test_submit_dry_run_does_not_invoke_provider(self, monkeypatch):
        rc = self._run_cli(
            "submit",
            "--pair",
            "SOLUSD",
            "--side",
            "sell",
            "--volume",
            "11.5",
            "--leverage",
            "2",
            "--stop-loss",
            "76.66",
            "--take-profit",
            "58.07",
            "--reference-entry",
            "69.22",
            "--position-value",
            "800",
            "--dry-run",
            monkeypatch=monkeypatch,
        )
        assert rc == 0

    def test_submit_leverage_cap_exceeded_returns_2(self, monkeypatch):
        # SOL is a major → cap 2x. Requesting 5x exits 2.
        rc = self._run_cli(
            "submit",
            "--pair",
            "SOLUSD",
            "--side",
            "sell",
            "--volume",
            "11.5",
            "--leverage",
            "5",
            "--stop-loss",
            "76.66",
            "--take-profit",
            "58.07",
            "--dry-run",
            monkeypatch=monkeypatch,
        )
        assert rc == 2

    def test_submit_missing_required_args_returns_2(self, monkeypatch):
        rc = self._run_cli(
            "submit",
            "--pair",
            "SOLUSD",
            "--side",
            "sell",
            "--volume",
            "11.5",
            "--dry-run",  # missing leverage / stop_loss / take_profit
            monkeypatch=monkeypatch,
        )
        assert rc == 2

    def test_submit_unknown_pair_returns_2(self, monkeypatch):
        rc = self._run_cli(
            "submit",
            "--pair",
            "DOGEUSD",
            "--side",
            "sell",
            "--volume",
            "1",
            "--leverage",
            "2",
            "--stop-loss",
            "0.5",
            "--take-profit",
            "0.3",
            "--dry-run",
            monkeypatch=monkeypatch,
        )
        assert rc == 2

    def test_parse_decoration_merges_json_and_flag(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-perps",
            "scripts",
            "run.py",
        )
        spec = importlib.util.spec_from_file_location("execution_kraken_perps_decor", run_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        raw = json.dumps(
            {
                "regime_label": "RISK_OFF",
                "risk_status": "CONCERN",
                "macro_signals": ["fear_panic"],
            }
        )
        out = mod._parse_decoration(raw, override_from_suggestion=True)
        assert out == {
            "regime_label": "RISK_OFF",
            "risk_status": "CONCERN",
            "macro_signals": ["fear_panic"],
            "override_from_suggestion": True,
        }

    def test_parse_decoration_returns_none_when_empty(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-perps",
            "scripts",
            "run.py",
        )
        spec = importlib.util.spec_from_file_location("execution_kraken_perps_decor2", run_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._parse_decoration(None, False) is None
        assert mod._parse_decoration("{}", False) is None
        assert mod._parse_decoration(None, True) == {"override_from_suggestion": True}

    def test_parse_decoration_invalid_json_raises(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-perps",
            "scripts",
            "run.py",
        )
        spec = importlib.util.spec_from_file_location("execution_kraken_perps_decor3", run_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with pytest.raises(ValueError, match="valid JSON"):
            mod._parse_decoration("not json", False)

    def test_parse_decoration_non_object_raises(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-perps",
            "scripts",
            "run.py",
        )
        spec = importlib.util.spec_from_file_location("execution_kraken_perps_decor4", run_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with pytest.raises(ValueError, match="JSON object"):
            mod._parse_decoration("[1, 2, 3]", False)
