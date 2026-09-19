"""Tests for execution-kraken-spot.

Covers:
  - validate_intent: required fields, enums, scaled/reject constraints
  - ExecutionProvider registry: get/register/resolve
  - KrakenExecutionProvider: place_order with mocked subprocess
    (submit, fill poll, error path, --cl-ord-id idempotency, get_balance,
     get_open_orders, cancel_order, supports)
  - execution-kraken-spot/lib.py: load_intent_file, intent_from_direct_args,
    render_intent_summary, render_confirmation, portfolio wiring
"""

import argparse
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# Make sure provider auto-registration runs.
from analysis.providers.execution import kraken_spot as _execution_kraken  # noqa: F401
from analysis.providers.execution.base import (
    ExecutionProvider,
    FillConfirmation,
    Intent,
    get_execution_provider,
    register_execution_provider,
    registered_venues,
    validate_intent,
)

# ───────────────────────────────────────────────────────────── validate_intent


class TestValidateIntent:
    def test_minimal_intent(self):
        intent = {
            "intent_id": "abc-123",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        result = validate_intent(intent)
        assert result["intent_id"] == "abc-123"

    def test_limit_requires_limit_price(self):
        with pytest.raises(ValueError, match="limit_price required"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "limit",
                    "volume": 0.01,
                }
            )

    def test_invalid_side(self):
        with pytest.raises(ValueError, match="side must be"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "long",
                    "order_type": "market",
                    "volume": 0.01,
                }
            )

    def test_invalid_order_type(self):
        with pytest.raises(ValueError, match="order_type must be"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "fill-or-kill",  # not in enum
                    "volume": 0.01,
                }
            )

    def test_negative_volume(self):
        with pytest.raises(ValueError, match="volume must be a positive number"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "market",
                    "volume": -0.01,
                }
            )

    def test_missing_required_fields(self):
        with pytest.raises(ValueError, match="missing required fields"):
            validate_intent({"intent_id": "x"})

    def test_reject_requires_reason(self):
        with pytest.raises(ValueError, match="reject_reason required"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.01,
                    "status": "REJECT",
                }
            )

    def test_scaled_requires_scaled_volume(self):
        with pytest.raises(ValueError, match="scaled_volume required"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.01,
                    "status": "SCALED",
                }
            )

    def test_invalid_status_enum(self):
        with pytest.raises(ValueError, match="status must be"):
            validate_intent(
                {
                    "intent_id": "x",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.01,
                    "status": "PENDING",
                }
            )

    def test_default_status_is_approved(self):
        intent = validate_intent(
            {
                "intent_id": "x",
                "venue": "kraken",
                "pair": "BTCUSD",
                "side": "buy",
                "order_type": "market",
                "volume": 0.01,
            }
        )
        assert intent["status"] == "APPROVED"


# ───────────────────────────────────────────────────────────── Registry


class TestExecutionRegistry:
    def test_kraken_is_registered_on_import(self):
        assert "kraken" in registered_venues()

    def test_get_known_provider(self):
        p = get_execution_provider("kraken")
        assert isinstance(p, ExecutionProvider)
        assert p.name == "kraken"

    def test_unknown_venue_raises(self):
        with pytest.raises(ValueError, match="Unknown execution venue"):
            get_execution_provider("nonexistent-venue")

    def test_register_is_idempotent(self):
        class Stub:
            name = "stub-test"

            def supports(self, pair, venue=None):
                return True

            def place_order(self, intent, *, wait=True, timeout_s=5.0):
                return None  # type: ignore

            def get_balance(self):
                return {}

            def get_open_orders(self):
                return []

            def cancel_order(self, order_id):
                return True

        register_execution_provider(Stub())  # type: ignore
        register_execution_provider(Stub())  # type: ignore
        p = get_execution_provider("stub-test")
        assert p.name == "stub-test"


# ───────────────────────────────────────────────────────────── Kraken provider


def _make_completed(stdout="", stderr="", returncode=0):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def _kraken_resp(payload):
    return _make_completed(stdout=json.dumps(payload))


