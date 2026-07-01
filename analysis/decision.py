"""Decision-context — structured decision trace for trade records.

A "decision" is the full trace of what the system thought and did at trade
time: the L3 idea, macro regime, risk verdict, and any manual overrides.
This module provides the TypedDict schema, a pure-function builder, and
validation helpers.

Design contract:
  - Pure functions only (no I/O, no network, no DB). The caller fetches
    macro state / risk verdicts and passes them in.
  - Single source of truth for the DecisionContext schema — the SKILL.md
    docs reference this module rather than duplicating the field list.
  - ``build_decision_context()`` is safe to call from execution skill
    ``lib.py`` modules as well as the backfill reference doc examples.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict


class DecisionContextOverride(TypedDict):
    """Records whether the user deviated from the system's suggestion."""

    from_suggestion: bool
    field: str | None
    reason: str | None


class DecisionContextL3Idea(TypedDict):
    """L3 idea snapshot frozen at trade time."""

    direction: str  # "long" | "short"
    conviction: int | None  # 1–5
    summary: str | None
    entry_price: float | None
    stop: float | None
    tp1: float | None
    tp2: float | None
    tp3: float | None
    rr_to_tp2: float | None


class DecisionContextRegime(TypedDict):
    """Macro regime snapshot at trade time."""

    label: str | None
    fng: float | None
    btc_dominance: float | None
    divergence: str | None


class DecisionContextRiskVerdict(TypedDict):
    """Risk advisory snapshot at trade time."""

    status: str | None  # "APPROVED" | "CONCERN" | "SCALE" | "REJECT" | "UNKNOWN" | None
    position_size_pct: float | None
    concerns: list[str]


class DecisionContext(TypedDict):
    """Complete decision trace attached to a trade.

    This is the **system of record** for "what did we think and why."
    Every executed trade carries one; planned trades that didn't execute
    (risk-rejected, user-skipped) should also carry one.
    """

    intent_id: str
    source_skill: str
    l3_idea: DecisionContextL3Idea
    regime: DecisionContextRegime
    macro_signals: list[str]
    risk_verdict: DecisionContextRiskVerdict
    override: DecisionContextOverride
    captured_at: str  # ISO 8601 UTC


# ── Pure-function builder ──────────────────────────────────────────────


# Canonical mapping from Kraken's raw side token to the
# decision-context direction enum. Both lib.py modules (spot + perps)
# funnel through this so an unexpected value raises instead of being
# silently coerced to "short".
_SIDE_TO_DIRECTION: dict[str, str] = {"buy": "long", "sell": "short"}


def direction_from_side(side_raw: str | None) -> str:
    """Map a raw Kraken side token (``"buy"`` / ``"sell"``) to the
    canonical decision-context direction (``"long"`` / ``"short"``).

    Case-insensitive on the input (Kraken's API lowercases the side
    upstream, but defensive). Raises ``ValueError`` for any other
    value — never silently coerces to ``"short"``. ``None`` and empty
    strings also raise; callers that have no side should pass
    ``"unknown"`` explicitly.
    """
    if not isinstance(side_raw, str) or not side_raw:
        raise ValueError(f"side must be 'buy' or 'sell', got {side_raw!r}")
    norm = side_raw.strip().lower()
    if norm not in _SIDE_TO_DIRECTION:
        raise ValueError(f"side must be 'buy' or 'sell', got {side_raw!r}")
    return _SIDE_TO_DIRECTION[norm]


def compute_rr_to_tp2(
    direction: str | None,
    entry: float | None,
    stop: float | None,
    tp2: float | None,
) -> float | None:
    """Direction-aware R:R to TP2.

    Long:  (tp2 - entry) / (entry - stop)
    Short: (entry - tp2) / (stop - entry)

    Returns ``None`` when any required price is missing or when the
    denominator is zero (degenerate stop == entry).
    """
    if direction is None or entry is None or stop is None or tp2 is None:
        return None
    if direction == "short":
        denom = stop - entry
        if denom <= 0:
            return None
        return round((entry - tp2) / denom, 2)
    denom = entry - stop
    if denom <= 0:
        return None
    return round((tp2 - entry) / denom, 2)


def build_decision_context(
    *,
    intent_id: str,
    source_skill: str,
    direction: str | None,
    conviction: int | None,
    summary: str | None,
    entry_price: float | None,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    tp3: float | None,
    regime_label: str | None = None,
    regime_fng: float | None = None,
    regime_btc_dominance: float | None = None,
    regime_divergence: str | None = None,
    macro_signals: list[str] | None = None,
    risk_status: str | None = None,
    risk_position_size_pct: float | None = None,
    risk_concerns: list[str] | None = None,
    override_from_suggestion: bool = False,
    override_field: str | None = None,
    override_reason: str | None = None,
) -> DecisionContext:
    """Build a DecisionContext from explicit keyword arguments.

    This is a **pure function** — no I/O, no side effects. Every field
    is passed in by the caller (execution skill lib.py, backfill script,
    test). Missing fields remain ``None`` rather than being guessed.
    """
    rr = compute_rr_to_tp2(direction, entry_price, stop, tp2)

    return DecisionContext(
        intent_id=intent_id,
        source_skill=source_skill,
        l3_idea=DecisionContextL3Idea(
            direction=direction or "unknown",
            conviction=conviction,
            summary=summary,
            entry_price=entry_price,
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            rr_to_tp2=rr,
        ),
        regime=DecisionContextRegime(
            label=regime_label,
            fng=regime_fng,
            btc_dominance=regime_btc_dominance,
            divergence=regime_divergence,
        ),
        macro_signals=macro_signals or [],
        risk_verdict=DecisionContextRiskVerdict(
            status=risk_status,
            position_size_pct=risk_position_size_pct,
            concerns=risk_concerns or [],
        ),
        override=DecisionContextOverride(
            from_suggestion=override_from_suggestion,
            field=override_field,
            reason=override_reason,
        ),
        captured_at=datetime.now(UTC).isoformat(),
    )


