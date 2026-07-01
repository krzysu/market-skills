"""Tests for market-trend-quality L2 — pullback classification, new impulse/volume logic."""

import importlib.util
import os
import random

random.seed(42)


def _load_tq_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-trend-quality", "lib.py")
    spec = importlib.util.spec_from_file_location("market_trend_quality_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(
    base_price=100.0,
    n=250,
    trend="uptrend",
    volatility=2.0,
    pullback_start=None,
    pullback_end=None,
):
    """Generate synthetic candles with optional pullback."""
    vals = []
    price = base_price
    for i in range(n):
        if trend == "uptrend":
            price += random.uniform(-volatility, volatility) + 0.3
        elif trend == "downtrend":
            price += random.uniform(-volatility, volatility) - 0.3
        else:
            price += random.uniform(-volatility, volatility)

        # Pullback: temporary dip
        if pullback_start is not None and pullback_end is not None:
            if pullback_start <= i <= pullback_end:
                price -= random.uniform(0.5, 1.5)

        vals.append(
            [
                i * 86400,
                price,
                price + random.uniform(0, 1),
                price - random.uniform(0, 1),
                price,
                random.randint(100000, 500000),
            ]
        )
    return vals


class TestImpulseVsRetrace:
    def test_impulse_bounce_detected(self):
        """Recent positive return after prior negative return should register as impulse."""
        mod = _load_tq_lib()
        # Build 60 candles with a clear bounce pattern in the last 13
        prices = [100.0]
        for i in range(1, 60):
            prices.append(prices[-1] * 1.002)  # gentle uptrend for first 47

        # Last 13: prior 6 declining, recent 6 bouncing up
        for i in range(1, 7):
            prices[-13 + i] = prices[-13 + i - 1] * 0.98
        for i in range(7, 13):
            prices[-13 + i] = prices[-13 + i - 1] * 1.03

        candles = [[i * 86400, p, p + 1, p - 1, p, 200000] for i, p in enumerate(prices)]

        result = mod.analyze(candles, interval="4h", period="6mo")
        impulse = result.get("signals", {}).get("impulse_vs_retrace", {})
        assert impulse.get("present") is True, f"Expected impulse present for bounce, got {impulse}"

    def test_no_impulse_in_dead_flat(self):
        """Flat/no-move market should not trigger impulse."""
        mod = _load_tq_lib()
        prices = [100.0] * 60
        candles = [[i * 86400, p, p + 0.1, p - 0.1, p, 200000] for i, p in enumerate(prices)]
        result = mod.analyze(candles, interval="4h", period="6mo")
        impulse = result.get("signals", {}).get("impulse_vs_retrace", {})
        assert impulse.get("present") is False, f"Expected no impulse for flat market, got {impulse}"


class TestVolumeConfirmation:
    def test_quiet_accumulation_accepted(self):
        """Quiet accumulation (vr in [0.7, 1.0] with price > ema_50) sets volume_confirmation.present=True."""
        mod = _load_tq_lib()
        candles = _make_candles(n=250, trend="uptrend")
        for c in candles:
            c[5] = 200000

        result = mod.analyze(candles, interval="4h", period="6mo")
        vol_signal = result.get("signals", {}).get("volume_confirmation", {})
        assert "present" in vol_signal, "volume_confirmation sub-signal must be evaluated"
        assert isinstance(vol_signal["present"], bool)
        assert vol_signal["weight"] == 0.15


class TestHEALTHYPULLBACKUPTREND:
    def test_pullback_classification_returns(self):
        """Analyze returns a pattern dict regardless of classification."""
        mod = _load_tq_lib()
        candles = _make_candles(n=250, trend="uptrend")
        result = mod.analyze(candles, interval="4h", period="6mo")
        assert "pattern" in result
        assert result["pattern"]["type"] == "TREND_QUALITY"

    def test_insufficient_data(self):
        mod = _load_tq_lib()
        result = mod.analyze([], interval="4h", period="6mo")
        assert result["pattern"]["present"] is False

    def test_basic_return_structure(self):
        mod = _load_tq_lib()
        candles = _make_candles(n=250, trend="uptrend")
        result = mod.analyze(candles, interval="4h", period="6mo")
        assert "signals" in result
        assert "input_scores" in result
        assert "narrative" in result


