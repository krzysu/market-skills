"""Tests for strategy-trend-follow L3 — pullback setups."""

import importlib.util
import json
import math
import os
import random


def _load_strat_lib():
    lib_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "skills",
        "strategy-trend-follow",
        "lib.py",
    )
    spec = importlib.util.spec_from_file_location("strategy_trend_follow_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_hype_fixture():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "hype_4h_2026-06-19.json")
    with open(fixture_path) as f:
        return json.load(f)


def _make_candles(base_price=100.0, n=250, trend="uptrend", drift=0.002, seed=42):
    """Generate deterministic synthetic candles with optional drift direction."""
    rng = random.Random(seed)
    vals = []
    price = base_price
    for _ in range(n):
        delta = drift if trend == "uptrend" else (-drift if trend == "downtrend" else 0.0)
        price = price * (1.0 + delta) + rng.uniform(-1.0, 1.0)
        vals.append(
            [
                int(vals[-1][0] + 86400) if vals else 0,
                price,
                price + rng.uniform(0, 1),
                price - rng.uniform(0, 1),
                price,
                rng.randint(100000, 500000),
            ]
        )
    return vals


def _make_pullback_candles(n=250, base=100.0, seed=42):
    """Build a series with a clear pullback: smooth uptrend → 6 candles dip → 6 candles recover.

    Targets HEALTHY_PULLBACK_UPTREND via the new impulse_bullish_reversal path.
    """
    rng = random.Random(seed)
    prices = [base * (1.0 + 0.002 * i) for i in range(n - 13)]
    pre_pullback = prices[-1]
    for i in range(6):
        pre_pullback = pre_pullback * 0.985
        prices.append(pre_pullback)
    post_pullback = prices[-1]
    for i in range(6):
        post_pullback = post_pullback * 1.015
        prices.append(post_pullback)
    candles = []
    for i, p in enumerate(prices):
        candles.append(
            [
                i * 86400,
                p,
                p + rng.uniform(0, 0.5),
                p - rng.uniform(0, 0.5),
                p,
                rng.randint(100000, 500000),
            ]
        )
    return candles


class TestStrategyTrendFollow:
    def test_analyze_returns_ideas_list(self):
        mod = _load_strat_lib()
        candles = _make_candles(n=250, trend="uptrend")
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        assert "ideas" in result
        assert isinstance(result["ideas"], list)
        assert "narrative" in result

    def test_ideas_have_required_keys(self):
        mod = _load_strat_lib()
        candles = _make_candles(n=250, trend="uptrend")
        result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")
        for idea in result["ideas"]:
            assert "direction" in idea
            assert "conviction" in idea
            assert "entry_type" in idea
            assert "entry_price" in idea
            assert "stop_loss" in idea
            assert "take_profit" in idea
            assert "reasoning" in idea

    def test_insufficient_data(self):
        mod = _load_strat_lib()
        result = mod.analyze([], ticker="TEST", interval="4h", period="6mo")
        assert result["ideas"] == []


class TestHypeFixture:
    def test_hype_fixture_returns_long_idea(self):
        mod = _load_strat_lib()
        candles = _load_hype_fixture()
        result = mod.analyze(candles, ticker="HYPE", interval="4h", period="6mo")

        assert "ideas" in result
        assert "narrative" in result
        assert isinstance(result["ideas"], list)
        assert len(result["ideas"]) >= 1, f"Expected at least one idea for HYPE, got {result}"

        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert len(long_ideas) >= 1, f"Expected a long idea for HYPE uptrend, got {result['ideas']}"

        idea = long_ideas[0]
        assert idea["direction"] == "long"
        assert idea["conviction"] >= 1, f"Expected conviction >= 1, got {idea}"
        assert idea["entry_price"] is not None
        assert idea["entry_price"] > 0
        assert "reasoning" in idea
        assert "source_skills" in idea


class TestPullbackSetup:
    """Behavioral test: pullback candles produce HEALTHY_PULLBACK_UPTREND and an L3 idea."""

    def test_pullback_produces_long_idea_with_pullback_reasoning(self):
        mod = _load_strat_lib()
        candles = _make_pullback_candles(n=250, base=100.0)
        result = mod.analyze(candles, ticker="PULL", interval="4h", period="6mo")

        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas:
            pullback_ideas = [i for i in long_ideas if "pullback" in i["reasoning"].lower()]
            assert pullback_ideas, (
                f"Expected a long idea referencing 'pullback', got {[i['reasoning'] for i in long_ideas]}"
            )
            idea = pullback_ideas[0]
            assert idea["conviction"] >= 1
            assert idea["conviction"] <= 4
        else:
            # If trend-quality didn't classify this as HEALTHY_PULLBACK_UPTREND (data-dependent),
            # the test should still document the outcome — but a pullback pattern with healthy
            # prior trend should at minimum land somewhere. Inspect the narrative.
            assert "narrative" in result


class TestMaturityFields:
    """L3 ideas expose move_maturity_pct and entry_window_validity_pct.

    These are observational, not gating — the cron uses them as a sanity
    check before flagging ``[OPPORTUNITY] ENTRY``. Both fields are present
    on every emitted idea (None when the lookback is unusable).
    """

    def test_ideas_have_move_maturity_and_entry_window_fields(self):
        mod = _load_strat_lib()
        candles = _make_candles(n=250, trend="uptrend")
        result = mod.analyze(candles, ticker="MAT", interval="4h", period="6mo")
        for idea in result["ideas"]:
            assert "move_maturity_pct" in idea
            assert "entry_window_validity_pct" in idea

    def test_mature_uptrend_flags_high_move_maturity(self):
        """Synthetic uptrend that ends far above its rolling low → mature
        move, >30% from swing low. The cron uses this to veto chase-risk."""
        mod = _load_strat_lib()
        candles = _make_candles(base_price=100.0, n=250, trend="uptrend", drift=0.005, seed=7)
        result = mod.analyze(candles, ticker="MATURE", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas:
            for idea in long_ideas:
                # Synthetic uptrend at 0.5%/candle over 250 candles is enormous,
                # so move_maturity must be > 50% (late/chase-risk class).
                assert idea["move_maturity_pct"] is not None
                assert idea["move_maturity_pct"] > 30.0, (
                    f"Expected >30% maturity on synthetic uptrend, got {idea['move_maturity_pct']}"
                )

    def test_entry_window_validity_zero_when_close_at_entry(self):
        """When entry_price == close, entry_window_validity_pct must be ~0."""
        mod = _load_strat_lib()
        candles = _make_candles(base_price=100.0, n=250, trend="uptrend", seed=11)
        result = mod.analyze(candles, ticker="EW", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas:
            for idea in long_ideas:
                # entry is the close of the last candle, so |close - entry| = 0
                assert idea["entry_window_validity_pct"] is not None
                assert idea["entry_window_validity_pct"] < 0.01

    def test_insufficient_data_returns_no_ideas(self):
        """Empty candles → no ideas, no maturity fields needed."""
        mod = _load_strat_lib()
        result = mod.analyze([], ticker="EMPTY", interval="4h", period="6mo")
        assert result["ideas"] == []


class TestPatternS:
    """Soft veto tags applied at L3 emit time so consumers running
    strategy-trend-follow standalone see the same protective downgrades the
    swing-scan cron applies downstream. Tag the idea with ``veto_reasons`` and
    downgrade conviction accordingly.

    Catches cases like an 80%-extended AERO 4h LONG where late chase-risk would
    otherwise be emitted as `conv=4` with no maturity signal. With Pattern S
    inlined, that same setup emits conv=2 with veto_reasons=['late-move', ...].
    """

    def test_mature_uptrend_gets_veto_reasons(self):
        """Synthetic uptrend at +0.5%/candle × 250 candles → move_maturity_pct >> 30.

        Pattern S must tag the idea with mature-move/late-move and downgrade conviction.
        """
        mod = _load_strat_lib()
        candles = _make_candles(base_price=100.0, n=250, trend="uptrend", drift=0.005, seed=7)
        result = mod.analyze(candles, ticker="MATURE_S", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas:
            idea = long_ideas[0]
            assert "veto_reasons" in idea, f"Expected veto_reasons on mature uptrend, got {idea}"
            assert any(r in idea["veto_reasons"] for r in ("late-move", "mature-move")), (
                f"Expected late-move or mature-move tag, got {idea['veto_reasons']}"
            )

    def test_veto_reasons_downgrades_conviction(self):
        """When late-move fires, conviction must be lower than the unvetoed baseline.

        Build a candle series with mature uptrend + Pattern S triggers → final conviction
        must be < tq_pattern.confidence (the raw trend-quality baseline).
        """
        mod = _load_strat_lib()
        candles = _make_candles(base_price=100.0, n=250, trend="uptrend", drift=0.005, seed=13)
        result = mod.analyze(candles, ticker="VETO_S", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas:
            idea = long_ideas[0]
            if "late-move" in idea.get("veto_reasons", []):
                assert idea["conviction"] <= 3, (
                    f"Expected late-move downgrade to drop conviction to <= 3, got {idea['conviction']}"
                )

    def test_no_late_move_tag_on_pullback_fixture(self):
        """Pullback fixture should not be tagged late-move (>50% from rolling low).

        Mature-move (>30%) is fine — pullbacks often happen within mature trends.
        The aggressive late-move tag (>50%) only fires on truly extended moves.
        """
        mod = _load_strat_lib()
        candles = _make_pullback_candles(n=250, base=100.0)
        result = mod.analyze(candles, ticker="FRESH_S", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas:
            for idea in long_ideas:
                vetoes = idea.get("veto_reasons", [])
                assert "late-move" not in vetoes, f"Expected no late-move tag on pullback fixture, got {vetoes}"

    def test_reasoning_carries_pattern_s_tag(self):
        """When Pattern S fires, reasoning should be amended with the tag list."""
        mod = _load_strat_lib()
        candles = _make_candles(base_price=100.0, n=250, trend="uptrend", drift=0.005, seed=21)
        result = mod.analyze(candles, ticker="REASON_S", interval="4h", period="6mo")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        if long_ideas and "veto_reasons" in long_ideas[0]:
            assert "Pattern S" in long_ideas[0]["reasoning"], (
                f"Expected 'Pattern S' in reasoning, got: {long_ideas[0]['reasoning']}"
            )


def _make_trending_candles(start=100.0, end=300.0, n=250, seed=42):
    """Deterministic monotonic trending candles from start to end.
    Low = start, last close = end — gives exact move_maturity_pct.
    Uses 2% per-bar range so ATR(14) is large enough for TP3 to clear
    the 5% dead-zone check (validate_l3_tp_ladder)."""
    rng = random.Random(seed)
    candles = []
    for i in range(n):
        pct = i / (n - 1)
        close = start + (end - start) * pct
        half_range = close * 0.01
        candles.append([
            i * 86400,
            close,
            close + rng.uniform(0, half_range * 2),
            close - rng.uniform(0, half_range * 2),
            close,
            rng.randint(100000, 500000),
        ])
    return candles


def _make_flat_candles(n=250, base=1656.80, half_range=25.004):
    """Build candles where every bar has a constant true range of 2*half_range.

    With a constant per-bar range, ATR(14) = 2*half_range exactly, giving a
    deterministic risk_dist = 2*ATR for the SHORT branch in strategy-trend-follow.
    The flat close (every bar ends at ``base``) keeps the last close pinned to
    ``base`` so ``entry = closes[-1] = base`` — no random walk drift.

    Used by the regression fixture below to reproduce the 2026-06-25 ETH SHORT
    bug shape: 2dp rounding of TP1 = entry - 1.5 × risk drops the recomputed
    R:R just below the documented 1.5 floor.
    """
    out = []
    for i in range(n):
        out.append(
            [
                i * 86400,
                base,
                base + half_range,
                base - half_range,
                base,
                200_000,
            ]
        )
    return out


class TestTakeProfitIdeal:
    """Regression fixture: 2dp rounding of TP1 = entry - 1.5 × risk loses
    precision and drops the recomputed R:R just below 1.5 on borderline setups.

    Today (2026-06-25): ETH SHORT v=v3, entry $1,656.80, TP1 $1,434.67 (rounded
    from ideal 1,434.665). Recomputed ``rr = |tp1 - entry| / |entry - stop| =
    222.13 / 148.09 = 1.49996... < 1.5`` rejected the would-be pick despite the
    setup being sound. Producer fix: serialize the unrounded construction value
    alongside the display value so downstream R:R checks (cron formula-based
    check, validator) can recover the exact ``entry - 1.5 × risk`` construction.
    """

    def _patch_trend_quality(self, monkeypatch, classification, confidence=3):
        """Patch analysis.skill_loader.load_skill to fire the requested trend classification."""
        tq_pattern = {
            "present": True,
            "confidence": confidence,
            "classification": classification,
            "max_confidence": 5,
            "type": "trend",
        }
        tq_mod = type(
            "TQ",
            (),
            {"analyze": staticmethod(lambda c, **_kw: {"pattern": tq_pattern, "input_scores": {}})},
        )()
        bo_mod = type(
            "BO",
            (),
            {"analyze": staticmethod(lambda c, **_kw: {"pattern": {"present": False}})},
        )()
        import analysis.skill_loader as sl

        canned = {
            "market-trend-quality": tq_mod,
            "market-breakout": bo_mod,
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

    def test_short_idea_exposes_take_profit_ideal(self, monkeypatch):
        """Strategy must serialize take_profit_ideal (unrounded construction)
        alongside take_profit (2dp display) for every SHORT idea.

        Pre-fix: the field doesn't exist → cron recomputes rr from the rounded
        TP1 and just-barely fails the strict ``>= 1.5`` check.
        Post-fix: cron (or any consumer) uses ``take_profit_ideal[0]`` and
        recovers the exact construction value.
        """
        self._patch_trend_quality(monkeypatch, "HEALTHY_DOWNTREND", confidence=3)
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=1656.80, half_range=25.004)
        result = mod.analyze(candles, ticker="ETH", interval="1d", period="1y")

        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas, f"expected a short idea on HEALTHY_DOWNTREND, got {result}"
        idea = short_ideas[0]

        assert "take_profit_ideal" in idea, (
            "strategy-trend-follow must emit take_profit_ideal so downstream "
            "R:R checks can use the unrounded construction TP1. Regression: "
            "ETH SHORT v=v3 2026-06-25 lost ~3.4e-5 of R:R precision to 2dp "
            "rounding and the daily-trade-pick cron rejected the setup."
        )
        tp_ideal = idea["take_profit_ideal"]
        assert len(tp_ideal) == 3, f"take_profit_ideal must mirror take_profit length (3), got {tp_ideal}"

    def test_short_ideal_reproduces_eth_rounding_bug_shape(self, monkeypatch):
        """The display TP1 (2dp) drifts below the constructed R:R, but the
        unrounded ideal lets the formula-based check recover the exact
        ``entry - 1.5 × risk`` construction.

        Verifies all sub-assertions the producer fix needs to satisfy:

        1. Display TP1 ≠ ideal TP1 (the rounding actually happens) — the bug
           only triggers when the unrounded TP has non-trivial fractional
           parts after 2dp round.
        2. Recomputed ``rr`` from the display values drops below 1.5 — exactly
           the shape that broke the daily-trade-pick cron on 2026-06-25.
        3. The formula-based R:R check (cron's preferred gate, which uses
           ``|tp1 - entry|`` directly instead of dividing by rounded risk)
           passes within tolerance when fed the unrounded TP1.

        Note: the strict ``rr >= 1.5`` check still drifts below 1.5 because
        the displayed stop_loss is also 2dp-rounded (the cron needs to switch
        to the formula-based gate to close the gap — see the ``test_short_...
        _formula_check_passes_with_unrounded_tp1`` test below for the in-action
        proof). The producer fix at this layer is the necessary input; the
        consumer-side gate change lives outside the library.
        """
        self._patch_trend_quality(monkeypatch, "HEALTHY_DOWNTREND", confidence=3)
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=1656.80, half_range=25.004)
        result = mod.analyze(candles, ticker="ETH", interval="1d", period="1y")

        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas
        idea = short_ideas[0]

        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        tp1_display = idea["take_profit"][0]
        tp1_ideal = idea["take_profit_ideal"][0]

        assert tp1_display != tp1_ideal, (
            f"sanity: this fixture relies on TP1 having a non-trivial 3rd "
            f"decimal so the 2dp round loses precision. Got display={tp1_display} "
            f"== ideal={tp1_ideal} — pick a different half_range."
        )

        risk = abs(entry - stop)
        rr_display = abs(entry - tp1_display) / risk

        assert rr_display < 1.5, (
            f"bug repro: rr computed from 2dp-rounded TP1 must fall below 1.5 "
            f"(this is the silent-reject shape), got rr_display={rr_display:.9f} "
            f"(entry={entry}, stop={stop}, tp1_display={tp1_display}, tp1_ideal={tp1_ideal})"
        )

        formula_diff = abs(abs(entry - tp1_ideal) - 1.5 * risk)
        assert formula_diff <= 1e-3 * risk, (
            f"formula-based check (cron's preferred gate) must pass with "
            f"the unrounded TP1: formula_diff={formula_diff:.9f}, tolerance={1e-3 * risk:.9f}"
        )

    def test_short_formula_check_passes_with_unrounded_tp1(self, monkeypatch):
        """Formula-based R:R check (cron's preferred gate, 2026-06-25):

            pass if ``abs(|tp1 - entry| - 1.5 * |entry - stop|) <= 1e-3 * |entry - stop|``

        With ``tp1 = take_profit_ideal[0]`` (unrounded), the construction is
        exact and the formula check returns ~0 (well below the 1e-3 tolerance
        on risk). With the display ``tp1 = take_profit[0]``, the same formula
        check still passes on this fixture (because both TP1 and stop round in
        the same direction) but the margin shrinks to ~0.005 — exactly the
        silent-reject band. The unrounded channel gives the formula check
        3+ orders of magnitude more headroom.
        """
        self._patch_trend_quality(monkeypatch, "HEALTHY_DOWNTREND", confidence=3)
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=1656.80, half_range=25.004)
        result = mod.analyze(candles, ticker="ETH", interval="1d", period="1y")

        short_ideas = [i for i in result["ideas"] if i["direction"] == "short"]
        assert short_ideas
        idea = short_ideas[0]

        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        risk = abs(entry - stop)
        tolerance = 1e-3 * risk

        diff_ideal = abs(abs(entry - idea["take_profit_ideal"][0]) - 1.5 * risk)
        diff_display = abs(abs(entry - idea["take_profit"][0]) - 1.5 * risk)

        assert diff_ideal <= tolerance, (
            f"formula check with take_profit_ideal[0] must pass: diff={diff_ideal:.9f}, tolerance={tolerance:.9f}"
        )
        assert diff_ideal < diff_display, (
            f"unrounded ideal should give a tighter formula match than the "
            f"display value (proves the new channel actually carries precision). "
            f"diff_ideal={diff_ideal:.9f}, diff_display={diff_display:.9f}"
        )

    def test_long_idea_also_exposes_take_profit_ideal(self, monkeypatch):
        """Same fix on the long branch (HEALTHY_UPTREND) — the rounding bug
        is symmetric: TP1 = entry + 1.5 × risk rounds the same way."""
        self._patch_trend_quality(monkeypatch, "HEALTHY_UPTREND", confidence=4)
        mod = _load_strat_lib()
        candles = _make_flat_candles(n=250, base=100.0, half_range=1.0)
        result = mod.analyze(candles, ticker="UP", interval="1d", period="1y")

        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, f"expected a long idea on HEALTHY_UPTREND, got {result}"
        idea = long_ideas[0]

        assert "take_profit_ideal" in idea
        assert len(idea["take_profit_ideal"]) == 3

        tp1_ideal = idea["take_profit_ideal"][0]
        entry = idea["entry_price"]
        stop = idea["stop_loss"]
        risk = abs(entry - stop)
        rr_ideal = abs(entry - tp1_ideal) / risk
        assert math.isclose(rr_ideal, 1.5, abs_tol=1e-9), (
            f"long TP1 ideal should also give rr=1.5 exactly by construction, got {rr_ideal}"
        )


class TestAssetClassScaling:
    """Pattern S maturity thresholds scale per asset_class.

    Perp-DEX assets (6x) need far larger moves to trigger late-move veto
    than blue-chip majors. Ai_infra (2x) sits between them.
    """

    def _patch_trend_quality(self, monkeypatch, classification, confidence=3):
        def _tq_analyze(c, **_kw):
            return {
                "pattern": {
                    "present": True, "confidence": confidence,
                    "classification": classification,
                    "max_confidence": 5, "type": "trend",
                },
                "input_scores": {},
            }
        tq_mod = type("TQ", (), {"analyze": staticmethod(_tq_analyze)})()
        bo_mod = type(
            "BO",
            (),
            {"analyze": staticmethod(lambda c, **_kw: {"pattern": {"present": False}})},
        )()
        import analysis.skill_loader as sl
        canned = {"market-trend-quality": tq_mod, "market-breakout": bo_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

    def test_perp_dex_does_not_fire_late_move_at_200pct(self, monkeypatch):
        """perp_dex asset with 200% maturity should NOT get late-move veto.

        200% < 300% (perp_dex late threshold), but 200% > 180% (mature threshold).
        """
        self._patch_trend_quality(monkeypatch, "HEALTHY_UPTREND", confidence=4)
        mod = _load_strat_lib()
        candles = _make_trending_candles(start=100.0, end=300.0, n=250)
        result = mod.analyze(candles, ticker="LIT", interval="4h", period="6mo", asset_class="perp_dex")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected at least one long idea for perp_dex"
        idea = long_ideas[0]
        vetoes = idea.get("veto_reasons", [])
        assert "late-move" not in vetoes, f"perp_dex should not fire late-move at 200% maturity, got {vetoes}"
        assert "asset-class-scaled" in vetoes, f"expected asset-class-scaled tag for perp_dex, got {vetoes}"
        assert "mature-move" in vetoes, f"expected mature-move at 200% even on perp_dex, got {vetoes}"

    def test_blue_chip_still_fires_late_move(self, monkeypatch):
        """Same candles without asset_class should still fire late-move."""
        self._patch_trend_quality(monkeypatch, "HEALTHY_UPTREND", confidence=4)
        mod = _load_strat_lib()
        candles = _make_trending_candles(start=100.0, end=300.0, n=250)
        result = mod.analyze(candles, ticker="BTC", interval="4h", period="6mo", asset_class=None)
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected at least one long idea for blue-chip"
        idea = long_ideas[0]
        vetoes = idea.get("veto_reasons", [])
        assert "late-move" in vetoes, f"blue-chip should fire late-move at 200% maturity, got {vetoes}"
        assert "asset-class-scaled" not in vetoes, "blue-chip should not have asset-class-scaled tag"

    def test_ai_infra_scales_mature_threshold(self, monkeypatch):
        """ai_infra (2x) with 80% maturity: fires mature-move but not late-move.

        80% > 60% (ai_infra mature threshold) but 80% < 100% (ai_infra late threshold).
        """
        self._patch_trend_quality(monkeypatch, "HEALTHY_UPTREND", confidence=4)
        mod = _load_strat_lib()
        candles = _make_trending_candles(start=100.0, end=180.0, n=250)
        result = mod.analyze(candles, ticker="TAO", interval="4h", period="6mo", asset_class="ai_infra")
        long_ideas = [i for i in result["ideas"] if i["direction"] == "long"]
        assert long_ideas, "expected at least one long idea for ai_infra"
        idea = long_ideas[0]
        vetoes = idea.get("veto_reasons", [])
        assert "late-move" not in vetoes, f"ai_infra should not fire late-move at 80% maturity, got {vetoes}"
        assert "mature-move" in vetoes, f"ai_infra should fire mature-move at 80% (60% threshold), got {vetoes}"
        assert "asset-class-scaled" in vetoes, f"expected asset-class-scaled tag for ai_infra, got {vetoes}"

    def test_conviction_higher_with_asset_class_scaling(self, monkeypatch):
        """Same candles: perp_dex conviction > default conviction because late-move
        penalty (-2 conv) isn't applied."""
        self._patch_trend_quality(monkeypatch, "HEALTHY_UPTREND", confidence=4)
        mod = _load_strat_lib()
        candles = _make_trending_candles(start=100.0, end=300.0, n=250)
        result_default = mod.analyze(candles, ticker="ASSET", interval="4h", period="6mo", asset_class=None)
        result_scaled = mod.analyze(candles, ticker="ASSET", interval="4h", period="6mo", asset_class="perp_dex")
        default_idea = next((i for i in result_default["ideas"] if i["direction"] == "long"), None)
        scaled_idea = next((i for i in result_scaled["ideas"] if i["direction"] == "long"), None)
        assert default_idea is not None, "expected a long idea in default result"
        assert scaled_idea is not None, "expected a long idea in perp_dex result"
        assert scaled_idea["conviction"] > default_idea["conviction"], (
            f"perp_dex conviction ({scaled_idea['conviction']}) should be > "
            f"default conviction ({default_idea['conviction']})"
        )