def build_decision_context_from_idea(
    *,
    intent_id: str,
    source_skill: str,
    idea: dict,
    regime_label: str | None = None,
    regime_fng: float | None = None,
    regime_btc_dominance: float | None = None,
    regime_divergence: str | None = None,
    macro_signals: list[str] | None = None,
    risk_status: str | None = None,
    risk_position_size_pct: float | None = None,
    risk_concerns: list[str] | None = None,
    override_from_suggestion: bool = False,
    override_field: str | None = None,
    override_reason: str | None = None,
) -> DecisionContext:
    """Convenience wrapper that extracts L3 fields from an idea dict.

    Accepts any dict with the same key naming as ``L3Idea`` / ``Intent``
    (e.g. ``direction``, ``conviction``, ``entry_price``, ``stop_loss``,
    ``take_profit``). This lets callers pass an L3 idea or Intent directly
    without manually extracting each price field.

    ``take_profit`` is expected to be a list of up to 3 floats;
    ``tp1``/``tp2``/``tp3`` are pulled by index. ``stop`` reads
    ``stop_loss`` if ``stop`` is absent.
    """
    direction = idea.get("direction")
    conviction = idea.get("conviction")
    summary = idea.get("summary")
    entry_price = idea.get("entry_price")
    stop = idea.get("stop") or idea.get("stop_loss")
    tps = idea.get("take_profit") or []
    tp1 = tps[0] if len(tps) > 0 else None
    tp2 = tps[1] if len(tps) > 1 else None
    tp3 = tps[2] if len(tps) > 2 else None

    return build_decision_context(
        intent_id=intent_id,
        source_skill=source_skill,
        direction=direction,
        conviction=conviction,
        summary=summary,
        entry_price=entry_price,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        regime_label=regime_label,
        regime_fng=regime_fng,
        regime_btc_dominance=regime_btc_dominance,
        regime_divergence=regime_divergence,
        macro_signals=macro_signals,
        risk_status=risk_status,
        risk_position_size_pct=risk_position_size_pct,
        risk_concerns=risk_concerns,
        override_from_suggestion=override_from_suggestion,
        override_field=override_field,
        override_reason=override_reason,
    )


# ── Validation ─────────────────────────────────────────────────────────


def validate_decision_context(dc: Any) -> list[str]:
    """Validate a DecisionContext-like dict, returning a list of issues.

    Returns an empty list when the value passes all checks. Issues are
    human-readable strings suitable for printing in a ``validate`` CLI
    command or test assertion.

    This is a **pure function** — no I/O, no side effects.
    """
    issues: list[str] = []

    if not isinstance(dc, dict):
        issues.append(f"decision_context must be a dict, got {type(dc).__name__}")
        return issues

    for field in ("intent_id", "source_skill", "captured_at"):
        if not isinstance(dc.get(field), str):
            issues.append(f"decision_context.{field} must be a string, got {type(dc.get(field)).__name__}")

    l3 = dc.get("l3_idea")
    if not isinstance(l3, dict):
        issues.append(f"decision_context.l3_idea must be a dict, got {type(l3).__name__}")
    else:
        if l3.get("direction") not in ("long", "short", "unknown"):
            issues.append(f"decision_context.l3_idea.direction must be long/short/unknown, got {l3.get('direction')!r}")
        if l3.get("conviction") is not None and not isinstance(l3["conviction"], (int, float)):
            issues.append("decision_context.l3_idea.conviction must be a number or null")
        rr = l3.get("rr_to_tp2")
        if rr is not None and not isinstance(rr, (int, float)):
            issues.append("decision_context.l3_idea.rr_to_tp2 must be a number or null")

    regime = dc.get("regime")
    if not isinstance(regime, dict):
        issues.append(f"decision_context.regime must be a dict, got {type(regime).__name__}")

    risk = dc.get("risk_verdict")
    if not isinstance(risk, dict):
        issues.append(f"decision_context.risk_verdict must be a dict, got {type(risk).__name__}")
    else:
        valid_statuses = ("APPROVED", "CONCERN", "SCALE", "REJECT", "UNKNOWN", None)
        if risk.get("status") not in valid_statuses:
            issues.append(
                f"decision_context.risk_verdict.status must be one of {valid_statuses}, got {risk.get('status')!r}"
            )
        if not isinstance(risk.get("concerns"), list):
            issues.append("decision_context.risk_verdict.concerns must be a list")

    override = dc.get("override")
    if not isinstance(override, dict):
        issues.append(f"decision_context.override must be a dict, got {type(override).__name__}")
    else:
        if not isinstance(override.get("from_suggestion"), bool):
            issues.append("decision_context.override.from_suggestion must be a bool")

    return issues


__all__ = [
    "DecisionContext",
    "DecisionContextL3Idea",
    "DecisionContextOverride",
    "DecisionContextRegime",
    "DecisionContextRiskVerdict",
    "build_decision_context",
    "build_decision_context_from_idea",
    "compute_rr_to_tp2",
    "direction_from_side",
    "validate_decision_context",
]