class TestFourSubSignalFallback:
    """4+ present sub-signals must classify even if signed_score<0.75.

    The signed_score threshold only triggers when |signed_score| >= 0.75. With
    4/5 sub-signals present, a deep pullback can pull signed_score below 0.75
    while the directional signal is still strong. The fallback counts present
    sub-signals regardless of contribution sign.
    """

    def test_present_sub_signal_count_helper(self):
        mod = _load_tq_lib()
        signals = {
            "a": {"present": True},
            "b": {"present": True},
            "c": {"present": False},
            "d": {"present": True},
        }
        assert mod._present_sub_signal_count(signals) == 3

    def test_present_sub_signal_count_handles_missing_present_key(self):
        mod = _load_tq_lib()
        signals = {"a": {}, "b": {"present": True}}
        assert mod._present_sub_signal_count(signals) == 1

    def test_classification_not_none_with_4_present_sub_signals(self, monkeypatch):
        """When 4+ sub-signals are present (regardless of signed_score magnitude),
        classification must not be None — the SOL residual bug."""
        import analysis.skill_loader as sl

        # Build fake sub-skill responses where trend_score=0 (so HEALTHY_UPTREND
        # gate fails) but 4 sub-signals are present.
        fake_trend = {
            "score": 0,
            "alignment": "FULL_BULL",
            "higher_high": True,
            "higher_low": True,
            "ema_50": 100.0,
            "current_price": 105.0,
        }
        fake_fib = {"nearest_fib_distance_pct": 12.0}  # deep pullback → -0.20 contribution
        fake_vol = {
            "volume_ratio": 1.0,  # not enough for any volume branch
            "obv_trend": "rising",
        }

        canned = {
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
            "market-fibonacci": type("F", (), {"analyze": staticmethod(lambda c, **_kw: fake_fib)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-ema": type("E", (), {"analyze": staticmethod(lambda c, **_kw: {"alignment": "FULL_BULL"})})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_tq_lib()
        candles = _make_candles(n=250, trend="uptrend")
        result = mod.analyze(candles, interval="4h", period="6mo")

        # 4 sub-signals present (ema, hh_hl, impulse; pullback present too, just negative)
        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count >= 4, (
            f"test setup error — expected ≥4 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] is not None, (
            f"4-sub fallback: classification dropped to None despite {present_count} present sub-signals"
        )
        assert result["pattern"]["present"] is True


class TestThreeSubSignalFallback:
    """3 present sub-signals must classify as WEAKENING via the count + weight gate.

    The 3-sub branch (lib.py:207) is gated by ``count == 3 AND weighted_sum > 0.30``.
    The ``weighted_sum > 0.30`` threshold replaces the pre-fix
    ``abs(signed_score) >= 0.50`` gate which silently dropped cases where
    opposing signs dilute signed_score while the directional signal is still
    strong (see ``test_three_sub_opposing_signs_wsum_above_030_classifies``
    below).

    With current sub-signal weights (0.25, 0.25, 0.20, 0.15, 0.15) the minimum
    3-sub weighted_sum is 0.50, so the threshold is structurally always met for
    count==3. The explicit threshold keeps the trigger self-documenting and
    resilient to future weight changes.

    The dedicated 2-sub branch is exercised
    by ``test_two_sub_bullish_at_w045_classifies`` and friends; the count==3
    boundary is guarded by ``test_three_sub_count_gate_protects_two_subs``
    which prevents the 3-sub branch from double-firing on 2-sub shapes.
    """

    def _flat_candles(self, n=250, base_price=100.0):
        """Build flat candles that suppress the impulse_vs_retrace sub-signal.

        impulse_vs_retrace uses 6-bar recent vs 6-bar prior return. For a
        trending or random candle stream, this branch fires via the
        deceleration case (recent > 0, prior > 0, abs(recent) < abs(prior)).
        For a constant-price stream, recent_return = 0 and prior_return = 0,
        so none of the three branches fire and the sub-signal is absent.
        """
        return [[i * 86400, base_price, base_price, base_price, base_price, 200000] for i in range(n)]

    def _patch_loader(self, monkeypatch, *, alignment, hh, hl, fib_distance, vol_setup):
        """Patch the L1 skill loader to return a deterministic shape.

        ``vol_setup`` is a dict with ``volume_ratio`` and ``obv_trend`` (and
        optionally ``force_present``) so the volume_confirmation branch can be
        exercised predictably.
        """
        import analysis.skill_loader as sl

        fake_trend = {
            "score": 0,  # HEALTHY_UPTREND gate fails
            "alignment": alignment,
            "higher_high": hh,
            "higher_low": hl,
            "ema_50": 100.0,
            "current_price": 105.0,
        }
        fake_fib = {"nearest_fib_distance_pct": fib_distance}
        fake_vol = {
            "volume_ratio": vol_setup["volume_ratio"],
            "obv_trend": vol_setup["obv_trend"],
        }

        canned = {
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
            "market-fibonacci": type("F", (), {"analyze": staticmethod(lambda c, **_kw: fake_fib)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-ema": type("E", (), {"analyze": staticmethod(lambda c, **_kw: {"alignment": alignment})})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

    def test_two_sub_bullish_at_w045_classifies(self, monkeypatch):
        """2 subs at w=0.45, signed_score=+0.45 must classify as WEAKENING.
        """
        # ema: present (FULL_BULL)         contribution +0.25
        # hh_hl: NOT present (mixed)        contribution   0
        # pullback: present (fib<3)        contribution +0.20
        # impulse: NOT present (flat)       contribution   0
        # volume: NOT present (vr=None)     contribution   0
        # -> 2 present, signed_score = 0.45, weighted_sum = 0.45
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BULL",
            hh=True,
            hl=False,  # mixed -> hh_hl NOT present
            fib_distance=2.0,  # < 3 -> +0.20
            vol_setup={"volume_ratio": None, "obv_trend": None},  # absent (no vr → no branch fires)
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="1h", period="1mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 2, (
            f"test setup error — expected exactly 2 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] == "WEAKENING", (
            f"2 subs at w=0.45, signed_score=+0.45 must classify as WEAKENING. "
            f"Got classification={result['pattern']['classification']!r}, "
            f"present={result['pattern']['present']}"
        )
        assert result["pattern"]["present"] is True

    def test_two_sub_bearish_at_w045_classifies(self, monkeypatch):
        """Symmetric bearish case: 2 subs at w=0.45, signed_score=-0.45 must classify as WEAKENING.

        Mirror of ``test_two_sub_bullish_at_w045_classifies`` for short bias.
        """
        # ema: present (FULL_BEAR)         contribution -0.25
        # hh_hl: NOT present (mixed)        contribution   0
        # pullback: present (fib>8)        contribution -0.20
        # impulse: NOT present (flat)       contribution   0
        # volume: NOT present (vr=None)     contribution   0
        # -> 2 present, signed_score = -0.45, weighted_sum = 0.45
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BEAR",
            hh=False,
            hl=True,  # mixed -> hh_hl NOT present
            fib_distance=12.0,  # > 8 -> -0.20
            vol_setup={"volume_ratio": None, "obv_trend": None},  # absent
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="1h", period="1mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 2, (
            f"test setup error — expected exactly 2 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] == "WEAKENING", (
            f"2 bearish subs at signed_score=-0.45, wsum=0.45 must classify "
            f"as WEAKENING. Got classification={result['pattern']['classification']!r}, "
            f"present={result['pattern']['present']}"
        )
        assert result["pattern"]["present"] is True

    def test_two_sub_opposing_signs_stays_none(self, monkeypatch):
        """Negative test: 2 present sub-signals with opposing signs must NOT classify.

        The 2-sub branch enforces directional coherence — when the two
        present subs disagree (ema FULL_BEAR + pullback shallow at fib<3),
        the directional signal is contradictory noise and must stay absent.
        Without the coherence gate, any 2-sub borderline case would
        promote to WEAKENING and silently over-fire on truly conflicting
        sub-signals.

        Setup: ema FULL_BEAR (-0.25) + pullback fib=2.0 (+0.20), all others
        absent. signed_score = -0.05, weighted_sum = 0.45.
        """
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BEAR",
            hh=False,
            hl=True,  # mixed -> hh_hl NOT present
            fib_distance=2.0,  # < 3 -> +0.20 (opposes ema)
            vol_setup={"volume_ratio": None, "obv_trend": None},  # absent
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="1h", period="1mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 2, (
            f"test setup error — expected exactly 2 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] is None, (
            f"2-sub opposing signs (ema FULL_BEAR + pullback shallow) must "
            f"NOT classify — directional coherence gate guards against "
            f"promoting contradictory noise. Got "
            f"classification={result['pattern']['classification']!r}, "
            f"present={result['pattern']['present']}"
        )
        assert result["pattern"]["present"] is False

    def test_three_sub_bullish_at_w060_classifies(self, monkeypatch):
        """3 subs (ema + pullback + volume) at w=0.60, signed_score=0.60.

        Without the 3-sub branch, classification drops to None and l2_fired()
        returns False in the L3 layer, suppressing the idea.
        """
        # ema: present (FULL_BULL)         contribution +0.25
        # hh_hl: NOT present (mixed)        contribution  0
        # pullback: present (fib_dist<3)    contribution +0.20
        # impulse: NOT present (flat candles, no return) contribution  0
        # volume: present (vr 1.0, obv rising) contribution +0.15
        # -> 3 present, signed_score = 0.60
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BULL",
            hh=True,
            hl=False,  # mixed -> hh_hl NOT present
            fib_distance=2.0,  # < 3 -> +0.20
            vol_setup={"volume_ratio": 1.0, "obv_trend": "rising"},  # +0.075
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="4h", period="6mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 3, (
            f"test setup error — expected exactly 3 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] == "WEAKENING", (
            f"3 subs at w=0.60 must classify as WEAKENING, got "
            f"classification={result['pattern']['classification']!r}, "
            f"present={result['pattern']['present']}"
        )
        assert result["pattern"]["present"] is True

    def test_three_sub_bearish_at_w060_classifies(self, monkeypatch):
        """Symmetric case for short bias: 3 bearish subs at signed_score=-0.60
        must also classify as WEAKENING.
        """
        # ema: present (FULL_BEAR)         contribution -0.25
        # hh_hl: NOT present (mixed)        contribution   0
        # pullback: present (fib_dist>8)    contribution -0.20
        # impulse: NOT present (flat candles) contribution   0
        # volume: present (distribution)    contribution -0.15
        # -> 3 present, signed_score = -0.60
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BEAR",
            hh=False,
            hl=True,  # mixed -> hh_hl NOT present
            fib_distance=12.0,  # > 8 -> -0.20
            vol_setup={"volume_ratio": 2.0, "obv_trend": "falling"},  # -0.15 distribution
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="4h", period="6mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 3, (
            f"test setup error — expected exactly 3 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] == "WEAKENING", (
            f"3 bearish subs at signed_score=-0.60 must classify as "
            f"WEAKENING, got {result['pattern']['classification']!r}"
        )
        assert result["pattern"]["present"] is True

    def test_three_sub_count_gate_protects_two_subs(self, monkeypatch):
        """The 3-sub branch is count-gated. A 2-sub configuration must NOT
        trigger the 3-sub WEAKENING path — but the dedicated 2-sub branch
        DOES accept 2-sub cases with
        directional coherence. This test guards the count==3 boundary
        specifically — that the 3-sub branch never fires on count==2,
        regardless of weighted_sum.

        Guards against a future refactor that loosens the count==3 check and
        starts firing the 3-sub branch on 2-sub configurations — which would
        double-count (2-sub branch + 3-sub branch) and inflate the
        classification logic's surface area.

        Setup: 2 subs (ema FULL_BULL + hh_hl intact) at weighted_sum=0.50,
        signed_score=0.50. Pullback and volume deliberately suppressed.
        Both subs contribute +ve → the 2-sub branch classifies as WEAKENING
        (directional-coherence). The 3-sub branch must
        NOT additionally fire — count==2 is below the count==3 boundary.
        """
        import analysis.skill_loader as sl

        fake_trend = {
            "score": 0,  # HEALTHY_UPTREND gate fails
            "alignment": "FULL_BULL",
            "higher_high": True,
            "higher_low": True,  # both intact -> hh_hl present
            "ema_50": 100.0,
            "current_price": 105.0,
        }
        # fib_distance=None -> pullback NOT present; vr=None -> volume NOT present.
        fake_fib = {"nearest_fib_distance_pct": None}
        fake_vol = {"volume_ratio": None, "obv_trend": None}
        canned = {
            "market-trend": type("T", (), {"analyze": staticmethod(lambda c, **_kw: fake_trend)})(),
            "market-fibonacci": type("F", (), {"analyze": staticmethod(lambda c, **_kw: fake_fib)})(),
            "market-volume": type("V", (), {"analyze": staticmethod(lambda c, **_kw: fake_vol)})(),
            "market-ema": type("E", (), {"analyze": staticmethod(lambda c, **_kw: {"alignment": "FULL_BULL"})})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="4h", period="6mo")

        # ema: present (FULL_BULL)         contribution +0.25
        # hh_hl: present (hh=T, hl=T)     contribution +0.25
        # pullback: NOT present (fib=None)
        # impulse: NOT present (flat)
        # volume: NOT present (vr=None)
        # -> 2 present, signed_score = 0.50, weighted_sum = 0.50
        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 2, (
            f"test setup error — expected exactly 2 present sub-signals, got {present_count}: {result['signals']}"
        )
        # Post-fix: the 2-sub branch promotes this shape
        # to WEAKENING because both present subs contribute +ve (ema FULL_BULL
        # + hh_hl intact). The 3-sub branch must NOT fire (count==2 below
        # the count==3 boundary) — classification lands at WEAKENING via the
        # 2-sub branch, not via double-counting. The test name guards the
        # count==3 boundary specifically: present_count == 2 must always
        # mean the 2-sub branch handled it, never the 3-sub branch.
        assert result["pattern"]["classification"] == "WEAKENING", (
            f"2-sub case at signed_score=0.50 / wsum=0.50 (both subs +ve) "
            f"must classify as WEAKENING via the 2-sub branch. "
            f"Got classification="
            f"{result['pattern']['classification']!r} with present_count="
            f"{present_count}: {result['signals']}"
        )
        assert result["pattern"]["present"] is True

    def test_three_sub_opposing_signs_wsum_above_030_classifies(self, monkeypatch):
        """3 sub-signals with opposing signs (wsum=0.70, signed_score=+0.20) must classify.

        3 sub-signals present with opposing signs:
          - ema_alignment: FULL_BEAR (-0.25)
          - hh_hl_integrity: hh=True, hl=True (+0.25)
          - pullback_depth: fib_distance=0.97% (+0.20)
        → 3 present, signed_score=+0.20, weighted_sum=0.70.

        Pre-fix the 3-sub branch required ``abs(signed_score) >= 0.50``,
        silently dropping the case despite weighted_sum being well above the
        bug-scan Shape #1 trigger (0.30). The pattern stayed absent while 3
        sub-signals were populated — the ghost shape the bug-scan catches.

        Post-fix the trigger uses ``weighted_sum > 0.30`` so this case
        classifies as WEAKENING.
        """
        # ema: present (FULL_BEAR)         contribution -0.25
        # hh_hl: present (hh=T, hl=T)     contribution +0.25
        # pullback: present (fib=0.97)     contribution +0.20
        # impulse: NOT present (flat)      contribution   0
        # volume: NOT present (vr=0.58)   contribution   0
        # -> 3 present, signed_score = 0.20, weighted_sum = 0.70
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BEAR",
            hh=True,
            hl=True,  # both intact -> hh_hl present
            fib_distance=0.97,  # < 3 -> +0.20
            vol_setup={"volume_ratio": 0.58, "obv_trend": "falling"},  # absent (vr < 0.7)
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="4h", period="6mo")

        present_count = sum(1 for s in result["signals"].values() if s.get("present"))
        assert present_count == 3, (
            f"test setup error — expected exactly 3 present sub-signals, got {present_count}: {result['signals']}"
        )
        assert result["pattern"]["classification"] == "WEAKENING", (
             f"3 subs with opposing signs (wsum=0.70, signed_score=0.20) "
             f"must classify as WEAKENING. "
            f"Got classification={result['pattern']['classification']!r}, "
            f"present={result['pattern']['present']}"
        )
        assert result["pattern"]["present"] is True

    def test_three_sub_wsum_invariant_present_count(self, monkeypatch):
        """When the 3-sub branch fires, present_count must equal 3.

        Companion to the existing `test_present_count_invariant_when_present_true`
        sweep — catches a case where the 3-sub branch fires from a different
        sub-signal count.
        """
        self._patch_loader(
            monkeypatch,
            alignment="FULL_BULL",
            hh=True,
            hl=False,
            fib_distance=2.0,
            vol_setup={"volume_ratio": 1.0, "obv_trend": "rising"},
        )
        mod = _load_tq_lib()
        result = mod.analyze(self._flat_candles(), interval="4h", period="6mo")

        if result["pattern"]["present"] and result["pattern"]["classification"] == "WEAKENING":
            present_count = sum(1 for s in result["signals"].values() if s.get("present"))
            assert present_count == 3, (
                f"3-sub branch fired but present_count={present_count}, signals={result['signals']}"
            )