class TestKrakenPlaceOrder:
    def _setup_submit_mock(self, submit_payload, query_payloads=None):
        """Patch subprocess.run to return submit then query-orders results in order."""
        submit = _kraken_resp(submit_payload)
        queries = [_kraken_resp(q) for q in (query_payloads or [])]
        responses = [submit, *queries]

        def runner(cmd, *args, **kwargs):
            if not responses:
                raise AssertionError(f"too many subprocess calls: {cmd}")
            return responses.pop(0)

        return patch("subprocess.run", side_effect=runner)

    def test_market_buy_filled_immediately(self):
        submit = {"txid": ["OABCDE-12345"], "descr": {"order": "buy 0.01 BTCUSD @ market"}}
        query = {
            "OABCDE-12345": {
                "status": "filled",
                "vol_exec": "0.01",
                "cost": "650.5",
                "fee": "1.30",
                "fee_currency": "ZUSD",
                "price": "65050",
                "descr": {"order": "buy 0.01 BTCUSD @ market"},
            }
        }
        intent = {
            "intent_id": "test-001",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        with self._setup_submit_mock(submit, [query]):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=True, timeout_s=2.0)

        assert fill["status"] == "filled"
        assert fill["order_id"] == "OABCDE-12345"
        assert fill["filled_volume"] == pytest.approx(0.01)
        assert fill["fill_price"] == pytest.approx(65050)
        assert fill["cost_quote"] == pytest.approx(650.5)
        assert fill["fee"] == pytest.approx(1.30)
        assert fill["fee_currency"] == "USD"  # ZUSD canonicalised
        assert fill["venue"] == "kraken"

    def test_market_buy_closed_status_full_fill_is_filled(self):
        """Kraken returns venue status 'closed' for a fully-executed MARKET
        order — it must normalise to 'filled', never leak through verbatim
        (market-skills-05x: the leaked 'closed' made the ledger gate skip
        the write entirely)."""
        submit = {"txid": ["OCLOSE-1"], "descr": {"order": "buy 0.01 BTCUSD @ market"}}
        query = {
            "OCLOSE-1": {
                "status": "closed",
                "vol_exec": "0.01",
                "cost": "650.5",
                "fee": "1.30",
                "fee_currency": "ZUSD",
                "price": "65050",
                "descr": {"order": "buy 0.01 BTCUSD @ market"},
            }
        }
        intent = {
            "intent_id": "05x-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        with self._setup_submit_mock(submit, [query]):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=True, timeout_s=2.0)

        assert fill["status"] == "filled"
        assert fill["filled_volume"] == pytest.approx(0.01)
        assert fill["order_id"] == "OCLOSE-1"

    def test_market_buy_closed_status_partial_fill_is_partial(self):
        """A 'closed' order with 0 < vol_exec < requested is recorded as
        partial with filled_volume == vol_exec."""
        submit = {"txid": ["OCLOSE-2"], "descr": {"order": "buy 0.01 BTCUSD @ market"}}
        query = {
            "OCLOSE-2": {
                "status": "closed",
                "vol_exec": "0.004",
                "cost": "260.0",
                "fee": "0.52",
                "fee_currency": "ZUSD",
                "price": "65000",
                "descr": {"order": "buy 0.01 BTCUSD @ market"},
            }
        }
        intent = {
            "intent_id": "05x-2",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        with self._setup_submit_mock(submit, [query]):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=True, timeout_s=2.0)

        assert fill["status"] == "partial"
        assert fill["filled_volume"] == pytest.approx(0.004)
        assert fill["requested_volume"] == pytest.approx(0.01)

    def test_unrecognised_venue_status_never_emitted_verbatim(self):
        """An unrecognised venue status must never be passed through as if
        it were contract-valid (market-skills-05x: the old ladder let raw
        venue strings through the else branch)."""
        submit = {"txid": ["OMYST-1"], "descr": {"order": "buy 0.01 BTCUSD @ market"}}
        query = {"OMYST-1": {"status": "mystery_state", "vol_exec": "0", "descr": {}}}
        intent = {
            "intent_id": "05x-3",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        with self._setup_submit_mock(submit, [query]):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=True, timeout_s=2.0)

        assert fill["status"] == "unknown"

    def test_unrecognised_venue_status_with_real_fill_is_rescued(self):
        """An unrecognised status carrying vol_exec > 0 resolves to the
        volume-derived positive status, never dropping the fill."""
        submit = {"txid": ["OMYST-2"], "descr": {"order": "buy 0.01 BTCUSD @ market"}}
        query = {
            "OMYST-2": {
                "status": "mystery_state",
                "vol_exec": "0.01",
                "cost": "650.5",
                "fee": "1.30",
                "fee_currency": "ZUSD",
                "price": "65050",
                "descr": {"order": "buy 0.01 BTCUSD @ market"},
            }
        }
        intent = {
            "intent_id": "05x-4",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        with self._setup_submit_mock(submit, [query]):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=True, timeout_s=2.0)

        assert fill["status"] == "filled"
        assert fill["filled_volume"] == pytest.approx(0.01)

    def test_no_wait_returns_submitted(self):
        submit = {"txid": ["OLIVE-77777"], "descr": {"order": "buy 1.5 <PRIVATE_PERP>USD @ limit 60.15"}}
        intent = {
            "intent_id": "test-002",
            "venue": "kraken",
            "pair": "<PRIVATE_PERP>USD",
            "side": "buy",
            "order_type": "limit",
            "volume": 1.5,
            "limit_price": 60.15,
        }
        # No query-orders calls expected when wait=False.
        with patch("subprocess.run", return_value=_kraken_resp(submit)):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=False)

        assert fill["status"] == "submitted"
        assert fill["order_id"] == "OLIVE-77777"
        assert fill["filled_volume"] == 0.0
        assert fill["fill_price"] is None

    def test_limit_order_sits_open_after_timeout(self):
        submit = {"txid": ["OOPEN-99999"], "descr": {"order": "buy 1 <PRIVATE_PERP>USD @ limit 50"}}
        query = {"OOPEN-99999": {"status": "open", "vol_exec": "0", "descr": {}}}
        intent = {
            "intent_id": "test-003",
            "venue": "kraken",
            "pair": "<PRIVATE_PERP>USD",
            "side": "buy",
            "order_type": "limit",
            "volume": 1.0,
            "limit_price": 50.0,
        }
        with self._setup_submit_mock(submit, [query]):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=True, timeout_s=0.5)

        assert fill["status"] == "open"
        assert fill["reason"].startswith("timeout after")

    def test_kraken_error_envelope(self):
        submit = {"error": ["EOrder:Insufficient funds"]}
        intent = {
            "intent_id": "test-004",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }
        with patch("subprocess.run", return_value=_kraken_resp(submit)):
            provider = get_execution_provider("kraken")
            fill = provider.place_order(intent, wait=False)

        assert fill["status"] == "error"
        assert "Insufficient funds" in fill["reason"]

    def test_cl_ord_id_passed_through(self):
        submit = {"txid": ["OIDEMP-1"], "descr": {"order": "buy 0.01 BTCUSD @ market"}}
        captured: list[list[str]] = []
        intent = {
            "intent_id": "int-uuid-abc",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
        }

        def runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return _kraken_resp(submit)

        with patch("subprocess.run", side_effect=runner):
            provider = get_execution_provider("kraken")
            provider.place_order(intent, wait=False)

        assert captured, "subprocess.run was not called"
        assert "--cl-ord-id" in captured[0]
        cl_idx = captured[0].index("--cl-ord-id")
        assert captured[0][cl_idx + 1] == "int-uuid-abc"

    def test_stop_limit_passes_price2(self):
        submit = {"txid": ["OSTOP-1"], "descr": {"order": "stop-loss-limit"}}
        captured: list[list[str]] = []
        intent = {
            "intent_id": "stop-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "sell",
            "order_type": "stop-loss-limit",
            "volume": 0.05,
            "limit_price": 64000,
            "stop_price": 65000,
        }

        def runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return _kraken_resp(submit)

        with patch("subprocess.run", side_effect=runner):
            provider = get_execution_provider("kraken")
            provider.place_order(intent, wait=False)

        assert "--price" in captured[0]
        assert "--price2" in captured[0]

    def test_unsupported_order_type(self):
        intent = {
            "intent_id": "bad-type",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "fill-or-kill",  # not in _KRAKEN_ORDER_TYPES
            "volume": 0.01,
        }
        provider = get_execution_provider("kraken")
        fill = provider.place_order(intent, wait=False)
        assert fill["status"] == "error"
        assert "not supported by Kraken CLI" in fill["reason"]


class TestKrakenReadOps:
    def test_get_balance_canonicalises_zusd(self):
        payload = {"ZUSD": "1000.5", "XXBT": "0.5", "EUR.HOLD": "100"}
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken")
            balances = provider.get_balance()
        assert balances["USD"] == pytest.approx(1000.5)
        assert balances["BTC"] == pytest.approx(0.5)

    def test_get_open_orders_parses_envelope(self):
        payload = {
            "open": {
                "OABC-1": {
                    "vol": "1.0",
                    "vol_exec": "0",
                    "descr": {
                        "pair": "<PRIVATE_PERP>USD",
                        "type": "buy",
                        "ordertype": "limit",
                        "price": "60.15",
                        "cl_ord_id": "test-123",
                    },
                }
            }
        }
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken")
            orders = provider.get_open_orders()
        assert len(orders) == 1
        o = orders[0]
        assert o["order_id"] == "OABC-1"
        assert o["pair"] == "<PRIVATE_PERP>USD"
        assert o["side"] == "buy"
        assert o["order_type"] == "limit"
        assert o["limit_price"] == pytest.approx(60.15)
        assert o["cl_ord_id"] == "test-123"

    def test_cancel_order_success(self):
        payload = {"count": 1, "pending": False}
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken")
            ok = provider.cancel_order("OABC-1")
        assert ok is True

    def test_cancel_order_error_returns_false(self):
        payload = {"error": ["EOrder:Unknown order"]}
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken")
            ok = provider.cancel_order("OABC-1")
        assert ok is False


class TestKrakenSupports:
    def test_supports_kraken_venue_true(self):
        payload = {"XBTUSD": {"altname": "XBTUSD"}}
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken")
            assert provider.supports("BTCUSD") is True

    def test_supports_wrong_venue_false(self):
        provider = get_execution_provider("kraken")
        assert provider.supports("BTCUSD", venue="hl") is False

    def test_supports_pair_not_found(self):
        payload = {"error": ["EQuery:Unknown asset pair"]}
        with patch("subprocess.run", return_value=_kraken_resp(payload)):
            provider = get_execution_provider("kraken")
            assert provider.supports("NOPE") is False


