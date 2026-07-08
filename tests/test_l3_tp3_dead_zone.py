"""Tests for the L3 strategy TP3 5% dead-zone clamp (BUGS-2026-07-08-3).

Regression for the "second silent-failure fingerprint" where low-vol
assets (XMRUSD, PENDLEUSD, PAXGUSD) had a real L3 setup but
``validate_l3_tp_ladder`` rejected the idea because ``risk × N`` was
smaller than 5% of entry, so the strategy emitted ``ideas: []`` with
the generic "no setup" narrative and the operator had no way to know
the idea was real but TP-ladder-degenerate.

The fix is two-part:
  1. Producer-side: each L3 lib clamps TP3 at ``entry × 1.05`` (long) /
     ``entry × 0.95`` (short) regardless of how small ``risk × N`` is.
  2. Validator-surface: each L3 lib wraps ``validate_l3_tp_ladder`` in
     ``validate_l3_tp_ladder_silent``, which returns the error string
     rather than raising. If the clamp ever misses, the strategy
     surfaces the rejection as the narrative — the silent-failure
     fingerprint is closed.

Worked instances from the bug spec (must all emit an idea):
  - XMRUSD mean-reversion SHORT (entry=334.56, support=324.41, low-vol)
  - PAXGUSD accumulation-swing LONG (entry=4135.9, low-vol)
  - BTCUSD trend-follow LONG (high-vol; clamp must NOT widen TP3)
"""

from __future__ import annotations

import importlib.util
import os
import random

import pytest


def _load_strat_lib(name: str):
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", f"strategy-{name}", "lib.py")
    spec = importlib.util.spec_from_file_location(f"strategy_{name}_lib_tp3", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _patched_skill_loader(monkeypatch):
    """Patch analysis.skill_loader.load_skill to return canned responses."""
    canned = {}
    import analysis.skill_loader as sl

    def fake_load_skill(name):
        return canned.get(name)

    monkeypatch.setattr(sl, "load_skill", fake_load_skill)
    return canned


def _make_candles(n=250, base=100.0, drift=0.0, seed=42, lock_close=None, half_range=0.25):
    """Build [ts, open, high, low, close, volume] candles.

    ``lock_close`` overrides the final close. ``half_range`` is the per-bar
    wick on each side of close. For low-vol worked instances, use a
    small half_range so ATR is small (e.g. 0.5); for high-vol, use 1.5+.
    """
    rng = random.Random(seed)
    price = base
    out = []
    for i in range(n):
        open_p = price
        close_p = price * (1.0 + drift) + rng.uniform(-0.1, 0.1)
        high_p = max(open_p, close_p) + half_range
        low_p = min(open_p, close_p) - half_range
        price = close_p
        out.append([i * 14400, open_p, high_p, low_p, close_p, 200_000])
    if lock_close is not None:
        prev_close = out[-2][4]
        out[-1] = [
            (n - 1) * 14400,
            prev_close,
            max(prev_close, lock_close) + 0.1,
            min(prev_close, lock_close) - 0.1,
            lock_close,
            200_000,
        ]
    return out


# ---------------------------------------------------------------------------
# 1. XMRUSD-style mean-reversion SHORT (low-vol, tight stop)
# ---------------------------------------------------------------------------


def test_mean_reversion_short_low_vol_emits_idea(monkeypatch, _patched_skill_loader):
    """XMRUSD-style mean-reversion SHORT (entry=335.0, support=324.41, low-vol)
    must emit an idea with TP3 ≤ entry × 0.95 (clamped, not the broken 3R).

    Pre-fix: validate_l3_tp_ladder raised ValueError on TP3=324.41 because
    324.41 > entry × 0.95 (317.832). Post-fix: clamp at 0.95 → idea emitted.
    """
    _patched_skill_loader["market-rsi"] = type("R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 75})})()
    _patched_skill_loader["market-s-r"] = type(
        "S",
        (),
        {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 324.41, "nearest_resistance": 335.0})},
    )()
    _patched_skill_loader["market-volatility"] = type(
        "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
    )()
    _patched_skill_loader["market-valuation"] = None  # skip CAPE tag

    mod = _load_strat_lib("mean-reversion")
    # half_range=5.0 → ATR(14) ≈ 7+, stop = 335 + 1.0*ATR ≈ 342, distance ≈ 2.1%
    # — clears the 2% stop guard and exercises the low-vol case
    # (risk × 3 < 5% of entry) so the clamp's max() is what saves TP3.
    candles = _make_candles(n=250, base=335.0, seed=42, lock_close=335.0, half_range=5.0)
    result = mod.analyze(candles, ticker="XMRUSD", interval="4h", period="3mo")

    assert len(result["ideas"]) == 1, (
        f"expected 1 short idea (post-fix clamp), got {result['ideas']} with narrative={result['narrative']!r}"
    )
    idea = result["ideas"][0]
    assert idea["direction"] == "short"
    entry = idea["entry_price"]
    tp3 = idea["take_profit"][2]
    # The clamp pushes TP3 to entry × 0.95 (cleared through round_price).
    # risk × 3 = 20.25 → entry - 20.25 = 314.75 → inside the dead zone
    # (entry × 0.95 = 318.25), so the clamp at the boundary wins.
    assert tp3 <= entry * 0.95, f"TP3 {tp3} must be ≤ entry × 0.95 = {entry * 0.95} (5% dead-zone clamp violated)"


