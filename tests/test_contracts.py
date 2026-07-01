"""Tests for analysis/contracts.py sanity helpers."""

import pytest

from analysis.contracts import (
    compute_rr_to_tp,
    conviction_version,
    l2_classification,
    l2_fired,
    validate_l3_tp_ladder,
)


class TestL2Fired:
    def test_present_true_with_classification(self):
        assert l2_fired({"pattern": {"present": True, "classification": "HEALTHY_UPTREND"}}) is True

    def test_present_true_without_classification_is_silent(self):
        """Pattern B: present=True but classification=None must NOT count as fired.

        L3 strategies should treat this as no-signal so a half-formed L2 verdict
        can't leak through.
        """
        assert l2_fired({"pattern": {"present": True, "classification": None}}) is False

    def test_present_false_with_classification_is_ghost(self):
        """Ghost-classification (present=False, classification populated) must NOT
        count as fired. The market-trend-quality classifier ensures classification
        stays None in this case; this helper enforces the invariant at the read
        site too.
        """
        assert l2_fired({"pattern": {"present": False, "classification": "BULLISH_LOW"}}) is False

    def test_present_false_with_no_classification(self):
        assert l2_fired({"pattern": {"present": False, "classification": None}}) is False

    def test_empty_pattern(self):
        assert l2_fired({"pattern": {}}) is False

    def test_no_pattern_key(self):
        assert l2_fired({}) is False

    def test_none_input(self):
        assert l2_fired(None) is False

    def test_non_dict_input(self):
        assert l2_fired("not a dict") is False
        assert l2_fired(42) is False


class TestL2Classification:
    def test_returns_classification_when_fired(self):
        result = l2_classification({"pattern": {"present": True, "classification": "HEALTHY_UPTREND"}})
        assert result == "HEALTHY_UPTREND"

    def test_returns_none_when_ghost(self):
        result = l2_classification({"pattern": {"present": False, "classification": "BULLISH_LOW"}})
        assert result is None

    def test_returns_none_when_silent(self):
        result = l2_classification({"pattern": {"present": True, "classification": None}})
        assert result is None


