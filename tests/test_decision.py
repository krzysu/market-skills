"""Tests for analysis/decision.py — DecisionContext pure helpers."""

import pytest

from analysis.decision import (
    build_decision_context,
    build_decision_context_from_idea,
    compute_rr_to_tp2,
    direction_from_side,
    validate_decision_context,
)

# ── compute_rr_to_tp2 ───────────────────────────────────────────────────


class TestComputeRrToTp2:
    def test_long_rr_positive(self):
        assert compute_rr_to_tp2("long", 100, 90, 130) == 3.0

    def test_short_rr_positive(self):
        assert compute_rr_to_tp2("short", 100, 110, 80) == 2.0

    def test_long_none_on_missing_direction(self):
        assert compute_rr_to_tp2(None, 100, 90, 130) is None

    def test_long_none_on_missing_entry(self):
        assert compute_rr_to_tp2("long", None, 90, 130) is None

    def test_long_none_on_zero_denom(self):
        assert compute_rr_to_tp2("long", 100, 100, 130) is None

    def test_short_none_on_zero_denom(self):
        assert compute_rr_to_tp2("short", 100, 100, 80) is None

    def test_long_rr_rounding(self):
        result = compute_rr_to_tp2("long", 100, 90, 132.5)
        assert result == 3.25

    def test_short_bug_regression_long_formula_on_short(self):
        """Verifies the bug fix: using long formula on short gives wrong result.

        Short: entry=69, stop=77, tp2=58
        Correct (short): (entry - tp2) / (stop - entry) = 11/8 = 1.38
        Wrong (long): (tp2 - entry) / (entry - stop) = -11/-8 = 1.375 (close but wrong)
        """
        result = compute_rr_to_tp2("short", 69, 77, 58)
        assert result == 1.38, f"Expected 1.38 for short R:R, got {result}"


# ── direction_from_side ────────────────────────────────────────────────


class TestDirectionFromSide:
    def test_buy_to_long(self):
        assert direction_from_side("buy") == "long"

    def test_sell_to_short(self):
        assert direction_from_side("sell") == "short"

    def test_uppercase_buy_normalised(self):
        # Kraken's API lowercases upstream, but defensive.
        assert direction_from_side("BUY") == "long"

    def test_uppercase_sell_normalised(self):
        assert direction_from_side("SELL") == "short"

    def test_whitespace_stripped(self):
        assert direction_from_side("  buy  ") == "long"

    def test_empty_string_raises(self):
        # Regression: previous code mapped anything non-"buy" to "short"
        # silently, so a missing/empty side would be recorded as a short
        # trade. Now raises.
        with pytest.raises(ValueError, match="side must be"):
            direction_from_side("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="side must be"):
            direction_from_side(None)

    def test_unknown_value_raises(self):
        with pytest.raises(ValueError, match="side must be"):
            direction_from_side("long")  # looks canonical but is not a raw side

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="side must be"):
            direction_from_side(42)


# ── build_decision_context ──────────────────────────────────────────────