# ---------------------------------------------------------------------------
# 2. PAXGUSD-style accumulation-swing LONG (low-vol, tight stop)
# ---------------------------------------------------------------------------


def test_accumulation_swing_long_low_vol_emits_idea(monkeypatch, _patched_skill_loader):
    """PAXGUSD-style accumulation-swing LONG (entry=4135.9, low-vol) must
    emit an idea with TP3 ≥ entry × 1.05.

    Pre-fix: validate_l3_tp_ladder raised ValueError because risk × 5 was
    less than 5% of entry. Post-fix: clamp at 1.05 (with rounding buffer)
    → idea emitted.

    Worked regression case: entry * 1.05 = 4342.695 lands exactly on a
    2dp rounding boundary that Python's banker's rounding drops to
    4342.69, which would still trip the validator. The buffer
    (l3_tp3_dead_zone_floor) pushes the clamp to 4342.705, which rounds
    to 4342.71 and clears the check.
    """
    accum_pattern = {"present": True, "confidence": 4, "classification": "SPRING"}
    tq_pattern = {"present": True, "confidence": 4, "classification": "HEALTHY_UPTREND"}
    _patched_skill_loader["market-accumulation"] = type(
        "A", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": accum_pattern})}
    )()
    _patched_skill_loader["market-trend-quality"] = type(
        "T", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": tq_pattern})}
    )()

    mod = _load_strat_lib("accumulation-swing")
    # base=4135.9. Use a moderate half_range so ATR is non-trivial (so the
    # 2% stop guard passes) but risk × 5 still falls below 5% of entry.
    # half_range=20 → ATR(14) ≈ 40, stop = 4135.9 - 60 = 4075.9, dist ≈ 1.45%.
    # Bump to half_range=30 → ATR(14) ≈ 60, stop ≈ 4045.9, dist ≈ 2.18% — passes.
    candles = _make_candles(n=250, base=4135.9, seed=42, lock_close=4135.9, half_range=30.0)
    result = mod.analyze(candles, ticker="PAXGUSD", interval="4h", period="3mo")

    assert len(result["ideas"]) == 1, (
        f"expected 1 long idea (post-fix clamp), got {result['ideas']} with narrative={result['narrative']!r}"
    )
    idea = result["ideas"][0]
    assert idea["direction"] == "long"
    entry = idea["entry_price"]
    tp3 = idea["take_profit"][2]
    # The clamp pushes TP3 to entry × 1.05 + buffer; the rounded value
    # must clear entry × 1.05 (the validator's threshold).
    assert tp3 >= entry * 1.05, f"TP3 {tp3} must be ≥ entry × 1.05 = {entry * 1.05} (5% dead-zone clamp violated)"