class TestVenueStatusNormalisation:
    """Unit coverage for the venue-status mapping table (market-skills-05x).

    Kraken reports a fully-executed MARKET order as venue status
    ``closed``; the contract vocabulary never contains a raw venue string,
    so ``closed`` with an executed volume must normalise to ``filled`` /
    ``partial`` from the volumes, and anything unrecognised must become
    ``unknown`` instead of leaking through verbatim.
    """

    def _norm(self, status, *, vol_exec=0.0, requested=0.0):
        return _execution_kraken.normalise_venue_status(status, vol_exec=vol_exec, requested_volume=requested)

    def test_filled_and_partial_labels_pass_through(self):
        assert self._norm("filled", vol_exec=0.01, requested=0.01) == "filled"
        assert self._norm("partial", vol_exec=0.005, requested=0.01) == "partial"

    def test_closed_full_execution_is_filled(self):
        assert self._norm("closed", vol_exec=0.01, requested=0.01) == "filled"

    def test_closed_partial_execution_is_partial(self):
        assert self._norm("closed", vol_exec=0.004, requested=0.01) == "partial"

    def test_closed_zero_execution_is_cancelled_never_positive(self):
        assert self._norm("closed", vol_exec=0.0, requested=0.01) == "cancelled"

    def test_cancel_flavoured_with_real_fill_is_partial(self):
        assert self._norm("canceled", vol_exec=0.003, requested=0.01) == "partial"
        assert self._norm("cancelled", vol_exec=0.003, requested=0.01) == "partial"

    def test_open_family_maps_to_open(self):
        for label in ("open", "pending", "new"):
            assert self._norm(label) == "open"

    def test_terminal_no_fill_labels_pass_through(self):
        assert self._norm("expired") == "expired"
        assert self._norm("rejected") == "rejected"

    def test_unrecognised_label_becomes_unknown_never_verbatim(self):
        assert self._norm("mystery_state") == "unknown"

    def test_empty_and_none_become_unknown(self):
        assert self._norm("") == "unknown"
        assert self._norm(None) == "unknown"

    def test_unrecognised_label_with_real_fill_is_rescued(self):
        assert self._norm("mystery_state", vol_exec=0.01, requested=0.01) == "filled"
        assert self._norm("mystery_state", vol_exec=0.004, requested=0.01) == "partial"

    def test_unknown_requested_volume_with_fill_is_filled(self):
        assert self._norm("closed", vol_exec=0.01, requested=0.0) == "filled"

    def test_fill_tolerance_absorbs_float_drift(self):
        # One-part-per-million tolerance: vol_exec within 0.0001% of the
        # requested volume is a full fill, not a phantom partial.
        assert self._norm("closed", vol_exec=100.0 * (1 - 5e-7), requested=100.0) == "filled"
        # A genuinely smaller fill stays partial.
        assert self._norm("closed", vol_exec=99.999, requested=100.0) == "partial"


# ───────────────────────────────────────────────────────────── execution-kraken-spot/lib.py


_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")
_LIB_PATH = os.path.join(_SKILLS_DIR, "execution-kraken-spot", "lib.py")


def _load_lib():
    """Load skills/execution-kraken-spot/lib.py by file path (skills/ isn't a package)."""
    spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_lib_under_test", _LIB_PATH)
    mod = __import__("importlib").util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLibRenderers:
    def test_render_intent_summary_includes_required_fields(self):
        render_intent_summary = _load_lib().render_intent_summary

        intent: Intent = {
            "intent_id": "abc-123",
            "venue": "kraken",
            "pair": "<PRIVATE_PERP>USD",
            "side": "buy",
            "order_type": "limit",
            "volume": 1.5,
            "limit_price": 60.15,
            "thesis": "retest",
            "strategy": "trend-follow",
            "conviction": 4,
            "source_skills": ["market-accumulation", "market-trend"],
        }
        out = render_intent_summary(intent)
        assert "<PRIVATE_PERP>USD" in out
        assert "BUY" in out
        assert "limit" in out
        assert "60.1500" in out
        assert "trend-follow" in out

    def test_render_confirmation_filled(self):
        render_confirmation = _load_lib().render_confirmation

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OABC-1",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        out = render_confirmation(conf)
        assert "OABC-1" in out
        assert "FILLED" in out
        assert "0.01" in out


class TestLibIntentLoading:
    def test_load_intent_file_happy(self, tmp_path):
        load_intent_file = _load_lib().load_intent_file

        p = tmp_path / "intent.json"
        p.write_text(
            json.dumps(
                {
                    "intent_id": "test-1",
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.01,
                }
            )
        )
        intent = load_intent_file(str(p))
        assert intent["intent_id"] == "test-1"

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
            "pair": "<PRIVATE_PERP>USD",
            "side": "buy",
            "order_type": "limit",
            "volume": 1.5,
            "limit_price": 60.15,
        }
        intent = intent_from_direct_args(args, intent_id="direct-1")
        assert intent["intent_id"] == "direct-1"
        assert intent["venue"] == "kraken"

    def test_intent_from_direct_args_missing_required(self):
        intent_from_direct_args = _load_lib().intent_from_direct_args

        with pytest.raises(ValueError, match="missing required args"):
            intent_from_direct_args({"pair": "BTCUSD"}, intent_id="x")