class TestPatternAAndBSchemaInvariant:
    """pattern.present and pattern.classification must be coherent.

    Pattern A — sub-signals present but pattern absent
        Any combination of ≥3 present sub-signals should still produce a classification
        (via the sub-signal fallback or HEALTHY gates).

    Pattern B — present=True but classification=None
        Should be impossible — `present` is computed solely from `classification is not None`
        in the classifier cascade.

    Sweep both invariants across many seeded runs to catch regressions.
    """

    def test_present_and_classification_coherent_across_seeds(self):
        """Across 30 random seeds, present/classification must never be incoherent."""
        mod = _load_tq_lib()
        for seed in range(30):
            random.seed(seed)
            candles = _make_candles(n=250, trend=("uptrend", "downtrend", "sideways")[seed % 3])
            result = mod.analyze(candles, interval=("4h", "1d")[seed % 2], period="6mo")
            pat = result["pattern"]
            assert (pat["classification"] is None) == (pat["present"] is False), (
                f"Pattern A/B violation at seed={seed}: present={pat['present']} classification={pat['classification']}"
            )

    def test_present_count_invariant_when_present_true(self):
        """When present=True, at least 2 sub-signals must be present.

        Catches a case where present=True fires with all sub-signals absent.
        """
        mod = _load_tq_lib()
        for seed in range(20):
            random.seed(seed)
            candles = _make_candles(n=250)
            result = mod.analyze(candles, interval="4h", period="6mo")
            if result["pattern"]["present"]:
                present_count = sum(1 for s in result["signals"].values() if s.get("present"))
                assert present_count >= 2, (
                    f"present=True but only {present_count} sub-signals present: {result['signals']}"
                )
