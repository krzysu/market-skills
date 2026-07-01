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

    def test_no_wait_returns_submitted(self):
        submit = {"txid": ["OLIVE-77777"], "descr": {"order": "buy 1.5 HYPEUSD @ limit 60.15"}}
        intent = {
            "intent_id": "test-002",
            "venue": "kraken",
            "pair": "HYPEUSD",
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
        submit = {"txid": ["OOPEN-99999"], "descr": {"order": "buy 1 HYPEUSD @ limit 50"}}
        query = {"OOPEN-99999": {"status": "open", "vol_exec": "0", "descr": {}}}
        intent = {
            "intent_id": "test-003",
            "venue": "kraken",
            "pair": "HYPEUSD",
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
                        "pair": "HYPEUSD",
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
        assert o["pair"] == "HYPEUSD"
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
            "pair": "HYPEUSD",
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
        assert "HYPEUSD" in out
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
            "pair": "HYPEUSD",
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


# ───────────────────────────────────────────────────────────── CLI surface


class TestCLIArgparse:
    """Smoke tests for scripts/run.py argparse — invoke main() with argv mocks."""

    def _run_cli(self, *argv, monkeypatch):
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", "/tmp/test-execution-kraken-spot-portfolio.db")
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