class TestBuildDecisionContext:
    def test_basic_long(self):
        dc = build_decision_context(
            intent_id="test-001",
            source_skill="test-strategy",
            direction="long",
            conviction=4,
            summary="test trade",
            entry_price=100.0,
            stop=90.0,
            tp1=120.0,
            tp2=130.0,
            tp3=140.0,
        )
        assert dc["intent_id"] == "test-001"
        assert dc["source_skill"] == "test-strategy"
        assert dc["l3_idea"]["direction"] == "long"
        assert dc["l3_idea"]["conviction"] == 4
        assert dc["l3_idea"]["summary"] == "test trade"
        assert dc["l3_idea"]["entry_price"] == 100.0
        assert dc["l3_idea"]["stop"] == 90.0
        assert dc["l3_idea"]["tp1"] == 120.0
        assert dc["l3_idea"]["tp2"] == 130.0
        assert dc["l3_idea"]["tp3"] == 140.0
        assert dc["l3_idea"]["rr_to_tp2"] == 3.0
        assert dc["captured_at"] is not None

    def test_short_rr(self):
        dc = build_decision_context(
            intent_id="test-002",
            source_skill="test-strategy",
            direction="short",
            conviction=3,
            summary="test short",
            entry_price=100.0,
            stop=110.0,
            tp1=90.0,
            tp2=80.0,
            tp3=70.0,
        )
        assert dc["l3_idea"]["rr_to_tp2"] == 2.0

    def test_none_fields_default_to_none(self):
        dc = build_decision_context(
            intent_id="test-003",
            source_skill="test-strategy",
            direction=None,
            conviction=None,
            summary=None,
            entry_price=None,
            stop=None,
            tp1=None,
            tp2=None,
            tp3=None,
        )
        assert dc["l3_idea"]["direction"] == "unknown"
        assert dc["l3_idea"]["conviction"] is None
        assert dc["l3_idea"]["rr_to_tp2"] is None
        assert dc["regime"]["label"] is None
        assert dc["risk_verdict"]["status"] is None
        assert dc["risk_verdict"]["concerns"] == []
        assert dc["override"]["from_suggestion"] is False

    def test_risk_verdict_populated(self):
        dc = build_decision_context(
            intent_id="test-004",
            source_skill="test-strategy",
            direction="long",
            conviction=5,
            summary="",
            entry_price=50.0,
            stop=45.0,
            tp1=60.0,
            tp2=65.0,
            tp3=70.0,
            risk_status="APPROVED",
            risk_position_size_pct=15.0,
            risk_concerns=["low volume"],
        )
        assert dc["risk_verdict"]["status"] == "APPROVED"
        assert dc["risk_verdict"]["position_size_pct"] == 15.0
        assert dc["risk_verdict"]["concerns"] == ["low volume"]

    def test_override_flag(self):
        dc = build_decision_context(
            intent_id="test-005",
            source_skill="test-strategy",
            direction="long",
            conviction=4,
            summary="",
            entry_price=50.0,
            stop=45.0,
            tp1=60.0,
            tp2=65.0,
            tp3=70.0,
            override_from_suggestion=True,
            override_field="stop",
            override_reason="tightened stop per risk",
        )
        assert dc["override"]["from_suggestion"] is True
        assert dc["override"]["field"] == "stop"
        assert dc["override"]["reason"] == "tightened stop per risk"

    def test_regime_populated(self):
        dc = build_decision_context(
            intent_id="test-006",
            source_skill="test-strategy",
            direction="long",
            conviction=3,
            summary="",
            entry_price=50.0,
            stop=45.0,
            tp1=60.0,
            tp2=65.0,
            tp3=70.0,
            regime_label="risk_on",
            regime_fng=65.0,
            regime_btc_dominance=45.2,
            regime_divergence="accumulation",
            macro_signals=["fng_greed", "btc_above_ema21"],
        )
        assert dc["regime"]["label"] == "risk_on"
        assert dc["regime"]["fng"] == 65.0
        assert dc["regime"]["btc_dominance"] == 45.2
        assert dc["regime"]["divergence"] == "accumulation"
        assert dc["macro_signals"] == ["fng_greed", "btc_above_ema21"]


# ── build_decision_context_from_idea ────────────────────────────────────