class TestValidateL3TpLadder:
    def test_long_valid_ladder(self):
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 100.0,
            "take_profit": [115.0, 120.0, 130.0],
        }
        validate_l3_tp_ladder(idea)  # should not raise

    def test_long_degenerate_tp3_raises(self):
        """TP3 within the 5% dead zone (entry < TP3 < entry × 1.05) produces
        degenerate R:R ≈ entry. Validator must reject."""
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 100.0,
            "take_profit": [101.0, 102.0, 103.0],  # TP3 = 103 < entry × 1.05 = 105
        }
        with pytest.raises(ValueError, match="TP3 must be ≥ entry"):
            validate_l3_tp_ladder(idea)

    def test_long_tp_below_entry_raises(self):
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 100.0,
            "take_profit": [95.0, 110.0, 120.0],
        }
        with pytest.raises(ValueError, match="take_profit must all be > entry"):
            validate_l3_tp_ladder(idea)

    def test_short_valid_ladder(self):
        idea = {
            "pair": "TEST",
            "direction": "short",
            "entry_price": 100.0,
            "take_profit": [85.0, 80.0, 70.0],
        }
        validate_l3_tp_ladder(idea)

    def test_short_degenerate_tp3_raises(self):
        """Mirror case for short — TP3 within the 5% dead zone."""
        idea = {
            "pair": "TEST",
            "direction": "short",
            "entry_price": 100.0,
            "take_profit": [99.0, 98.0, 97.0],  # TP3 = 97 > entry × 0.95 = 95
        }
        with pytest.raises(ValueError, match="TP3 must be ≤ entry"):
            validate_l3_tp_ladder(idea)

    def test_short_tp_above_entry_raises(self):
        idea = {
            "pair": "TEST",
            "direction": "short",
            "entry_price": 100.0,
            "take_profit": [105.0, 90.0, 80.0],
        }
        with pytest.raises(ValueError, match="take_profit must all be < entry"):
            validate_l3_tp_ladder(idea)

    def test_empty_tps_skipped(self):
        idea = {"pair": "TEST", "direction": "long", "entry_price": 100.0, "take_profit": []}
        validate_l3_tp_ladder(idea)  # should not raise

    def test_none_entry_skipped(self):
        idea = {"pair": "TEST", "direction": "long", "entry_price": None, "take_profit": [110.0]}
        validate_l3_tp_ladder(idea)  # should not raise

    def test_long_non_monotonic_tps_raises(self):
        """A long ladder must be strictly ascending.

        Out-of-order TPs defeat the staggered-exit purpose of the 3-element ladder
        and indicate upstream rounding or arithmetic bug, not an intentional setup.
        """
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 100.0,
            "take_profit": [110.0, 105.0, 120.0],
        }
        with pytest.raises(ValueError, match="strictly ascending"):
            validate_l3_tp_ladder(idea)

    def test_short_non_monotonic_tps_raises(self):
        idea = {
            "pair": "TEST",
            "direction": "short",
            "entry_price": 100.0,
            "take_profit": [85.0, 90.0, 70.0],
        }
        with pytest.raises(ValueError, match="strictly descending"):
            validate_l3_tp_ladder(idea)

    def test_long_all_equal_tps_raises(self):
        """When sub-$1 prices round to a single 2-decimal value,
        TPs=[0.08,0.08,0.08] would defeat the ladder. For a LONG setup the
        entry-side check catches it first (TPs below entry), but either guard
        is correct — what matters is that the degenerate shape is rejected,
        not silently emitted to the cron pipeline.
        """
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 0.09,
            "take_profit": [0.08, 0.08, 0.08],
        }
        with pytest.raises(ValueError):
            validate_l3_tp_ladder(idea)

    def test_short_all_equal_tps_raises(self):
        """Mirror case for short — entry=0.09, TPs=[0.08,0.08,0.08] is the
        canonical degenerate shape (sub-$1 rounding). Must raise on
        distinctness (and also on strict-descending monotonicity).
        """
        idea = {
            "pair": "ALGOUSD",
            "direction": "short",
            "entry_price": 0.09,
            "take_profit": [0.08, 0.08, 0.08],
        }
        with pytest.raises(ValueError, match="(strictly descending|distinct)"):
            validate_l3_tp_ladder(idea)

    def test_long_partial_tie_tps_raises(self):
        """TP1 == TP2 is also degenerate — at least one duplicate means the
        ladder can't actually stagger exits into 3 distinct bands."""
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 100.0,
            "take_profit": [110.0, 110.0, 120.0],
        }
        with pytest.raises(ValueError, match="distinct"):
            validate_l3_tp_ladder(idea)

    def test_short_partial_tie_tps_raises(self):
        idea = {
            "pair": "TEST",
            "direction": "short",
            "entry_price": 100.0,
            "take_profit": [85.0, 85.0, 70.0],
        }
        with pytest.raises(ValueError, match="distinct"):
            validate_l3_tp_ladder(idea)

    def test_long_zero_stop_raises(self):
        """stop_loss == entry_price is a zero stop — a market-order-equivalent
        with no downside protection. The validator must reject regardless of
        whether the TP ladder itself is well-formed.
        """
        idea = {
            "pair": "ALGOUSD",
            "direction": "long",
            "entry_price": 0.09,
            "stop_loss": 0.09,
            "take_profit": [0.10, 0.11, 0.12],
        }
        with pytest.raises(ValueError, match="stop_loss must not equal entry_price"):
            validate_l3_tp_ladder(idea)

    def test_short_zero_stop_raises(self):
        """Mirror case for short — the actual ALGOUSD 4h SHORT regression shape."""
        idea = {
            "pair": "ALGOUSD",
            "direction": "short",
            "entry_price": 0.09,
            "stop_loss": 0.09,
            "take_profit": [0.08, 0.07, 0.06],
        }
        with pytest.raises(ValueError, match="stop_loss must not equal entry_price"):
            validate_l3_tp_ladder(idea)

    def test_zero_stop_full_repro_raises(self):
        """ALGOUSD 4h SHORT repro: all-equal TPs AND zero stop. The validator
        rejects at the zero-stop gate before the distinctness check.
        """
        idea = {
            "pair": "ALGOUSD",
            "direction": "short",
            "entry_price": 0.09,
            "stop_loss": 0.09,
            "take_profit": [0.08, 0.08, 0.08],
        }
        with pytest.raises(ValueError, match="stop_loss must not equal entry_price"):
            validate_l3_tp_ladder(idea)

    def test_none_stop_skipped(self):
        """A market-order-style idea with no stop is the L3 strategy's
        call, not the validator's — skip the stop check when not set."""
        idea = {
            "pair": "TEST",
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": None,
            "take_profit": [115.0, 120.0, 130.0],
        }
        validate_l3_tp_ladder(idea)  # should not raise

    def test_zero_stop_with_high_value_also_raises(self):
        """Defence-in-depth: zero-stop rejection must not depend on price tier."""
        idea = {
            "pair": "BTCUSD",
            "direction": "long",
            "entry_price": 100000.0,
            "stop_loss": 100000.0,
            "take_profit": [105000.0, 110000.0, 120000.0],
        }
        with pytest.raises(ValueError, match="stop_loss must not equal entry_price"):
            validate_l3_tp_ladder(idea)


class TestConvictionVersion:
    def test_version_matches_conviction(self):
        assert conviction_version(1) == "v1"
        assert conviction_version(2) == "v2"
        assert conviction_version(3) == "v3"
        assert conviction_version(4) == "v4"
        assert conviction_version(5) == "v5"

    def test_version_clips_out_of_range(self):
        assert conviction_version(0) == "v1"
        assert conviction_version(6) == "v5"
        assert conviction_version(-10) == "v1"


