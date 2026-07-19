"""bug-scan — classifier-anomaly detector for cron + LLM surfacing.

Detects the recurring bug patterns the swing-scan cron has been hunting
since 2026-06-21, plus the L3 calibration skew and cross-TF contradictions
that surfaced in the 2026-06-23 synthesis. Designed to be called by the
swing-scan / morning-brief / external-scanner crons so they surface the
same findings without each cron re-implementing the rules.

Three input modes:
  - Fresh fetch: positional tickers + --interval/--period. Fetches candles
    via analysis.data and runs all L2 + L3 in-process.
  - --from-state PATH: read the swing-scan state tracker JSON and
    translate its open_findings into the bug-scan envelope. Cheap path
    (no network) for crons that already track findings.
  - --from-json PATH: read a pre-fetched run-all-l2 or run-all-l3 envelope
    and run the detectors on it. Lets the morning-brief / external-scanner
    pass the L2/L3 JSON they already produced.

Detection rules (all wired here, single source of truth):
  - Pattern B Shape #1 — absent-with-subs: l2_fired is False but >=2
    sub-signals are present with combined wsum > 0.30.
  - Pattern B Shape #2 — silent: l2_fired is True but l2_classification
    is None while sub-signals are firing.
  - Pattern B Shape #3 — ghost-classification: l2_fired is False but
    l2_classification is populated. Catches an L2 that regresses to
    silently emitting a classification despite pattern.present=False.
  - Sub-signal weight drift: weights don't sum to 1.0 +/- 0.05. Catches
    the market-exhaustion 0.900 drift that was fixed on 2026-06-21.
  - L3 calibration skew: >=6 ideas, zero with conviction >= 4. Regime
    signal that something is off with conviction scoring.
  - Cross-TF classification contradiction: same ticker + same L2 skill
    shows HEALTHY_UPTREND on one TF and WEAKENING on another. The
    cross-TF classification-contradiction case from 2026-06-23.
  - Cross-TF direction conflict: same ticker + same L3 strategy shows a
    long dominant idea on one TF and a short dominant idea on another
    (both ideas conviction >= 2). Surfaces in L3-only envelopes piped via
    --from-json, which previously produced zero findings because the
    cross-TF detector only walked the L2 axis.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from analysis.contracts import l2_classification, l2_fired
from analysis.registry import l2_skills, l3_strategies

# -- shape constants ----------------------------------------------------------

SHAPE_PATTERN_B_1 = "pattern_b_1"  # absent-with-subs
SHAPE_PATTERN_B_2 = "pattern_b_2"  # silent (present=True, classification=None)
SHAPE_PATTERN_B_3 = "pattern_b_3"  # ghost (present=False, classification populated)
SHAPE_WEIGHT_DRIFT = "weight_drift"
SHAPE_L3_CALIBRATION_SKEW = "l3_calibration_skew"
SHAPE_CROSS_TF_CONTRADICTION = "cross_tf_contradiction"
SHAPE_CROSS_TF_DIRECTION_CONFLICT = "cross_tf_direction_conflict"
SHAPE_CHOP_SCORE = "chop_score"

TAG_BUG = "[BUG]"
TAG_DRIFT = "[DRIFT]"
TAG_INFO = "[INFO]"

# -- thresholds ---------------------------------------------------------------

# Pattern B Shape #1: minimum sub-signal count + wsum to call it a bug.
PATTERN_B_1_MIN_SUBS = 2
PATTERN_B_1_MIN_WSUM = 0.30

# Sub-signal weight drift: weights should sum to 1.0 (each L2 has weighted
# sub-signals whose weights sum to 1.0). 5% tolerance for float math.
WEIGHT_SUM_TARGET = 1.0
WEIGHT_SUM_TOLERANCE = 0.05

# L3 calibration skew: a regime signal — lots of ideas but none strong.
L3_CALIBRATION_MIN_IDEAS = 6
L3_CALIBRATION_MAX_HIGH_CONV = 0  # zero ideas with conviction >= 4
L3_CALIBRATION_HIGH_CONV = 4

# Cross-TF direction conflict: only compare ideas whose conviction is
# strong enough to be meaningful — weaker ideas are noise across TFs.
L3_DIRECTION_MIN_CONVICTION = 2

# L2 skills whose pattern data the bug-scan inspects — sourced from the
# single registry of truth so a newly-added L2 skill is swept automatically
# (no hardcoded drift between bug-scan and analysis.registry).
L2_SKILLS = tuple(l2_skills())

# L3 strategies whose idea counts the bug-scan inspects — same registry
# single-source-of-truth contract as L2_SKILLS above.
L3_STRATEGIES = tuple(l3_strategies())


# -- finding construction -----------------------------------------------------


def _configured_weight_sum(signals: dict) -> float:
    """Sum of *all* configured sub-signal weights in ``signals``.

    Used by the weight-drift detector — drift is a config concern, not a
    firing concern, so this includes present: false entries (which still
    carry their configured weight).
    """
    total = 0.0
    for sig in (signals or {}).values():
        if not isinstance(sig, dict):
            continue
        weight = sig.get("weight", 0.0)
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            total += float(weight)
    return total


def _present_subs_and_wsum(signals: dict) -> tuple[list[str], float]:
    """Return (present_sub_names, wsum) for a sub-signals dict.

    Only sub-signals with ``present: True`` contribute to ``wsum`` — this
    is the wsum used by Pattern B Shape #1 (absent-with-subs) and must
    reflect the *firing* evidence, not the configured total. Use
    :func:`_configured_weight_sum` for the configured total (weight-drift).
    """
    present_names: list[str] = []
    wsum = 0.0
    for name, sig in (signals or {}).items():
        if not isinstance(sig, dict):
            continue
        if sig.get("present") is not True:
            continue
        present_names.append(name)
        weight = sig.get("weight", 0.0)
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            wsum += float(weight)
    return present_names, wsum


def _shape_b1_finding(*, ticker: str, tf: str, skill: str, present_subs: list[str], wsum: float) -> dict:
    return {
        "tag": TAG_BUG,
        "shape": SHAPE_PATTERN_B_1,
        "ticker": ticker,
        "tf": tf,
        "skill": skill,
        "summary": (f"Pattern B Shape #1: {len(present_subs)} subs (w={wsum:.2f}) but pattern absent"),
        "wsum": round(wsum, 4),
        "present_sub_signals": present_subs,
        "severity": "medium" if wsum < 0.70 else "high",
    }


def _shape_b2_finding(*, ticker: str, tf: str, skill: str) -> dict:
    return {
        "tag": TAG_BUG,
        "shape": SHAPE_PATTERN_B_2,
        "ticker": ticker,
        "tf": tf,
        "skill": skill,
        "summary": "Pattern B Shape #2: pattern present but classification is None",
        "severity": "high",
    }


def _shape_b3_finding(*, ticker: str, tf: str, skill: str, classification: str | None) -> dict:
    return {
        "tag": TAG_BUG,
        "shape": SHAPE_PATTERN_B_3,
        "ticker": ticker,
        "tf": tf,
        "skill": skill,
        "summary": (f"Pattern B Shape #3: pattern absent but classification={classification!r}"),
        "severity": "high",
    }


def _weight_drift_finding(*, ticker: str, tf: str, skill: str, wsum: float) -> dict:
    return {
        "tag": TAG_DRIFT,
        "shape": SHAPE_WEIGHT_DRIFT,
        "ticker": ticker,
        "tf": tf,
        "skill": skill,
        "summary": (f"Sub-signal weights sum to {wsum:.3f} (target {WEIGHT_SUM_TARGET} +/- {WEIGHT_SUM_TOLERANCE})"),
        "wsum": round(wsum, 4),
        "severity": "medium",
    }


def _calibration_skew_finding(*, ticker: str, tf: str, ideas_count: int, high_conv: int, conv_dist: dict) -> dict:
    return {
        "tag": TAG_INFO,
        "shape": SHAPE_L3_CALIBRATION_SKEW,
        "ticker": ticker,
        "tf": tf,
        "summary": (f"L3 calibration skew: {ideas_count} ideas, conv {conv_dist}, no conv>={L3_CALIBRATION_HIGH_CONV}"),
        "ideas_count": ideas_count,
        "high_conviction_count": high_conv,
        "conviction_distribution": conv_dist,
        "severity": "low",
    }


def _cross_tf_finding(*, ticker: str, skill: str, tf_a: str, class_a: str, tf_b: str, class_b: str) -> dict:
    return {
        "tag": TAG_INFO,
        "shape": SHAPE_CROSS_TF_CONTRADICTION,
        "ticker": ticker,
        "tf": f"{tf_a}/{tf_b}",
        "skill": skill,
        "summary": (f"Cross-TF contradiction: {tf_a}={class_a} vs {tf_b}={class_b}"),
        "tf_a": tf_a,
        "tf_b": tf_b,
        "classification_a": class_a,
        "classification_b": class_b,
        "severity": "medium",
    }


def _cross_tf_direction_finding(*, ticker: str, strategy: str, tf_a: str, dir_a: str, tf_b: str, dir_b: str) -> dict:
    return {
        "tag": TAG_INFO,
        "shape": SHAPE_CROSS_TF_DIRECTION_CONFLICT,
        "ticker": ticker,
        "tf": f"{tf_a}/{tf_b}",
        "strategy": strategy,
        "summary": (f"Cross-TF direction conflict: {tf_a}={dir_a} vs {tf_b}={dir_b}"),
        "tf_a": tf_a,
        "tf_b": tf_b,
        "direction_a": dir_a,
        "direction_b": dir_b,
        "severity": "medium",
    }


def _chop_score_finding(*, score: float, window_ticks: int, ideas_count: int, low_count: int) -> dict:
    severity = "low"
    if score > 0.70:
        severity = "medium"  # transition-zone
    elif score < 0.40:
        severity = "low"  # aggressive mode, all good
    return {
        "tag": TAG_INFO,
        "shape": SHAPE_CHOP_SCORE,
        "ticker": None,
        "tf": None,
        "skill": None,
        "summary": (
            f"Chop score {score:.2f} ({window_ticks} ticks, {ideas_count} ideas, conv <= 2 = {low_count}/{ideas_count})"
        ),
        "score": round(score, 4),
        "window_ticks": window_ticks,
        "ideas_count": ideas_count,
        "low_count": low_count,
        "severity": severity,
    }


# -- L2 detection -------------------------------------------------------------


def _scan_l2_skill(skill_result: dict | None, *, ticker: str, tf: str, skill: str) -> list[dict]:
    """Run all L2 detectors on a single (ticker, tf, skill) row."""
    if not skill_result or not isinstance(skill_result, dict):
        return []
    if "error" in skill_result:
        return []
    findings: list[dict] = []

    fired = l2_fired(skill_result, skill_name=skill)
    # l2_classification returns None unless the L2 actually fired (the
    # contract for "safe" classification reads). For Pattern B #3 (ghost)
    # we need the RAW classification, so we look at pattern.classification
    # directly — the ghost case is precisely the inconsistency that
    # l2_classification is designed to hide from downstream consumers.
    classification = l2_classification(skill_result, skill_name=skill)
    pattern = skill_result.get("pattern") or {}
    raw_classification = pattern.get("classification")
    present_flag = pattern.get("present")
    signals = skill_result.get("signals") or {}
    present_subs, wsum = _present_subs_and_wsum(signals)
    configured_wsum = _configured_weight_sum(signals)

    # Weight drift — applies to any skill with sub-signal weights, regardless
    # of whether the pattern fired. Catches market-exhaustion-style drift.
    # Uses the *configured* total (all weights), not the present-only wsum,
    # because drift is a config concern.
    if signals and abs(configured_wsum - WEIGHT_SUM_TARGET) > WEIGHT_SUM_TOLERANCE:
        findings.append(_weight_drift_finding(ticker=ticker, tf=tf, skill=skill, wsum=configured_wsum))

    # Pattern B Shape #1 — absent-with-subs.
    if not fired and len(present_subs) >= PATTERN_B_1_MIN_SUBS and wsum > PATTERN_B_1_MIN_WSUM:
        findings.append(_shape_b1_finding(ticker=ticker, tf=tf, skill=skill, present_subs=present_subs, wsum=wsum))

    # Pattern B Shape #2 — present=True, classification=None.
    # ``fired`` is False in this case (l2_fired requires both). So we
    # distinguish by looking at pattern.present directly.
    if present_flag is True and classification is None and present_subs:
        findings.append(_shape_b2_finding(ticker=ticker, tf=tf, skill=skill))

    # Pattern B Shape #3 — present=False, classification populated.
    # Use RAW classification (not the l2_classification helper) — the
    # ghost shape is the case where the helper would suppress the bug.
    if present_flag is False and raw_classification is not None:
        findings.append(_shape_b3_finding(ticker=ticker, tf=tf, skill=skill, classification=raw_classification))

    return findings


def scan_l2(l2_data: dict) -> list[dict]:
    """Run all L2 detectors on a normalized L2 dataset.

    ``l2_data`` shape::

        {
            "tickers": {
                "HYPEUSD": {
                    "tfs": {
                        "1h": {
                            "skills": {
                                "market-trend-quality": {"pattern": ..., "signals": ...},
                                ...
                            }
                        }
                    }
                }
            }
        }
    """
    findings: list[dict] = []
    tickers = (l2_data or {}).get("tickers") or {}
    for ticker, t_entry in tickers.items():
        tfs = (t_entry or {}).get("tfs") or {}
        for tf, tf_entry in tfs.items():
            skills = (tf_entry or {}).get("skills") or {}
            for skill, skill_result in skills.items():
                findings.extend(_scan_l2_skill(skill_result, ticker=ticker, tf=tf, skill=skill))

    return findings


def _dominant_idea_direction(ideas: list[dict]) -> str | None:
    """Return the dominant direction among ideas with conviction >= the
    cross-TF noise floor, or ``None`` when there is no clear winner.

    Sums conviction per direction (``long`` / ``short``) over ideas whose
    conviction is at least :data:`L3_DIRECTION_MIN_CONVICTION`; returns the
    direction with the highest total. Ties return ``None`` — an ambiguous
    TF contributes no signal to the cross-TF direction comparison rather
    than fabricating a conflict.
    """
    tallies: dict[str, int] = {}
    for idea in ideas or []:
        if not isinstance(idea, dict):
            continue
        conviction = idea.get("conviction")
        if not isinstance(conviction, (int, float)) or isinstance(conviction, bool):
            continue
        if conviction < L3_DIRECTION_MIN_CONVICTION:
            continue
        direction = idea.get("direction")
        if direction not in ("long", "short"):
            continue
        tallies[direction] = tallies.get(direction, 0) + int(conviction)
    if not tallies:
        return None
    best = max(tallies.values())
    leaders = [d for d, v in tallies.items() if v == best]
    if len(leaders) != 1:
        return None
    return leaders[0]


def _scan_cross_tf_contradictions(l2_norm: dict, l3_norm: dict) -> list[dict]:
    """Flag cross-timeframe contradictions across BOTH tiers.

    Two independent axes:

    - **L2 axis** — for each ``(ticker, skill)``, flag pairs of TFs whose
      classifications disagree along the healthy-vs-weakening axis. Walks
      ``l2_norm["tickers"][*].tfs[*].skills[*]`` via
      :func:`analysis.contracts.l2_classification`.

    - **L3 axis** — for each ``(ticker, strategy)``, flag pairs of TFs
      whose dominant idea *direction* disagrees (``long`` on one TF,
      ``short`` on the other), considering only ideas with conviction >=
      :data:`L3_DIRECTION_MIN_CONVICTION` to filter noise. Walks
      ``l3_norm["tickers"][*].tfs[*].strategies[*].ideas[*]``.

    The two axes emit distinguishable shapes
    (:data:`SHAPE_CROSS_TF_CONTRADICTION` vs
    :data:`SHAPE_CROSS_TF_DIRECTION_CONFLICT`) so callers can route them
    separately. ``scan()`` calls this once, after both :func:`scan_l2` and
    :func:`scan_l3`, so an L3-only envelope still reaches the L3 axis
    (the original bug — the call lived inside ``scan_l2`` and saw an empty
    ``tickers`` dict for L3-only input).
    """
    findings: list[dict] = []
    bullish = {"HEALTHY_UPTREND", "HEALTHY_PULLBACK_UPTREND"}
    bearish = {"HEALTHY_DOWNTREND"}
    weakening = {"WEAKENING", "DEGRADING"}

    l2_tickers = (l2_norm or {}).get("tickers") or {}
    l3_tickers = (l3_norm or {}).get("tickers") or {}

    # --- L2 axis: healthy-vs-weakening classification contradiction ---
    for ticker, t_entry in l2_tickers.items():
        tfs = (t_entry or {}).get("tfs") or {}
        # Build skill -> {tf: classification} map.
        skill_to_tf_class: dict[str, dict[str, str | None]] = {}
        for tf, tf_entry in tfs.items():
            for skill, skill_result in (tf_entry or {}).get("skills", {}).items():
                cls = l2_classification(skill_result, skill_name=skill)
                if cls is None:
                    continue
                skill_to_tf_class.setdefault(skill, {})[tf] = cls

        for skill, tf_class in skill_to_tf_class.items():
            tf_list = sorted(tf_class.keys())
            for i, tf_a in enumerate(tf_list):
                cls_a = tf_class[tf_a]
                for tf_b in tf_list[i + 1 :]:
                    cls_b = tf_class[tf_b]
                    if cls_a == cls_b:
                        continue
                    # Healthy vs weakening contradiction.
                    a_healthy = cls_a in bullish
                    a_bear_healthy = cls_a in bearish
                    a_weaken = cls_a in weakening
                    b_healthy = cls_b in bullish
                    b_bear_healthy = cls_b in bearish
                    b_weaken = cls_b in weakening
                    if (a_healthy and b_weaken) or (b_healthy and a_weaken):
                        findings.append(
                            _cross_tf_finding(
                                ticker=ticker,
                                skill=skill,
                                tf_a=tf_a,
                                class_a=cls_a,
                                tf_b=tf_b,
                                class_b=cls_b,
                            )
                        )
                    elif (a_bear_healthy and b_weaken) or (b_bear_healthy and a_weaken):
                        findings.append(
                            _cross_tf_finding(
                                ticker=ticker,
                                skill=skill,
                                tf_a=tf_a,
                                class_a=cls_a,
                                tf_b=tf_b,
                                class_b=cls_b,
                            )
                        )

    # --- L3 axis: cross-TF direction conflict ---
    for ticker, t_entry in l3_tickers.items():
        tfs = (t_entry or {}).get("tfs") or {}
        # Build strategy -> {tf: dominant_direction} map.
        strat_to_tf_dir: dict[str, dict[str, str]] = {}
        for tf, tf_entry in tfs.items():
            for strategy, strategy_result in (tf_entry or {}).get("strategies", {}).items():
                ideas = (strategy_result or {}).get("ideas") or []
                dom = _dominant_idea_direction(ideas)
                if dom is None:
                    continue
                strat_to_tf_dir.setdefault(strategy, {})[tf] = dom

        for strategy, tf_dir in strat_to_tf_dir.items():
            tf_list = sorted(tf_dir.keys())
            for i, tf_a in enumerate(tf_list):
                dir_a = tf_dir[tf_a]
                for tf_b in tf_list[i + 1 :]:
                    dir_b = tf_dir[tf_b]
                    if dir_a == dir_b:
                        continue
                    findings.append(
                        _cross_tf_direction_finding(
                            ticker=ticker,
                            strategy=strategy,
                            tf_a=tf_a,
                            dir_a=dir_a,
                            tf_b=tf_b,
                            dir_b=dir_b,
                        )
                    )
    return findings


# -- L3 detection -------------------------------------------------------------


def _conviction_distribution(ideas: list[dict]) -> dict[int, int]:
    dist: dict[int, int] = {}
    for idea in ideas or []:
        c = idea.get("conviction")
        if not isinstance(c, int):
            continue
        dist[c] = dist.get(c, 0) + 1
    return dist


def _scan_l3_strategy(strategy_result: dict | None, *, ticker: str, tf: str, strategy: str) -> list[dict]:
    if not strategy_result or not isinstance(strategy_result, dict):
        return []
    ideas = strategy_result.get("ideas") or []
    n = len(ideas)
    if n < L3_CALIBRATION_MIN_IDEAS:
        return []
    dist = _conviction_distribution(ideas)
    high = sum(v for k, v in dist.items() if k >= L3_CALIBRATION_HIGH_CONV)
    if high > L3_CALIBRATION_MAX_HIGH_CONV:
        return []
    return [
        _calibration_skew_finding(
            ticker=ticker,
            tf=tf,
            ideas_count=n,
            high_conv=high,
            conv_dist={k: dist[k] for k in sorted(dist)},
        )
    ]


def scan_l3(l3_data: dict) -> list[dict]:
    """Run all L3 detectors on a normalized L3 dataset.

    ``l3_data`` shape mirrors :func:`scan_l2` but with ``strategies`` per
    (ticker, tf) instead of ``skills``.
    """
    findings: list[dict] = []
    tickers = (l3_data or {}).get("tickers") or {}
    for ticker, t_entry in tickers.items():
        tfs = (t_entry or {}).get("tfs") or {}
        for tf, tf_entry in tfs.items():
            strategies = (tf_entry or {}).get("strategies") or {}
            for strategy, strategy_result in strategies.items():
                findings.extend(_scan_l3_strategy(strategy_result, ticker=ticker, tf=tf, strategy=strategy))
    return findings


# -- input normalization ------------------------------------------------------


def normalize_l2_envelope(envelope: dict) -> dict:
    """Normalize a run-all-l2 envelope (or anything with the same shape).

    Accepts::

        {"interval": ..., "period": ..., "tickers": {"HYPEUSD": {"ticker": ..., "skills": {...}}, ...}}

    Returns::

        {"tickers": {"HYPEUSD": {"tfs": {"<the envelope's interval>": {"skills": {...}}}}}}
    """
    interval = envelope.get("interval", "?")
    out: dict[str, Any] = {"tickers": {}}
    for ticker, t_entry in (envelope.get("tickers") or {}).items():
        skills = (t_entry or {}).get("skills") or {}
        out["tickers"][ticker] = {
            "tfs": {interval: {"skills": skills}},
        }
    return out


def normalize_l3_envelope(envelope: dict) -> dict:
    """Normalize a run-all-l3 envelope (or anything with the same shape)."""
    interval = envelope.get("interval", "?")
    out: dict[str, Any] = {"tickers": {}}
    for ticker, t_entry in (envelope.get("tickers") or {}).items():
        strategies = (t_entry or {}).get("strategies") or {}
        out["tickers"][ticker] = {
            "tfs": {interval: {"strategies": strategies}},
        }
    return out


def envelope_from_json(payload: dict) -> tuple[dict, dict]:
    """Auto-detect tier and return (l2_normalized, l3_normalized).

    Accepts two input shapes:

    - **Flat single-interval** (``run-all-l2`` / ``run-all-l3`` output)::
          ``{"interval": "1h", "tickers": {T: {"skills": ..., "strategies": ...}}}``
      Every ticker lands under a single TF key (the envelope's
      ``interval``).

    - **Already-normalized multi-TF** (a merged envelope that already
      carries per-TF structure, e.g. several ``run-all-l3`` runs joined
      across intervals)::
          ``{"tickers": {T: {"tfs": {"1h": {"skills": ...}, "4h": {"strategies": ...}}}}}``
      Each ``tfs`` entry is threaded through untouched, so a multi-TF
      envelope keeps its distinct timeframes — which is what the
      cross-TF detectors need to compare across TFs.

    Some envelopes (e.g. run-watchlist) carry both ``skills`` and
    ``strategies`` per ticker — both are returned populated when present.
    Crons can pipe a run-watchlist envelope through here.

    If a single ticker carries both shapes (a ``tfs`` block *and*
    direct ``skills``/``strategies``), the multi-TF shape wins: the
    direct fields are silently skipped for that ticker. The two shapes
    are documented as mutually exclusive, so this only matters for a
    merged envelope that left stray top-level fields on a ticker.
    """
    l2_out: dict[str, Any] = {"tickers": {}}
    l3_out: dict[str, Any] = {"tickers": {}}
    interval = payload.get("interval", "?")
    for ticker, t_entry in (payload.get("tickers") or {}).items():
        # Already-normalized multi-TF shape: tickers.<T>.tfs.<TF>.{skills,strategies}
        tfs = (t_entry or {}).get("tfs") or {}
        if tfs:
            for tf, tf_entry in tfs.items():
                skills = (tf_entry or {}).get("skills") or {}
                strategies = (tf_entry or {}).get("strategies") or {}
                if skills:
                    l2_out["tickers"].setdefault(ticker, {"tfs": {}})
                    l2_out["tickers"][ticker]["tfs"].setdefault(tf, {"skills": {}})
                    l2_out["tickers"][ticker]["tfs"][tf]["skills"] = skills
                if strategies:
                    l3_out["tickers"].setdefault(ticker, {"tfs": {}})
                    l3_out["tickers"][ticker]["tfs"].setdefault(tf, {"strategies": {}})
                    l3_out["tickers"][ticker]["tfs"][tf]["strategies"] = strategies
            continue
        # Flat single-interval shape: tickers.<T>.{skills,strategies}
        skills = (t_entry or {}).get("skills") or {}
        strategies = (t_entry or {}).get("strategies") or {}
        if skills:
            l2_out["tickers"].setdefault(ticker, {"tfs": {}})
            l2_out["tickers"][ticker]["tfs"].setdefault(interval, {"skills": {}})
            l2_out["tickers"][ticker]["tfs"][interval]["skills"] = skills
        if strategies:
            l3_out["tickers"].setdefault(ticker, {"tfs": {}})
            l3_out["tickers"][ticker]["tfs"].setdefault(interval, {"strategies": {}})
            l3_out["tickers"][ticker]["tfs"][interval]["strategies"] = strategies
    return l2_out, l3_out


def translate_state_tracker(state: dict) -> list[dict]:
    """Translate ``swing_scan_state.json`` open_findings into bug-scan findings.

    The state tracker is human-authored with tags already set, so this is
    mostly a schema bridge: parse the ``summary`` field for ticker/TF/shape
    and emit each row in the bug-scan envelope format.
    """
    findings: list[dict] = []
    for entry in (state or {}).get("open_findings") or []:
        summary = entry.get("summary", "")
        tag = entry.get("tag", TAG_INFO)
        # Map tag.
        if tag == TAG_BUG:
            severity = "high"
        elif tag == TAG_DRIFT:
            severity = "medium"
        else:
            severity = "low"
        # Best-effort: surface summary as-is, leave ticker/tf/skill empty
        # since the state tracker doesn't carry structured fields.
        findings.append(
            {
                "tag": tag,
                "shape": "state_tracker",
                "summary": summary,
                "ticker": None,
                "tf": None,
                "skill": None,
                "ticks_seen": entry.get("ticks_seen"),
                "first_seen": entry.get("first_seen"),
                "last_seen": entry.get("last_seen"),
                "key": entry.get("key"),
                "severity": severity,
            }
        )
    return findings


# -- top-level scan -----------------------------------------------------------


def scan(input_data: dict) -> dict:
    """Top-level: run all detectors and return the bug-scan envelope.

    ``input_data`` may be:
      - A normalized dict with ``tickers.<T>.tfs.<TF>.{skills, strategies}``.
      - A run-all-l2 or run-all-l3 envelope (auto-detected by presence of
        ``skills`` vs ``strategies`` keys).
      - A state tracker (``open_findings`` key) — translated, not detected.
      - A pre-baked dict with a top-level ``findings`` list (passthrough).
    """
    if not input_data:
        return {"ok": True, "findings": []}

    # Pre-baked findings: passthrough.
    if isinstance(input_data.get("findings"), list) and "tickers" not in input_data:
        return {"ok": True, "findings": list(input_data["findings"])}

    # State tracker: translate.
    if "open_findings" in input_data:
        return {"ok": True, "findings": translate_state_tracker(input_data)}

    # Auto-detect L2 / L3 envelopes by key shape.
    l2_norm, l3_norm = envelope_from_json(input_data)

    findings: list[dict] = []
    findings.extend(scan_l2(l2_norm))
    findings.extend(scan_l3(l3_norm))
    # Cross-TF detectors run AFTER both tiers so they see the full merged
    # picture — an L3-only envelope still reaches the L3 direction axis
    # (the call used to live inside scan_l2 and saw an empty tickers dict).
    findings.extend(_scan_cross_tf_contradictions(l2_norm, l3_norm))
    return {"ok": True, "findings": findings}


# -- fresh fetch --------------------------------------------------------------


def fetch_l2_l3_for(
    tickers: list[str],
    *,
    intervals: list[str],
    periods: list[str],
    source: str | None = None,
) -> tuple[dict, dict]:
    """Fetch candles once per (ticker, interval) and run all L2 + L3 skills.

    Returns (l2_data, l3_data) normalized to the scan_l2/scan_l3 input shape.
    """
    from analysis.data import fetch_ohlc
    from analysis.skill_loader import load_skill

    l2_out: dict[str, Any] = {"tickers": {}}
    l3_out: dict[str, Any] = {"tickers": {}}
    for ticker in tickers:
        l2_out["tickers"].setdefault(ticker, {"tfs": {}})
        l3_out["tickers"].setdefault(ticker, {"tfs": {}})
        for interval, period in zip(intervals, periods, strict=False):
            candles = fetch_ohlc(ticker, interval=interval, period=period, source=source)
            if not candles:
                l2_out["tickers"][ticker]["tfs"][interval] = {"skills": {"_fetch": {"error": "no data"}}}
                l3_out["tickers"][ticker]["tfs"][interval] = {
                    "strategies": {"_fetch": {"ideas": [], "narrative": "no data"}}
                }
                continue

            l2_skills_out: dict[str, Any] = {}
            for skill_name in L2_SKILLS:
                mod = load_skill(skill_name)
                if mod is None:
                    l2_skills_out[skill_name] = {"error": "skill not found"}
                    continue
                try:
                    l2_skills_out[skill_name] = mod.analyze(candles, interval=interval, period=period)
                except Exception as e:
                    l2_skills_out[skill_name] = {"error": str(e)}

            l3_strats_out: dict[str, Any] = {}
            for strat_name in L3_STRATEGIES:
                mod = load_skill(strat_name)
                if mod is None:
                    l3_strats_out[strat_name] = {
                        "ideas": [],
                        "narrative": "skill not found",
                    }
                    continue
                try:
                    l3_strats_out[strat_name] = mod.analyze(candles, ticker=ticker, interval=interval, period=period)
                except Exception as e:
                    l3_strats_out[strat_name] = {
                        "ideas": [],
                        "narrative": f"error: {e}",
                    }

            l2_out["tickers"][ticker]["tfs"][interval] = {"skills": l2_skills_out}
            l3_out["tickers"][ticker]["tfs"][interval] = {"strategies": l3_strats_out}
    return l2_out, l3_out


# -- CLI glue ----------------------------------------------------------------


def _read_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def default_state_path() -> str:
    """Default path for the swing-scan state tracker JSON.

    Resolves to ``$XDG_DATA_HOME/market-skills/swing_scan_state.json``.
    Raises :class:`OSError` when ``XDG_DATA_HOME`` is unset — the
    library deliberately does not paper over with a host-specific
    fallback (see AGENTS.md "What to avoid"). Callers may pass
    ``--from-state=PATH`` to use an explicit location.
    """
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        raise OSError(
            "XDG_DATA_HOME is not set; cannot resolve the swing-scan state "
            "path. Set XDG_DATA_HOME or pass --from-state=PATH explicitly."
        )
    return os.path.join(base, "market-skills", "swing_scan_state.json")


def _default_state_path() -> str:
    return default_state_path()


def run_scan(
    *,
    tickers: list[str] | None = None,
    intervals: list[str] | None = None,
    periods: list[str] | None = None,
    source: str | None = None,
    from_state: str | None = None,
    from_json: str | None = None,
    with_chop_score: bool = False,
) -> dict:
    """Public entry: dispatch to the right input mode, run scan, return envelope.

    Args:
        tickers: positional tickers for the fresh-fetch mode.
        intervals: comma-separated list, paired 1:1 with ``periods``.
        periods: comma-separated list, paired 1:1 with ``intervals``.
        source: data provider prefix (e.g. ``hl:``, ``yf:``).
        from_state: path to a swing-scan state tracker JSON.
        from_json: path to a run-all-l2 or run-all-l3 envelope.
        with_chop_score: also append current tick's L3 ideas to the rolling
            history and emit a ``chop_score`` finding from the last N ticks.
            Opt-in so callers that don't need regime context don't pay the
            file-write cost.
    """
    if from_state:
        envelope = scan(_read_json(from_state))
        if with_chop_score:
            envelope["findings"].extend(_chop_score_findings())
        return envelope
    if from_json:
        payload = _read_json(from_json)
        if os.environ.get("BUG_SCAN_FROM_JSON_DEBUG") == "1":
            l2_norm, l3_norm = envelope_from_json(payload)
            l2_keys = len((l2_norm or {}).get("tickers") or {})
            l3_keys = len((l3_norm or {}).get("tickers") or {})
            print(f"  bug-scan from-json: l2_keys={l2_keys}, l3_keys={l3_keys}", file=sys.stderr)
        envelope = scan(payload)
        if with_chop_score:
            envelope["findings"].extend(_chop_score_findings())
        return envelope
    if not tickers:
        return {"ok": False, "error": "no input: provide tickers, --from-state, or --from-json"}
    if not intervals:
        intervals = ["1d"]
    if not periods:
        periods = ["1y"]
    if len(intervals) != len(periods):
        # Pad: if user passed a single period, reuse it for every interval.
        if len(periods) == 1:
            periods = periods * len(intervals)
        else:
            return {
                "ok": False,
                "error": f"interval/period count mismatch: {intervals} vs {periods}",
            }
    l2_data, l3_data = fetch_l2_l3_for(tickers, intervals=intervals, periods=periods, source=source)
    findings = scan_l2(l2_data) + scan_l3(l3_data) + _scan_cross_tf_contradictions(l2_data, l3_data)
    if with_chop_score:
        # Persist the current tick's ideas to history, then read the
        # chop_score from the now-updated rolling window.
        all_ideas = _collect_ideas(l3_data)
        if all_ideas:
            analysis_regime_append_tick(all_ideas)
        findings.extend(_chop_score_findings())
    return {"ok": True, "findings": findings}


def _collect_ideas(l3_data: dict) -> list[dict]:
    """Flatten L3 strategy results into a single list of idea dicts."""
    out: list[dict] = []
    tickers = (l3_data or {}).get("tickers") or {}
    for _ticker, t_entry in tickers.items():
        tfs = (t_entry or {}).get("tfs") or {}
        for _tf, tf_entry in tfs.items():
            strategies = (tf_entry or {}).get("strategies") or {}
            for _strategy, strategy_result in strategies.items():
                if not isinstance(strategy_result, dict):
                    continue
                for idea in strategy_result.get("ideas") or []:
                    if isinstance(idea, dict):
                        out.append(idea)
    return out


def _chop_score_findings() -> list[dict]:
    """Read the rolling L3 idea history and emit a chop_score finding."""
    from analysis import chop as _regime

    summary = _regime.chop_score_from_history()
    if not summary:
        return []
    return [
        _chop_score_finding(
            score=summary["score"],
            window_ticks=summary["window_ticks"],
            ideas_count=summary["ideas_count"],
            low_count=summary["low_count"],
        )
    ]


def analysis_regime_append_tick(ideas: list[dict]) -> int:
    """Thin wrapper around ``analysis.chop.append_tick`` so callers don't
    need to import the module separately.
    """
    from analysis import chop as _regime

    return _regime.append_tick(ideas)


# -- display formatting -------------------------------------------------------


def _severity_rank(sev: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "info": 3}.get(sev, 4)


def format_for_terminal(envelope: dict, *, max_lines: int = 50) -> str:
    """Human-readable one-line-per-finding summary, sorted BUG > DRIFT > INFO."""
    findings = envelope.get("findings") or []
    if not findings:
        return "  (no findings)"
    findings = sorted(findings, key=lambda f: (_severity_rank(f.get("severity", "info")), f.get("tag", "")))
    lines = []
    for f in findings[:max_lines]:
        tag = f.get("tag", TAG_INFO)
        ticker = f.get("ticker") or "-"
        tf = f.get("tf") or "-"
        skill = f.get("skill") or "-"
        summary = f.get("summary", "")
        lines.append(f"  {tag:6s} {ticker:10s} {tf:6s} {skill:24s} {summary}")
    if len(findings) > max_lines:
        lines.append(f"  ... and {len(findings) - max_lines} more (use --json to see all)")
    return "\n".join(lines)