class TestBuildDecisionContextFromIdea:
    def test_from_l3_idea_dict(self):
        idea = {
            "direction": "short",
            "conviction": 2,
            "summary": "weak short",
            "entry_price": 200.0,
            "stop_loss": 210.0,
            "take_profit": [190.0, 180.0, 170.0],
        }
        dc = build_decision_context_from_idea(
            intent_id="idea-001",
            source_skill="test-l3",
            idea=idea,
        )
        assert dc["l3_idea"]["direction"] == "short"
        assert dc["l3_idea"]["entry_price"] == 200.0
        assert dc["l3_idea"]["stop"] == 210.0
        assert dc["l3_idea"]["tp1"] == 190.0
        assert dc["l3_idea"]["tp2"] == 180.0
        assert dc["l3_idea"]["tp3"] == 170.0
        assert dc["l3_idea"]["rr_to_tp2"] == 2.0

    def test_from_idea_with_optional_fields(self):
        idea = {"direction": "long", "conviction": 3, "entry_price": 10.0, "stop": 9.0, "take_profit": [12.0]}
        dc = build_decision_context_from_idea(
            intent_id="idea-002",
            source_skill="test-l3",
            idea=idea,
            regime_label="fear_recovery",
            risk_status="CONCERN",
            macro_signals=["volatility_high"],
        )
        assert dc["regime"]["label"] == "fear_recovery"
        assert dc["risk_verdict"]["status"] == "CONCERN"
        # stop_loss not in idea, stop is used
        assert dc["l3_idea"]["stop"] == 9.0

    def test_from_empty_idea(self):
        dc = build_decision_context_from_idea(
            intent_id="idea-003",
            source_skill="test-l3",
            idea={},
        )
        assert dc["l3_idea"]["direction"] == "unknown"
        assert dc["l3_idea"]["conviction"] is None
        assert dc["l3_idea"]["entry_price"] is None
        assert dc["l3_idea"]["stop"] is None
        assert dc["l3_idea"]["tp1"] is None
        assert dc["l3_idea"]["rr_to_tp2"] is None


# ── validate_decision_context ───────────────────────────────────────────


class TestValidateDecisionContext:
    def test_valid(self):
        dc = build_decision_context(
            intent_id="valid-001",
            source_skill="test",
            direction="long",
            conviction=4,
            summary="test",
            entry_price=100.0,
            stop=90.0,
            tp1=120.0,
            tp2=130.0,
            tp3=140.0,
        )
        assert validate_decision_context(dc) == []

    def test_not_a_dict(self):
        issues = validate_decision_context("not a dict")
        assert any("must be a dict" in i for i in issues)

    def test_missing_intent_id(self):
        issues = validate_decision_context({"intent_id": 123, "source_skill": "x", "captured_at": "now"})
        assert any("intent_id must be a string" in i for i in issues)

    def test_bad_direction(self):
        dc = build_decision_context(
            intent_id="bad-dir",
            source_skill="test",
            direction="sideways",  # invalid
            conviction=None,
            summary=None,
            entry_price=None,
            stop=None,
            tp1=None,
            tp2=None,
            tp3=None,
        )
        issues = validate_decision_context(dc)
        assert any("direction must be long/short/unknown" in i for i in issues)

    def test_buy_raw_side_fails_validation(self):
        """Regression: execution libs used to pass side_raw as direction.
        "buy"/"sell" must be caught as invalid by the validator."""
        dc = build_decision_context(
            intent_id="raw-side",
            source_skill="test",
            direction="buy",  # raw Kraken side — not canonical
            conviction=None,
            summary=None,
            entry_price=None,
            stop=None,
            tp1=None,
            tp2=None,
            tp3=None,
        )
        issues = validate_decision_context(dc)
        assert any("direction must be long/short/unknown" in i for i in issues)

    def test_bad_risk_status(self):
        dc = build_decision_context(
            intent_id="bad-risk",
            source_skill="test",
            direction="long",
            conviction=None,
            summary=None,
            entry_price=None,
            stop=None,
            tp1=None,
            tp2=None,
            tp3=None,
            risk_status="INVALID_STATUS",
        )
        issues = validate_decision_context(dc)
        assert any("risk_verdict.status" in i for i in issues)

    def test_missing_override_from_suggestion(self):
        issues = validate_decision_context(
            {
                "intent_id": "x",
                "source_skill": "x",
                "captured_at": "now",
                "l3_idea": {"direction": "long"},
                "regime": {},
                "risk_verdict": {"status": None, "concerns": []},
                "override": {"field": None, "reason": None},
            }
        )
        assert any("from_suggestion must be a bool" in i for i in issues)