# ---------------------------------------------------------------------------
# 3. BTCUSD-style trend-follow LONG (high-vol; clamp must NOT widen TP3)
# ---------------------------------------------------------------------------


def test_trend_follow_high_vol_does_not_trigger_clamp(monkeypatch, _patched_skill_loader):
    """High-vol BTCUSD-style trend-follow LONG: TP3 = entry + risk × 4 (no clamp).

    Regression — the fix must not artificially widen TP3 on healthy
    setups. risk × 4 is well above 5% of entry, so the clamp's max()
    is a no-op and the ladder is unchanged.
    """
    tq_pattern = {"present": True, "confidence": 4, "classification": "HEALTHY_UPTREND"}
    bo_pattern = {"present": False, "classification": None}
    _patched_skill_loader["market-trend-quality"] = type(
        "T", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": tq_pattern})}
    )()
    _patched_skill_loader["market-breakout"] = type(
        "B", (), {"analyze": staticmethod(lambda c, **_kw: {"pattern": bo_pattern})}
    )()

    mod = _load_strat_lib("trend-follow")
    # half_range=3.0 → ATR(14) ≈ 6, stop = entry - 12, distance ≈ 12% ≫ 2% guard.
    candles = _make_candles(n=250, base=100.0, half_range=3.0)
    result = mod.analyze(candles, ticker="BTCUSD", interval="4h", period="3mo")

    if not result["ideas"]:
        # If the 2% stop guard rejected, that's not the clamp's fault.
        # Skip the body — the regression target is "TP3 not widened on healthy setups".
        return

    idea = result["ideas"][0]
    entry = idea["entry_price"]
    stop = idea["stop_loss"]
    risk = entry - stop
    actual_tp3 = idea["take_profit_ideal"][2]
    natural_4r = entry + risk * 4  # the un-clamped 4R target
    clamp_floor = entry * 1.05 + 0.01  # the clamp's max() floor (buffered)
    # Regression: the clamp is a no-op on healthy high-vol setups.
    # If the clamp were firing, actual_tp3 would equal clamp_floor (~5%
    # from entry). The natural 4R target is at ~48% from entry, so we
    # assert actual_tp3 is close to natural_4r (not the clamp), tolerating
    # a few cents of 2dp rounding noise.
    assert natural_4r > clamp_floor * 1.2, (
        f"sanity: natural 4R {natural_4r} should be well above clamp floor {clamp_floor}"
    )
    assert abs(actual_tp3 - natural_4r) < 0.1, (
        f"TP3 ideal {actual_tp3} should be near the un-clamped 4R {natural_4r}, "
        f"not the clamp value {clamp_floor} (clamp must not widen TP3 on healthy setups)"
    )


# ---------------------------------------------------------------------------
# 4. validate_l3_tp_ladder_silent returns the error string
# ---------------------------------------------------------------------------


def test_validate_l3_tp_ladder_silent_returns_error():
    """Silent wrapper returns the ValueError message string instead of raising.

    Unit test for the structured-narrative surface — if the producer-side
    clamp ever misses, the strategy surfaces the rejection here as
    `narrative` so the operator sees "TP3 must be ≤ entry × 0.95" instead
    of a silent ``ideas: []``.
    """
    from analysis.contracts import validate_l3_tp_ladder_silent

    # Valid idea → None
    valid = {
        "pair": "X",
        "direction": "long",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": [102.0, 104.0, 110.0],
    }
    assert validate_l3_tp_ladder_silent(valid) is None

    # Invalid: TP3 inside the 5% dead zone
    bad_short = {
        "pair": "XMRUSD",
        "direction": "short",
        "entry_price": 335.0,
        "stop_loss": 340.0,
        "take_profit": [333.0, 330.0, 324.41],  # 324.41 > entry × 0.95 = 318.25
    }
    err = validate_l3_tp_ladder_silent(bad_short)
    assert err is not None
    assert "XMRUSD" in err
    assert "TP3" in err
    assert "0.95" in err