class TestLibPortfolioWiring:
    def _write_fill(self, tmp_path):
        from portfolio.db import add_portfolio, init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        pid = add_portfolio(db_path, "spot", base_ccy="USD")
        return db_path, pid

    def test_write_fill_buy(self, tmp_path):
        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OABC-1",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        intent: Intent = {
            "intent_id": "abc",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "strategy": "trend-follow",
            "thesis": "Breakout retest",
        }
        tx_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)
        assert tx_id > 0

        # Verify the row.
        from portfolio.db import list_transactions

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1
        row = rows[0]
        assert row["side"] == "BUY"
        assert row["asset"] == "kraken:BTCUSD"
        assert row["qty"] == pytest.approx(0.01)
        assert row["price"] == pytest.approx(65000.0)
        assert row["cost_quote"] == pytest.approx(650.0)
        assert row["tx_hash"] == "OABC-1"
        # ref = intent_id (for downstream reconciliation)
        assert row["ref"] == "abc"
        # notes blob round-trips JSON
        notes = json.loads(row["notes"])
        assert notes["strategy"] == "trend-follow"
        assert notes["thesis"] == "Breakout retest"
        assert notes["intent_id"] == "abc"
        assert notes["venue"] == "kraken"
        # decision_context direction is canonical (not raw side)
        assert notes["decision_context"]["l3_idea"]["direction"] == "long"

    def test_write_fill_rejects_non_positive_status(self, tmp_path):
        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OABC-1",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.0,
            "status": "rejected",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        with pytest.raises(ValueError, match="non-positive fill"):
            write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path)

    def test_write_fill_rejects_zero_volume(self, tmp_path):
        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "abc",
            "order_id": "OABC-1",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.0,  # zero even though status says filled
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        with pytest.raises(ValueError, match="zero-volume fill"):
            write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path)

    def test_fill_requires_ledger_write_is_volume_based(self):
        """The ledger gate is a fill-presence test, not a status-string
        test (market-skills-05x): a 'closed' / 'unknown' confirmation
        carrying a fill still requires the write; a zero-volume fill never
        does, even with a positive label."""
        lib = _load_lib()

        def conf(status, filled):
            return {
                "intent_id": "05x-gate",
                "order_id": "O05X-G",
                "pair": "BTCUSD",
                "side": "buy",
                "order_type": "market",
                "requested_volume": 0.01,
                "filled_volume": filled,
                "status": status,
                "timestamp": "2026-09-19T00:00:00+00:00",
                "venue": "kraken",
            }

        assert lib.fill_requires_ledger_write(conf("closed", 0.01)) is True
        assert lib.fill_requires_ledger_write(conf("unknown", 0.01)) is True
        assert lib.fill_requires_ledger_write(conf("filled", 0.01)) is True
        assert lib.fill_requires_ledger_write(conf("filled", 0.0)) is False
        assert lib.fill_requires_ledger_write(conf("rejected", 0.0)) is False

    def test_write_fill_accepts_unknown_status_with_positive_volume(self, tmp_path):
        """write_fill_to_portfolio must not refuse a real fill because of
        its status label — 'unknown' with filled_volume > 0 writes the row
        (market-skills-05x)."""
        from portfolio.db import list_transactions

        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "05x-unk",
            "order_id": "O05X-U",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "unknown",
            "timestamp": "2026-09-19T00:00:00+00:00",
            "venue": "kraken",
        }
        tx_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path)
        assert tx_id > 0

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1
        assert rows[0]["qty"] == pytest.approx(0.01)
        assert rows[0]["tx_hash"] == "O05X-U"

    def test_write_fill_accepts_legacy_closed_label_with_volume(self, tmp_path):
        """Even the pre-fix leaked venue label ('closed') with a real fill
        is written — the ledger no longer gates on the status string."""
        from portfolio.db import list_transactions

        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "05x-closed",
            "order_id": "O05X-C",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "closed",
            "timestamp": "2026-09-19T00:00:00+00:00",
            "venue": "kraken",
        }
        tx_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path)
        assert tx_id > 0

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1

    def test_write_fill_retry_same_intent_id_keeps_first_decision(self, tmp_path):
        """Retry path: venue returns the original order (same intent_id),
        ``add_decision`` is a no-op, and the original decision_context
        survives. Verifies the fix for the retry-idempotency contract
        (see LLM-ORCHESTRATION.md §4)."""
        from portfolio.db import get_decision, list_transactions

        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "retry-1",
            "order_id": "OFILL-1",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        intent: Intent = {
            "intent_id": "retry-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "strategy": "trend-follow",
            "thesis": "Breakout retest",
        }
        first_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)
        first_decision = get_decision(db_path, "retry-1")
        assert first_decision is not None
        first_captured_at = first_decision["captured_at"]
        first_dc = first_decision["decision_context_json"]

        # Retry — same intent_id, same fill, just re-recorded. Must not raise.
        second_id = write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)
        assert second_id != first_id  # different transaction row

        # The decisions table has exactly one row for this intent_id, and
        # the original decision_context is preserved (first call wins).
        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 2
        retry_decision = get_decision(db_path, "retry-1")
        assert retry_decision["captured_at"] == first_captured_at
        assert retry_decision["decision_context_json"] == first_dc

    def test_write_fill_merges_decision_decoration(self, tmp_path):
        """Decision decoration (regime, macro, risk verdict, override) from
        the Intent is merged into the auto-built DecisionContext and
        written to the decisions table."""
        from portfolio.db import get_decision

        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "decor-1",
            "order_id": "OFILL-D",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        intent: Intent = {
            "intent_id": "decor-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "strategy": "trend-follow",
            "thesis": "Breakout retest",
            "decision_decoration": {
                "regime_label": "RISK_ON",
                "regime_fng": 65.0,
                "regime_btc_dominance": 45.2,
                "macro_signals": ["fng_greed", "btc_above_ema21"],
                "risk_status": "APPROVED",
                "risk_position_size_pct": 15.0,
                "risk_concerns": ["low volume"],
                "override_from_suggestion": True,
                "override_field": "stop",
                "override_reason": "tightened stop per user",
            },
        }
        write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)

        decision = get_decision(db_path, "decor-1")
        assert decision is not None
        dc = json.loads(decision["decision_context_json"])
        assert dc["regime"]["label"] == "RISK_ON"
        assert dc["regime"]["fng"] == 65.0
        assert dc["regime"]["btc_dominance"] == 45.2
        assert dc["macro_signals"] == ["fng_greed", "btc_above_ema21"]
        assert dc["risk_verdict"]["status"] == "APPROVED"
        assert dc["risk_verdict"]["position_size_pct"] == 15.0
        assert dc["risk_verdict"]["concerns"] == ["low volume"]
        assert dc["override"]["from_suggestion"] is True
        assert dc["override"]["field"] == "stop"
        assert dc["override"]["reason"] == "tightened stop per user"

    def test_write_fill_decoration_absent_yields_builder_defaults(self, tmp_path):
        """No decision_decoration on the Intent: regime/risk/override
        fields stay at builder defaults (None / [] / False). Mirrors the
        docstring contract for the absent-decoration case."""
        from portfolio.db import get_decision

        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "no-decor-1",
            "order_id": "OFILL-ND",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        intent: Intent = {
            "intent_id": "no-decor-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "strategy": "trend-follow",
            "thesis": "Breakout retest",
        }
        write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)

        decision = get_decision(db_path, "no-decor-1")
        dc = json.loads(decision["decision_context_json"])
        assert dc["regime"]["label"] is None
        assert dc["regime"]["fng"] is None
        assert dc["regime"]["btc_dominance"] is None
        assert dc["macro_signals"] == []
        assert dc["risk_verdict"]["status"] is None
        assert dc["risk_verdict"]["position_size_pct"] is None
        assert dc["risk_verdict"]["concerns"] == []
        assert dc["override"]["from_suggestion"] is False

    def test_write_fill_invalid_side_raises(self, tmp_path):
        """An empty/unknown side in the FillConfirmation now raises
        rather than silently being recorded as a short trade."""
        db_path, pid = self._write_fill(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        conf: FillConfirmation = {
            "intent_id": "bad-side-1",
            "order_id": "OFILL-BAD",
            "pair": "BTCUSD",
            "side": "",  # empty — would have been silently "short"
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-06-22T00:00:00+00:00",
            "venue": "kraken",
        }
        intent: Intent = {
            "intent_id": "bad-side-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "strategy": "trend-follow",
            "thesis": "x",
        }
        with pytest.raises(ValueError, match="side must be"):
            write_fill_to_portfolio(conf, portfolio_id=pid, db_path=db_path, intent=intent)


class TestLedgerWriteFailureContract:
    """Per-fix fixtures for market-skills-hxe: a venue fill whose ledger
    write fails must never silently succeed with no transaction row, and
    the CLI must surface it as a hard failure (non-zero exit) instead of
    a stderr-only warning.

    Shapes pinned:
      - DB without the `decisions` table -> transaction row still lands
        (migrate-on-demand), the decision row is written, no exception.
      - add_decision-style failure mid-write -> no partial transaction
        row (atomic rollback) and the error propagates.
      - CLI: write_fill_to_portfolio raising -> exit 1 with a loud
        venue/ledger disagreement message, the confirmation still printed,
        the stderr warning kept, and `errors` populated in the --json
        payload.
    """

    def _conf(self, order_id="OHXE-1"):
        return {
            "intent_id": "hxe-1",
            "order_id": order_id,
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": 0.01,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": "filled",
            "timestamp": "2026-09-18T00:00:00+00:00",
            "venue": "kraken",
        }

    def _intent(self):
        return {
            "intent_id": "hxe-1",
            "venue": "kraken",
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "volume": 0.01,
            "strategy": "trend-follow",
            "thesis": "Breakout retest",
        }

    def _db_without_decisions_table(self, tmp_path):
        """Pre-decisions-feature DB: portfolios + transactions only."""
        import sqlite3

        db_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_ccy TEXT NOT NULL DEFAULT 'EUR',
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
                ts TEXT NOT NULL,
                side TEXT NOT NULL,
                asset TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL,
                cost_quote REAL,
                fee REAL DEFAULT 0,
                tx_hash TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                ref TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(portfolio_id, ts, tx_hash, side, asset)
            );
            """
        )
        conn.execute("INSERT INTO portfolios (name, base_ccy) VALUES ('spot', 'USD')")
        conn.commit()
        conn.close()
        return db_path, 1

    def test_missing_decisions_table_still_lands_transaction_row(self, tmp_path):
        """THE fixture: a DB without the `decisions` table must not lose
        the transaction row. Pre-fix this raised
        sqlite3.OperationalError('no such table: decisions') from
        add_decision BEFORE the transaction insert, so no row landed and
        run.py only printed a stderr warning with exit 0."""
        from portfolio.db import get_decision, list_transactions

        db_path, pid = self._db_without_decisions_table(tmp_path)
        write_fill_to_portfolio = _load_lib().write_fill_to_portfolio

        tx_id = write_fill_to_portfolio(self._conf(), portfolio_id=pid, db_path=db_path, intent=self._intent())
        assert tx_id > 0

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1, "transaction row lost on a DB without the decisions table"
        assert rows[0]["tx_hash"] == "OHXE-1"
        assert rows[0]["ref"] == "hxe-1"
        # The decision trace was migrated on demand and written too.
        decision = get_decision(db_path, "hxe-1")
        assert decision is not None
        dc = json.loads(decision["decision_context_json"])
        assert dc["l3_idea"]["direction"] == "long"

    def test_atomic_rollback_when_decision_insert_fails(self, tmp_path):
        """A failure during the decision insert must leave NO transaction
        row (single-transaction rollback) and must propagate — the caller
        (run.py) turns it into a hard error rather than a silent skip.
        The `decisions` table keeps its real shape; a BEFORE INSERT
        trigger makes the decision INSERT itself fail, so the insert is
        genuinely attempted and any half-written state must be rolled
        back."""
        import sqlite3

        from portfolio.db import add_transaction_with_decision, list_transactions

        db_path, pid = self._write_fill(tmp_path)
        # Make the decision INSERT itself fail while the table has its
        # real shape (migrate-on-demand is a no-op, the indexes build
        # fine, the trigger aborts the INSERT).
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TRIGGER decisions_block BEFORE INSERT ON decisions BEGIN SELECT RAISE(ABORT, 'boom'); END;"
        )
        conn.commit()
        conn.close()

        decision = {
            "intent_id": "boom-1",
            "pair": "BTCUSD",
            "decision_context_json": "{}",
            "portfolio_id": pid,
            "captured_at": "2026-09-18T00:00:00+00:00",
        }
        with pytest.raises(sqlite3.IntegrityError, match="boom"):
            add_transaction_with_decision(
                db_path,
                pid,
                ts="2026-09-18T00:00:01+00:00",
                side="BUY",
                asset="kraken:BTCUSD",
                qty=0.01,
                price=65000.0,
                cost_quote=650.0,
                tx_hash="OBOOM-1",
                source="execution-kraken-spot",
                ref="boom-1",
                notes="{}",
                decision=decision,
            )

        # Rolled back atomically: no transaction row for the failed write.
        rows = [r for r in list_transactions(db_path, portfolio_id=pid) if r["tx_hash"] == "OBOOM-1"]
        assert rows == [], "failed decision insert must not leave a transaction row behind"
        # And no half-written decision row either.
        conn = sqlite3.connect(db_path)
        leftover = conn.execute("SELECT * FROM decisions").fetchall()
        conn.close()
        assert leftover == []

    def test_atomic_rollback_when_transaction_insert_fails_after_decision(self, tmp_path):
        """The mirror direction: the decision row IS written, then the
        `transactions` INSERT fails — the single transaction must roll
        the decision row back too, leaving no decision trace behind for
        a fill that never landed in the ledger."""
        import sqlite3

        from portfolio.db import add_transaction_with_decision, get_decision, list_transactions

        db_path, pid = self._write_fill(tmp_path)
        # Healthy decisions table (via _write_fill/init_db); make the
        # transactions INSERT itself fail with a BEFORE INSERT trigger.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TRIGGER transactions_block BEFORE INSERT ON transactions BEGIN SELECT RAISE(ABORT, 'tx-boom'); END;"
        )
        conn.commit()
        conn.close()

        decision = {
            "intent_id": "tx-boom-1",
            "pair": "BTCUSD",
            "decision_context_json": "{}",
            "portfolio_id": pid,
            "captured_at": "2026-09-18T00:00:00+00:00",
        }
        with pytest.raises(sqlite3.IntegrityError, match="tx-boom"):
            add_transaction_with_decision(
                db_path,
                pid,
                ts="2026-09-18T00:00:02+00:00",
                side="SELL",
                asset="kraken:BTCUSD",
                qty=0.01,
                price=66000.0,
                cost_quote=660.0,
                tx_hash="OBOOM-2",
                source="execution-kraken-spot",
                ref="tx-boom-1",
                notes="{}",
                decision=decision,
            )

        # Rolled back atomically: no transaction row AND the decision
        # row that was already written is gone.
        rows = [r for r in list_transactions(db_path, portfolio_id=pid) if r["tx_hash"] == "OBOOM-2"]
        assert rows == [], "failed transactions insert must not leave a transaction row behind"
        assert get_decision(db_path, "tx-boom-1") is None, (
            "decision row written before the failed transactions insert must be rolled back"
        )

    def _run_cli(self, *argv, monkeypatch, tmp_path):
        from portfolio.db import add_portfolio, init_db

        db_path = str(tmp_path / "cli.db")
        init_db(db_path)
        add_portfolio(db_path, "spot", base_ccy="USD")
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", db_path)
        monkeypatch.setenv("AFK_SLEEP_WINDOW_START_HOUR_UTC", "0")
        monkeypatch.setenv("AFK_SLEEP_WINDOW_END_HOUR_UTC", "0")
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        run_path = os.path.join(os.path.dirname(__file__), "..", "skills", "execution-kraken-spot", "scripts", "run.py")
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_hxe_run", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Fresh argparse namespace mirroring a live --portfolio submit.
        args = argparse.Namespace(
            command="submit",
            db=str(tmp_path / "cli.db"),
            intent=None,
            pair="BTCUSD",
            side="buy",
            order_type="market",
            volume=0.01,
            limit_price=None,
            stop_price=None,
            time_in_force=None,
            deadline=None,
            intent_id="hxe-cli-1",
            thesis=None,
            strategy=None,
            conviction=None,
            source_skills=None,
            decision_decoration=None,
            override_from_suggestion=False,
            portfolio="spot",
            dry_run=False,
            yes=True,
            no_wait=False,
            wait_timeout=5.0,
            json="--json" in argv,
        )

        provider = get_execution_provider("kraken")
        confirmation = self._conf(order_id="OHXE-CLI")
        write_err = ValueError("no such table: decisions")

        with (
            patch.object(provider, "place_order", return_value=confirmation) as mock_place,
            patch.object(mod._lib, "write_fill_to_portfolio", side_effect=write_err) as mock_write,
        ):
            rc = mod.cmd_submit(args)

        assert mock_place.called
        assert mock_write.called
        return rc, args.json

    def test_cli_ledger_write_failure_is_hard_error(self, tmp_path, monkeypatch, capsys):
        """A venue fill whose portfolio write fails must exit non-zero,
        keep the stderr warning AND add a loud venue/ledger-disagreement
        error — not silently exit 0."""
        rc, _ = self._run_cli(monkeypatch=monkeypatch, tmp_path=tmp_path)
        assert rc == 1, "ledger write failure after a venue fill must exit non-zero"
        captured = capsys.readouterr()
        # The confirmation is still printed — the order was NOT abandoned.
        assert "OHXE-CLI" in captured.out
        # The original warning is kept in addition to the hard failure.
        assert "warning: order placed but portfolio write failed" in captured.err
        assert "DISAGREE" in captured.err

    def test_cli_ledger_write_failure_json_carries_errors(self, tmp_path, monkeypatch, capsys):
        """--json path: non-zero exit and `errors` populated so an
        automated caller cannot miss the venue/ledger disagreement."""
        rc, as_json = self._run_cli("--json", monkeypatch=monkeypatch, tmp_path=tmp_path)
        assert as_json
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["confirmation"]["order_id"] == "OHXE-CLI"
        assert payload["errors"], "errors must be populated in the --json payload"
        assert "portfolio write failed" in payload["errors"][0]
        assert "portfolio_tx_id" not in payload

    # Reuse TestLibPortfolioWiring's helper via inheritance-free delegation.
    def _write_fill(self, tmp_path):
        from portfolio.db import add_portfolio, init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        pid = add_portfolio(db_path, "spot", base_ccy="USD")
        return db_path, pid


class TestMarketOrderLedgerAutoWrite:
    """Per-fix fixtures for market-skills-05x: a fully-filled MARKET order
    comes back with venue status 'closed', which the old status-string
    ledger gate (`status in ("filled", "partial")`) never matched — the
    venue filled but ``write_fill_to_portfolio`` was never attempted
    (exit 0, no row, clean logs). The gate must be volume-based: any
    confirmation carrying a fill writes the row automatically.

    Pre-fix, the first test below produced exit 0 with NO ledger row.
    """

    def _conf(self, order_id="O05X-CLI", status="closed", filled=0.01):
        return {
            "intent_id": "05x-cli",
            "order_id": order_id,
            "pair": "BTCUSD",
            "side": "buy",
            "order_type": "market",
            "requested_volume": 0.01,
            "filled_volume": filled,
            "fill_price": 65000.0,
            "cost_quote": 650.0,
            "fee": 1.3,
            "fee_currency": "USD",
            "status": status,
            "timestamp": "2026-09-19T00:00:00+00:00",
            "venue": "kraken",
        }

    def _run_cli(self, *argv, monkeypatch, tmp_path, confirmation):
        """Drive cmd_submit with a patched provider returning the given
        confirmation and the REAL write_fill_to_portfolio (not mocked),
        against a fresh portfolio DB."""
        from portfolio.db import add_portfolio, init_db

        db_path = str(tmp_path / "cli.db")
        init_db(db_path)
        pid = add_portfolio(db_path, "spot", base_ccy="USD")
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", db_path)
        monkeypatch.setenv("AFK_SLEEP_WINDOW_START_HOUR_UTC", "0")
        monkeypatch.setenv("AFK_SLEEP_WINDOW_END_HOUR_UTC", "0")
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        run_path = os.path.join(os.path.dirname(__file__), "..", "skills", "execution-kraken-spot", "scripts", "run.py")
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_05x_run", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        args = argparse.Namespace(
            command="submit",
            db=db_path,
            intent=None,
            pair="BTCUSD",
            side="buy",
            order_type="market",
            volume=0.01,
            limit_price=None,
            stop_price=None,
            time_in_force=None,
            deadline=None,
            intent_id="05x-cli-1",
            thesis=None,
            strategy=None,
            conviction=None,
            source_skills=None,
            decision_decoration=None,
            override_from_suggestion=False,
            portfolio="spot",
            dry_run=False,
            yes=True,
            no_wait=False,
            wait_timeout=5.0,
            json="--json" in argv,
        )

        provider = get_execution_provider("kraken")
        with patch.object(provider, "place_order", return_value=confirmation) as mock_place:
            rc = mod.cmd_submit(args)

        assert mock_place.called
        return rc, args.json, db_path, pid

    def test_cli_closed_full_fill_writes_ledger_row_automatically(self, tmp_path, monkeypatch, capsys):
        """THE regression fixture: a market buy whose confirmation is venue
        status 'closed' with full vol_exec must write the ledger row
        automatically (pre-fix: exit 0 with NO row)."""
        from portfolio.db import list_transactions

        rc, as_json, db_path, pid = self._run_cli(
            "--json",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            confirmation=self._conf(status="closed", filled=0.01),
        )
        assert rc == 0, "a closed full fill must auto-write the ledger row and exit 0"
        assert as_json
        payload = json.loads(capsys.readouterr().out)
        assert payload["confirmation"]["status"] == "closed"
        assert "portfolio_tx_id" in payload, "ledger row must be auto-written for a closed full fill"

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1, "the market-order fill must land in the ledger without manual wiring"
        assert rows[0]["qty"] == pytest.approx(0.01)
        assert rows[0]["tx_hash"] == "O05X-CLI"
        assert rows[0]["asset"] == "kraken:BTCUSD"

    def test_cli_closed_partial_fill_is_recorded(self, tmp_path, monkeypatch, capsys):
        """A partial fill under the leaked 'closed' label is written too —
        the gate is the volume, not the label."""
        from portfolio.db import list_transactions

        rc, as_json, db_path, pid = self._run_cli(
            "--json",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            confirmation=self._conf(status="closed", filled=0.004),
        )
        assert rc == 0
        assert as_json
        payload = json.loads(capsys.readouterr().out)
        assert "portfolio_tx_id" in payload

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 1
        assert rows[0]["qty"] == pytest.approx(0.004)

    def test_cli_no_fill_confirmation_writes_nothing(self, tmp_path, monkeypatch, capsys):
        """Negative control: a zero-volume confirmation ('submitted') still
        writes no row and stays exit 0 — the volume gate is not a blanket
        always-write."""
        from portfolio.db import list_transactions

        rc, as_json, db_path, pid = self._run_cli(
            "--json",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            confirmation=self._conf(status="submitted", filled=0.0),
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert "portfolio_tx_id" not in payload

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 0

    def test_cli_filled_label_zero_volume_is_loud_ledger_error(self, tmp_path, monkeypatch, capsys):
        """A contradictory confirmation — positive status label with
        filled_volume == 0 (e.g. a venue response missing or empty
        vol_exec) — must still reach the write and hit the loud
        venue/ledger-disagreement hard error; a volume-only gate would
        silently skip it (exit 0, no row), regressing the hxe contract."""
        from portfolio.db import list_transactions

        rc, as_json, db_path, pid = self._run_cli(
            "--json",
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            confirmation=self._conf(status="filled", filled=0.0),
        )
        assert rc == 1, "a 'filled' label with zero volume must be a loud hard error, not a silent skip"
        assert as_json
        captured = capsys.readouterr()
        assert "warning: order placed but portfolio write failed" in captured.err
        assert "DISAGREE" in captured.err
        payload = json.loads(captured.out)
        assert payload["errors"], "errors must be populated in the --json payload"
        assert "portfolio_tx_id" not in payload

        rows = list_transactions(db_path, portfolio_id=pid)
        assert len(rows) == 0


# ───────────────────────────────────────────────────────────── CLI surface


class TestCLIArgparse:
    """Smoke tests for scripts/run.py argparse — invoke main() with argv mocks."""

    def _run_cli(self, *argv, monkeypatch):
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", "/tmp/test-execution-kraken-spot-portfolio.db")
        # Collapse the AFK sleep window to an empty window (start == end
        # skips the gate) so submit tests are deterministic and don't
        # depend on the wall-clock UTC hour they happen to run in.
        monkeypatch.setenv("AFK_SLEEP_WINDOW_START_HOUR_UTC", "0")
        monkeypatch.setenv("AFK_SLEEP_WINDOW_END_HOUR_UTC", "0")
        skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-spot",
            "scripts",
            "run.py",
        )
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_run", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(sys, "argv", ["run.py", *argv]):
            return mod.main()

    def test_no_subcommand_shows_help(self, capsys, monkeypatch):
        # argparse calls sys.exit(0) on --help; the script doesn't catch
        # that — SystemExit propagates and pytest catches it as a clean
        # exit. Just verify the help text was emitted.
        with pytest.raises(SystemExit) as exc:
            self._run_cli("--help", monkeypatch=monkeypatch)
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "submit" in captured.out
        assert "balance" in captured.out

    def test_balance_calls_provider(self, monkeypatch):
        with patch(
            "analysis.providers.execution.kraken_spot.KrakenExecutionProvider.get_balance",
            return_value={"USD": 100.0, "BTC": 0.5},
        ) as mock_balance:
            rc = self._run_cli("balance", monkeypatch=monkeypatch)
        assert rc == 0
        mock_balance.assert_called_once()

    def test_orders_calls_provider(self, monkeypatch):
        with patch(
            "analysis.providers.execution.kraken_spot.KrakenExecutionProvider.get_open_orders",
            return_value=[{"order_id": "O1", "pair": "BTCUSD"}],
        ) as mock_orders:
            rc = self._run_cli("orders", monkeypatch=monkeypatch)
        assert rc == 0
        mock_orders.assert_called_once()

    def test_cancel_calls_provider(self, monkeypatch):
        with patch(
            "analysis.providers.execution.kraken_spot.KrakenExecutionProvider.cancel_order",
            return_value=True,
        ) as mock_cancel:
            rc = self._run_cli("cancel", "OABC-1", monkeypatch=monkeypatch)
        assert rc == 0
        mock_cancel.assert_called_once_with("OABC-1")

    def test_submit_rejected_intent_returns_2(self, monkeypatch):
        rc = self._run_cli(
            "submit",
            "--pair",
            "BTCUSD",
            "--side",
            "buy",
            "--order-type",
            "market",
            "--volume",
            "0.01",
            "--intent",
            "/dev/null",  # forces --intent path; load will fail
            monkeypatch=monkeypatch,
        )
        # load_intent_file on /dev/null raises — main returns 2.
        assert rc == 2

    def test_submit_dry_run_invokes_kraken_validate(self, tmp_path, monkeypatch):
        # Patch subprocess.run to capture the validate call.
        captured: list[list[str]] = []

        def runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return _make_completed(
                stdout=json.dumps({"descr": {"order": "buy 0.01 BTCUSD @ market"}}),
            )

        with patch("subprocess.run", side_effect=runner):
            rc = self._run_cli(
                "submit",
                "--pair",
                "BTCUSD",
                "--side",
                "buy",
                "--order-type",
                "market",
                "--volume",
                "0.01",
                "--dry-run",
                monkeypatch=monkeypatch,
            )

        assert rc == 0
        # Find the --validate call.
        validate_calls = [c for c in captured if "--validate" in c]
        assert validate_calls, f"no --validate call captured: {captured}"
        cmd = validate_calls[0]
        assert "--type" in cmd
        assert "market" in cmd

    def test_parse_decoration_merges_json_and_flag(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-spot",
            "scripts",
            "run.py",
        )
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_decor", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        raw = json.dumps(
            {
                "regime_label": "RISK_ON",
                "risk_status": "APPROVED",
                "macro_signals": ["fng_greed"],
            }
        )
        out = mod._parse_decoration(raw, override_from_suggestion=True)
        assert out == {
            "regime_label": "RISK_ON",
            "risk_status": "APPROVED",
            "macro_signals": ["fng_greed"],
            "override_from_suggestion": True,
        }

    def test_parse_decoration_returns_none_when_empty(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-spot",
            "scripts",
            "run.py",
        )
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_decor2", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._parse_decoration(None, False) is None
        assert mod._parse_decoration("{}", False) is None
        assert mod._parse_decoration(None, True) == {"override_from_suggestion": True}

    def test_parse_decoration_invalid_json_raises(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-spot",
            "scripts",
            "run.py",
        )
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_decor3", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with pytest.raises(ValueError, match="valid JSON"):
            mod._parse_decoration("not json", False)

    def test_parse_decoration_non_object_raises(self):
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "execution-kraken-spot",
            "scripts",
            "run.py",
        )
        spec = __import__("importlib").util.spec_from_file_location("execution_kraken_spot_decor4", run_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with pytest.raises(ValueError, match="JSON object"):
            mod._parse_decoration("[1, 2, 3]", False)


# ───────────────────────────────────────────────────────────── cl_ord_id length limit
#
# Per-fix fixtures for the Kraken cl_ord_id rejection bug: the auto-
# generated cli-<uuid4> id was 40 chars and the venue rejects anything
# over 18 chars with EGeneral:Invalid arguments:cl_ord_id, breaking every
# dry-run and every live submit; the dry-run path also collapsed the
# venue's error message into the generic "kraken --validate failed".


class TestGeneratedIntentIdLimit:
    def test_generated_id_within_measured_limit(self):
        """The default id must be <= the module limit (the real constant,
        not a literal 18), keep the cli- prefix, and fit in one call."""
        lib = _load_lib()
        limit = lib.KRAKEN_CL_ORD_ID_MAX_LEN
        intent_id = lib.generate_intent_id()
        assert intent_id.startswith("cli-")
        assert len(intent_id) <= limit

    def test_generated_id_unique_across_calls(self):
        lib = _load_lib()
        ids = {lib.generate_intent_id() for _ in range(50)}
        assert len(ids) == 50

    def test_limit_default_is_measured_18(self):
        # The env var is unset in the test environment; the module default
        # must be the empirically measured bound (18 OK / 19 FAIL), not
        # the documented 36.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KRAKEN_CL_ORD_ID_MAX_LEN", None)
            lib = _load_lib()
            assert lib.KRAKEN_CL_ORD_ID_MAX_LEN == 18

    def test_limit_env_var_override(self):
        lib = _load_lib()
        with patch.dict(os.environ, {"KRAKEN_CL_ORD_ID_MAX_LEN": "10"}):
            fresh = _load_lib()
            assert fresh.KRAKEN_CL_ORD_ID_MAX_LEN == 10
            generated = fresh.generate_intent_id()
            assert len(generated) <= 10
        # The pristine module still holds the default.
        assert lib.KRAKEN_CL_ORD_ID_MAX_LEN == 18

    def test_cli_default_path_generates_bounded_id(self, monkeypatch):
        """The CLI default path (no --intent-id) must forward a
        --cl-ord-id within the measured limit and keep the cli- prefix.
        Guards run.py's default-id fix: reverting to the bare
        cli-<dashed uuid4> generator (40 chars) fails the length assert."""
        lib = _load_lib()
        limit = lib.KRAKEN_CL_ORD_ID_MAX_LEN
        captured: list[list[str]] = []

        def runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return _make_completed(stdout=json.dumps({"descr": {"order": "buy 0.01 BTCUSD @ market"}}))

        with patch("subprocess.run", side_effect=runner):
            rc = TestCLIArgparse()._run_cli(
                "submit",
                "--pair",
                "BTCUSD",
                "--side",
                "buy",
                "--order-type",
                "market",
                "--volume",
                "0.01",
                "--dry-run",
                monkeypatch=monkeypatch,
            )

        assert rc == 0
        validate_calls = [c for c in captured if "--validate" in c]
        assert validate_calls, f"no --validate call captured: {captured}"
        cl_idx = validate_calls[0].index("--cl-ord-id")
        cl_ord_id = validate_calls[0][cl_idx + 1]
        assert cl_ord_id.startswith("cli-")
        assert len(cl_ord_id) <= limit

    def test_too_small_env_override_raises_from_generator(self):
        """An override below the generation floor (cli- prefix + >=2 hex
        chars) must raise a clear configuration error naming the env var
        and the floor, instead of silently emitting an id that the CLI's
        own validation would then reject."""
        with patch.dict(os.environ, {"KRAKEN_CL_ORD_ID_MAX_LEN": "4"}):
            fresh = _load_lib()
            assert fresh.KRAKEN_CL_ORD_ID_MAX_LEN == 4
            floor = len(fresh.INTENT_ID_PREFIX) + 2
            with pytest.raises(ValueError, match="KRAKEN_CL_ORD_ID_MAX_LEN") as excinfo:
                fresh.generate_intent_id()
            assert str(floor) in str(excinfo.value)

    def test_non_numeric_env_override_raises_clear_error(self):
        """A non-numeric override must abort with a clear configuration
        error naming the env var, not a raw ``invalid literal for int()``
        traceback from module import."""
        with patch.dict(os.environ, {"KRAKEN_CL_ORD_ID_MAX_LEN": "banana"}):
            with pytest.raises(ValueError, match="KRAKEN_CL_ORD_ID_MAX_LEN"):
                _load_lib()


class TestHandSuppliedIntentIdLimit:
    """A hand-supplied id (over the venue limit) must fail at the CLI
    boundary, before any venue call, naming the limit and offending
    length; a compliant id must flow through unchanged (no truncation)."""

    def _submit_argv(self, *extra):
        return (
            "submit",
            "--pair",
            "BTCUSD",
            "--side",
            "buy",
            "--order-type",
            "market",
            "--volume",
            "0.01",
            *extra,
        )

    def test_oversized_flag_id_rejected_before_venue_call(self, monkeypatch, capsys):
        lib = _load_lib()
        limit = lib.KRAKEN_CL_ORD_ID_MAX_LEN
        oversized = "x" * (limit + 22)  # 40 chars, the old cli-<uuid4> shape
        with patch("subprocess.run", side_effect=AssertionError("venue must not be called")) as mock_run:
            rc = TestCLIArgparse()._run_cli(*self._submit_argv("--intent-id", oversized), monkeypatch=monkeypatch)
        assert rc == 2
        err = capsys.readouterr().err
        assert str(len(oversized)) in err, f"offending length not named in error: {err}"
        assert str(limit) in err, f"limit not named in error: {err}"
        mock_run.assert_not_called()

    def test_oversized_intent_file_id_rejected(self, tmp_path, monkeypatch, capsys):
        lib = _load_lib()
        limit = lib.KRAKEN_CL_ORD_ID_MAX_LEN
        p = tmp_path / "intent.json"
        p.write_text(
            json.dumps(
                {
                    "intent_id": "y" * (limit + 1),
                    "venue": "kraken",
                    "pair": "BTCUSD",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.01,
                }
            )
        )
        with patch("subprocess.run", side_effect=AssertionError("venue must not be called")) as mock_run:
            rc = TestCLIArgparse()._run_cli(*self._submit_argv("--intent", str(p)), monkeypatch=monkeypatch)
        assert rc == 2
        err = capsys.readouterr().err
        assert str(limit) in err, f"limit not named in error: {err}"
        mock_run.assert_not_called()

    def test_compliant_flag_id_passed_unchanged(self, monkeypatch):
        captured: list[list[str]] = []
        compliant = "tf-hype-0622a"  # 13 chars

        def runner(cmd, *args, **kwargs):
            captured.append(cmd)
            return _make_completed(stdout=json.dumps({"descr": {"order": "buy 0.01 BTCUSD @ market"}}))

        with patch("subprocess.run", side_effect=runner):
            rc = TestCLIArgparse()._run_cli(
                *self._submit_argv("--intent-id", compliant, "--dry-run"), monkeypatch=monkeypatch
            )
        assert rc == 0
        validate_calls = [c for c in captured if "--validate" in c]
        assert validate_calls, f"no --validate call captured: {captured}"
        cl_idx = validate_calls[0].index("--cl-ord-id")
        assert validate_calls[0][cl_idx + 1] == compliant  # unchanged, not truncated


class TestDryRunVenueErrorSurfacing:
    """The dry-run path must surface the venue error that arrives as JSON
    on STDOUT with a non-zero rc, instead of the generic
    "kraken --validate failed" string."""

    def _dry_run_argv(self, *extra):
        return (
            "submit",
            "--pair",
            "NEAREUR",
            "--side",
            "sell",
            "--order-type",
            "stop-loss",
            "--volume",
            "42.875",
            "--limit-price",
            "2.28",
            "--dry-run",
            *extra,
        )

    def test_venue_error_on_stdout_is_surfaced(self, monkeypatch, capsys):
        venue_stdout = json.dumps({"error": "api", "message": "EGeneral:Invalid arguments:cl_ord_id"})
        with patch(
            "subprocess.run",
            return_value=_make_completed(stdout=venue_stdout, stderr="", returncode=1),
        ):
            rc = TestCLIArgparse()._run_cli(*self._dry_run_argv("--json"), monkeypatch=monkeypatch)
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        kv = payload["kraken_validate"]
        assert kv["error"] == "EGeneral:Invalid arguments:cl_ord_id"
        assert "kraken --validate failed" not in json.dumps(payload)
        assert kv["rc"] == 1
        assert "EGeneral:Invalid arguments:cl_ord_id" in kv["stdout"]

    def test_stderr_fallback_still_works(self, monkeypatch, capsys):
        with patch(
            "subprocess.run",
            return_value=_make_completed(stdout="", stderr="kraken: boom", returncode=2),
        ):
            TestCLIArgparse()._run_cli(*self._dry_run_argv("--json"), monkeypatch=monkeypatch)
        payload = json.loads(capsys.readouterr().out)
        kv = payload["kraken_validate"]
        # No stdout JSON to parse: the CLI's stderr text becomes the error
        # (the pre-fix fallback behaviour), plus the raw rc/stderr keys.
        assert kv["error"] == "kraken: boom"
        assert kv["rc"] == 2
        assert kv["stderr"] == "kraken: boom"

    def test_human_rendering_names_venue_message(self, monkeypatch, capsys):
        venue_stdout = json.dumps({"error": "api", "message": "EGeneral:Invalid arguments:cl_ord_id"})
        with patch(
            "subprocess.run",
            return_value=_make_completed(stdout=venue_stdout, stderr="", returncode=1),
        ):
            rc = TestCLIArgparse()._run_cli(*self._dry_run_argv(), monkeypatch=monkeypatch)
        assert rc == 0
        out = capsys.readouterr().out
        assert "EGeneral:Invalid arguments:cl_ord_id" in out
        assert "kraken --validate failed" not in out
        assert "kraken rc" in out