class TestComputeRrToTp:
    """Precomputed R:R to each TP level on L3Idea.

    The helper is the single source of truth for the direction-asymmetric
    formula; L3 strategies call it from their post-build loop and emit
    ``rr_to_tp: [rr_to_tp1, rr_to_tp2, rr_to_tp3]`` on every idea. Consumers
    (swing-scan, position-watchdog, paper-trader, LLM agent brain) read the
    field directly instead of reimplementing the formula per-strategy.
    """

    def test_long_1p5_2p5_4_ladder(self):
        """trend-follow / breakout-confirm canonical ladder: 1.5R / 2.5R / 4R."""
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 90.0,  # risk = 10
            "take_profit": [115.0, 125.0, 140.0],
            "take_profit_ideal": [115.0, 125.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == [1.5, 2.5, 4.0]

    def test_short_1_2_3_ladder(self):
        """exhaustion-fade canonical ladder: 1R / 2R / 3R (descending for short)."""
        idea = {
            "direction": "short",
            "entry_price": 100.0,
            "stop_loss": 110.0,  # risk = 10
            "take_profit": [90.0, 80.0, 70.0],
            "take_profit_ideal": [90.0, 80.0, 70.0],
        }
        assert compute_rr_to_tp(idea) == [1.0, 2.0, 3.0]

    def test_long_2_3_4_ladder(self):
        """accumulation-swing / liquidity-sweep canonical ladder: 2R / 3R / 4R."""
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": [120.0, 130.0, 140.0],
            "take_profit_ideal": [120.0, 130.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == [2.0, 3.0, 4.0]

    def test_long_2_3_5_ladder(self):
        """accumulation-swing specific: 2R / 3R / 5R (wider TP3)."""
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": [120.0, 130.0, 150.0],
            "take_profit_ideal": [120.0, 130.0, 150.0],
        }
        assert compute_rr_to_tp(idea) == [2.0, 3.0, 5.0]

    def test_prefers_take_profit_ideal_over_take_profit(self):
        """On sub-$1 setups, ``take_profit`` (2dp display) drifts below the
        unrounded construction; the helper must read ``take_profit_ideal``
        first so consumers get the canonical R:R. ALGO 4h SHORT 2026-06-25
        regression shape.
        """
        idea = {
            "direction": "long",
            "entry_price": 0.09,
            "stop_loss": 0.08,
            "take_profit": [0.10, 0.11, 0.12],
            "take_profit_ideal": [0.105, 0.11, 0.12],  # TP1 = 0.105 ideal, 0.10 display
        }
        # ideal: (0.105-0.09)/0.01 = 1.5  ; display would give 1.0
        assert compute_rr_to_tp(idea) == [1.5, 2.0, 3.0]

    def test_falls_back_to_take_profit_when_no_ideal(self):
        """Strategies that don't populate ``take_profit_ideal`` (none right
        now, but kept as a graceful fallback) should still get sensible
        R:R from the 2dp display values.
        """
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": [115.0, 125.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == [1.5, 2.5, 4.0]

    def test_empty_tps_returns_empty(self):
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": [],
        }
        assert compute_rr_to_tp(idea) == []

    def test_missing_entry_returns_empty(self):
        """Defensive: degenerate prices are ``validate_l3_tp_ladder``'s job,
        not this helper's. The helper returns ``[]`` for any non-computable
        shape so the L3 strategy post-build loop can call it blindly.
        """
        idea = {
            "direction": "long",
            "entry_price": None,
            "stop_loss": 90.0,
            "take_profit": [115.0, 125.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == []

    def test_missing_stop_returns_empty(self):
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": None,
            "take_profit": [115.0, 125.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == []

    def test_zero_risk_long_returns_empty(self):
        """Defensive: entry == stop is a zero stop (validator rejects it
        separately). Helper returns ``[]`` so the L3 emit doesn't carry a
        meaningless R:R of infinity.
        """
        idea = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_loss": 100.0,
            "take_profit": [115.0, 125.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == []

    def test_zero_risk_short_returns_empty(self):
        idea = {
            "direction": "short",
            "entry_price": 100.0,
            "stop_loss": 100.0,
            "take_profit": [85.0, 80.0, 70.0],
        }
        assert compute_rr_to_tp(idea) == []

    def test_long_default_direction_when_missing(self):
        """A misformed idea without ``direction`` key should not crash; the
        helper falls back to the long formula. The L3 contract always
        carries direction, but consumers reading ad-hoc dicts (LLM
        agent brain) should not hit an exception.
        """
        idea = {
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": [115.0, 125.0, 140.0],
        }
        assert compute_rr_to_tp(idea) == [1.5, 2.5, 4.0]
        assert conviction_version(100) == "v5"