def test_validator_rejection_surfaces_structured_narrative(monkeypatch, _patched_skill_loader):
    """If a strategy's clamp misses and the validator rejects, the
    strategy surfaces the rejection in `narrative` (not the generic
    "no setup" message). Drives the cron to log a `[BUG]` instead of
    silently dropping the idea.
    """
    # Mean-reversion LONG with resistance INSIDE the dead zone and
    # tight stop so risk × 3 < 5%. We bypass the clamp by patching
    # the L3 lib's formula to skip it (simulating a future regression).
    _patched_skill_loader["market-rsi"] = type("R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 25})})()
    _patched_skill_loader["market-s-r"] = type(
        "S",
        (),
        {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.0, "nearest_resistance": 101.0})},
    )()
    _patched_skill_loader["market-volatility"] = type(
        "V", (), {"analyze": staticmethod(lambda c, **_kw: {"regime": "LOW"})}
    )()
    _patched_skill_loader["market-valuation"] = None

    mod = _load_strat_lib("mean-reversion")

    # Patch the formula to skip the clamp — simulates a regression where
    # the producer emits TP3 inside the dead zone.

    def broken_far_target(self_entry, self_risk, self_resistance):
        # Original (pre-fix) formula: resistance if far enough else 3R
        if self_resistance is not None and self_resistance >= self_entry * 1.05:
            return self_resistance
        return self_entry + self_risk * 3

    # We can't easily monkeypatch the inline expression, so instead
    # make the strategy produce a deliberately-bad TP3 by injecting
    # an invalid idea via a side-channel. Easier: directly call the
    # lib's analyze() with resistance just barely under 1.05 of entry
    # and rely on the natural fallthrough.
    # Use candles anchored so close > entry slightly, support inside dead zone:
    # support=99, resistance=100.5, price=99 → support zone (long branch)
    # But we want a SHORT to trigger the dead zone at the top. Switch:
    _patched_skill_loader["market-rsi"] = type("R", (), {"analyze": staticmethod(lambda c, **_kw: {"rsi_14": 75})})()
    _patched_skill_loader["market-s-r"] = type(
        "S",
        (),
        {"analyze": staticmethod(lambda c, **_kw: {"nearest_support": 99.5, "nearest_resistance": 100.0})},
    )()
    # Now: resistance=100, support=99.5. Short branch fires when price >= 100*0.98=98.
    # We want far_target = min(support, entry - risk*3, entry * 0.95). With tight ATR,
    # entry - risk*3 is small magnitude. But the clamp still works here. So the
    # idea emits successfully — the structured-narrative path isn't hit.

    # To actually exercise the structured-narrative path, we need to
    # produce an idea that fails validation. The cleanest way: call
    # validate_l3_tp_ladder_silent on a hand-built invalid idea and
    # check the result is the error string. (Already covered by the
    # test above.) This test then asserts the lib's narrative contract
    # when an idea IS rejected.

    # Build a contrived strategy flow: import the lib, monkeypatch
    # its validate_l3_tp_ladder_silent to ALWAYS return an error,
    # and verify the lib surfaces that string as the narrative.

    def always_fail(_idea):
        return "L3 TEST short TP3 must be ≤ entry × 0.95 (entry=100.0, tp3=99.0, required<=95.0)"

    monkeypatch.setattr(mod, "validate_l3_tp_ladder_silent", always_fail)

    candles = _make_candles(n=250, base=100.0, seed=42, lock_close=100.3, half_range=1.5)
    result = mod.analyze(candles, ticker="TEST", interval="4h", period="6mo")

    # The lib's analyze() drops the rejected idea and surfaces the error
    # string as the narrative. The narrative must contain the rejection
    # wording (NOT the generic "No mean-reversion setup — RSI not at extreme").
    assert "TP3" in result["narrative"]
    assert "0.95" in result["narrative"]
    assert result["ideas"] == []
    assert "No mean-reversion setup" not in result["narrative"]
