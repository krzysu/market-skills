"""TypedDict contracts for L1, L2, and L3 skill return shapes."""

from typing import Any, NotRequired, TypedDict

SWING_MIN_STOP_DISTANCE: float = 0.02
"""Minimum stop-to-entry distance for swing-mode L3 ideas (2%).

Sub-2% stops are noise-risk in swing mode (normal volatility will stop them
out). L3 strategies call :func:`enforce_min_stop_distance` with this default;
a strategy that legitimately needs tighter stops in some regime can override
per-call.
"""


class L2Pattern(TypedDict):
    present: bool
    confidence: int
    max_confidence: int
    classification: str | None
    type: str


class L2Signal(TypedDict):
    present: bool
    weight: float


class L2Result(TypedDict):
    pattern: L2Pattern
    signals: dict[str, L2Signal]
    input_scores: dict[str, Any]
    narrative: str


class L1Result(TypedDict):
    """Minimal shared fields across all L1 skills. Skill-specific keys are added as extras."""

    current_price: NotRequired[float | None]
    score: NotRequired[int | None]
    signal: NotRequired[str | None]
    zone: NotRequired[str | None]


class L3Idea(TypedDict):
    pair: str
    direction: str  # "long" | "short"
    conviction: int  # 1–5
    version: NotRequired[str]  # L3 conviction version (e.g. "v1".."v5")
    entry_type: str  # "limit" | "market" | "stop"
    entry_price: float | None
    entry_range: NotRequired[list[float]]  # [low, high] acceptable entry window
    stop_loss: float | None
    take_profit: list[float]
    take_profit_ideal: NotRequired[list[float]]  # unrounded construction; cron can recover the exact entry ± N × risk
    rr_to_tp: NotRequired[list[float]]  # [rr_to_tp1, rr_to_tp2, rr_to_tp3]; precomputed via compute_rr_to_tp()
    reasoning: str
    source_skills: list[str]
    move_maturity_pct: NotRequired[float | None]  # (close - rolling_low) / rolling_low * 100; scales with asset_class
    entry_window_validity_pct: NotRequired[float | None]  # abs(close - entry) / entry * 100; >3 chase-risk
    veto_reasons: NotRequired[list[str]]  # soft veto tags applied at L3 emit time (chase-risk, late-move, etc.)
    asset_class: NotRequired[str | None]  # perp_dex / low_float / ai_infra — enables scaled maturity thresholds


class L3Result(TypedDict):
    ideas: list[L3Idea]
    narrative: str
    rejection_reasons: NotRequired[list[str]]  # when ideas=[]; lets LLM agents branch without parsing narrative


# --- Macro domain (cross-asset regime context) ---


class MacroInputs(TypedDict):
    """Raw macro indicators fetched per call.

    All fields are nullable: a single source failure (e.g. Alternative.me
    down, CoinGecko rate-limited, yfinance ticker delisted) must not take
    the whole signal down. The fetcher records the failure in
    ``RegimeSignal.errors`` and the corresponding input is ``None``.
    """

    fng: NotRequired[float | None]  # Alternative.me Fear & Greed, 0..100
    fng_label: NotRequired[str | None]  # "Extreme Fear".. "Extreme Greed"
    vix: NotRequired[float | None]  # yfinance ^VIX close
    dxy: NotRequired[float | None]  # yfinance DX-Y.NYB close
    us10y: NotRequired[float | None]  # yfinance ^TNX close (real-rates / liquidity proxy)
    btc_dominance: NotRequired[float | None]  # derived: btc_mcap / total_mcap * 100
    btc_dominance_source: NotRequired[str | None]  # "yf" | "coingecko" | None when both fail
    total_mcap_usd: NotRequired[float | None]  # CoinGecko /global total crypto market cap


class MacroRegime(TypedDict):
    """Derived labels over MacroInputs.

    Categorical so the LLM agent brain can read three orthogonal axes
    (risk appetite vs liquidity vs sentiment) instead of guessing from
    raw numbers. Narrate-only by design — no conviction_modifier or
    directional_filter. The L3 layer attaches this block to its
    envelope and the agent brain decides what to do.

    ``risk_appetite`` may also be ``"UNKNOWN"`` when the upstream
    ``RegimeSignal`` is incomplete (one or more fetch failures). The
    regime_consistency policy in ``analysis.risk.spot`` treats UNKNOWN
    as adverse (CONCERN) so a degraded regime can never pass silently.
    """

    risk_appetite: str  # "RISK_ON" | "NEUTRAL" | "RISK_OFF" | "CRISIS" | "UNKNOWN"
    liquidity: str  # "EASY" | "TIGHTENING" | "TIGHT" | "STRESS"
    sentiment: str  # "EXTREME_FEAR" | "FEAR" | "NEUTRAL" | "GREED" | "EXTREME_GREED"


class RegimeSignal(TypedDict):
    """Singleton macro context. See ARCHITECTURE.md 'Macro domain'.

    ``timestamp`` is the *fetch* time, not the data's intrinsic date —
    F&G is daily, CoinGecko is roughly live, and yfinance closes lag
    by minutes-hours. Callers that need "as-of" reasoning should
    read the source tickers directly.

    ``regime_note`` is a one-liner the LLM can drop into its narration
    verbatim; the structured ``regime`` block is for programmatic use.

    ``incomplete`` is ``True`` whenever ``errors`` is non-empty — the
    canonical "regime degraded" signal for downstream consumers
    (run-all-l3 stdout, regime_consistency policy). When ``True``,
    ``regime.risk_appetite`` is downgraded to ``"UNKNOWN"`` so naive
    consumers that read only the label never see a partial regime
    mislabelled as RISK_ON / RISK_OFF. The downstream policy that
    treats UNKNOWN as adverse (regime_consistency) fires accordingly.

    ``missing_inputs`` is the structured mirror of ``errors``: a list
    of input names that failed (``fng`` / ``vix`` / ``dxy`` / ``us10y``
    / ``btc_dominance`` / ``total_mcap_usd``). Lets LLM agents ask
    "which input failed?" without parsing ``regime_note`` or
    string-matching ``errors[]``. Always a list (empty when the regime
    is complete), matching the AXI envelope's structured-errors
    principle. ``btc_dominance`` / ``total_mcap_usd`` share the
    CoinGecko upstream; either appearing here means the CoinGecko
    HTTP call (or its total-mcap field) failed.
    """

    timestamp: str
    inputs: MacroInputs
    regime: MacroRegime
    errors: list[str]  # per-source fetch failures ("fng: timeout", "coingecko: 429", ...)
    incomplete: bool  # bool(errors); canonical "regime degraded" flag
    missing_inputs: list[str]  # structured: ["vix", "btc_dominance"] etc.
    regime_note: str


# --- Risk layer (advisory — not a hard gate) ---


class RiskVerdictFragment(TypedDict):
    """One policy's verdict against an Intent.

    A RiskVerdict is composed of N fragments, one per policy. The LLM reads
    the fragments to narrate a recommendation; it is NOT a hard gate. The
    user always has the final say at execution time (execution-kraken's
    interactive confirm is the actual safety layer).

    Status taxonomy:
      APPROVED  — policy has no objection. Default if the policy didn't fire.
      CONCERN   — informational; non-blocking. The LLM should mention it in
                  its narrative but execution can proceed.
      SCALE     — non-blocking, but suggests a smaller volume. The LLM
                  surfaces the suggested_volume and may decide to apply it.
      REJECT    — policy recommends NOT executing. Advisory only; the LLM
                  should narrate the reason prominently but the execution skill
                  will still respect --yes if the user explicitly overrides.
    """

    policy: str
    status: str  # "APPROVED" | "CONCERN" | "SCALE" | "REJECT"
    reason: str
    detail: NotRequired[dict[str, Any]]
    suggested_volume: NotRequired[float | None]


class RiskVerdict(TypedDict):
    """Aggregated verdict across all policies.

    `status` is the worst-case across fragments (REJECT > SCALE > CONCERN >
    APPROVED). The LLM is free to override an advisory REJECT based on
    context the policies didn't see (e.g. the user just verbally explained
    why this exception is acceptable).
    """

    intent_id: str
    pair: str
    side: str
    status: str
    fragments: list[RiskVerdictFragment]
    concerns: list[str]  # human-readable; safe-to-show summary of CONCERN fragments
    suggested_volume: NotRequired[float | None]  # populated when at least one fragment is SCALE
    narrative_hint: NotRequired[str]  # one-sentence prompt for the LLM


# --- Sanity helpers ---


def l2_fired(result: dict | None, skill_name: str = "") -> bool:
    """Single source of truth: an L2 verdict fires iff pattern.present is True AND
    classification is not None.

    Use this from every L3 strategy instead of inlining the present check. Guards
    against two invariant violations the L2 layer can produce:
      - Ghost-classification: present=False with a classification populated —
        downstream code that reads classification while trusting present will see a
        contradiction. This helper returns False, so callers treat it as not-fired.
      - Silent: present=True with classification=None — the verdict says it fired
        but the classifier cascade didn't pick a label. This helper also returns
        False, so L3 logic can't accidentally promote a half-formed verdict.
    """
    if not isinstance(result, dict):
        return False
    pattern = result.get("pattern")
    if not isinstance(pattern, dict):
        return False
    if not pattern.get("present"):
        return False
    if pattern.get("classification") is None:
        return False
    return True


def l2_classification(result: dict | None, skill_name: str = "") -> str | None:
    """Return the L2 classification only when the pattern fired. Returns None otherwise.

    Replaces inline patterns like ``classification = pattern.get("classification");
    if pattern["present"] and classification in (...):`` — single read site enforces
    the present/classification invariant from l2_fired().
    """
    if not l2_fired(result, skill_name):
        return None
    return result["pattern"].get("classification")


def validate_l3_tp_ladder(idea: dict) -> None:
    """Raise ValueError if the take_profit ladder or stop_loss is structurally invalid.

    Rejects:
      - stop_loss == entry_price (no downside protection)
      - any TP not strictly on the correct side of entry (long/short)
      - non-monotonic or duplicate TPs (degenerate ladder)
      - TP3 inside the 5% dead zone (entry < TP3 < entry × 1.05 long,
        or symmetric for short — produces degenerate R:R ≈ entry)

    Called by L3 strategies after building ``ideas[]`` so degenerate ladders
    fail loud in CI rather than silently reach the cron output.
    """
    direction = idea.get("direction")
    entry = idea.get("entry_price")
    stop = idea.get("stop_loss")
    tps = idea.get("take_profit") or []
    if entry is None or not tps:
        return  # contract handled by other validators
    if stop is not None and entry is not None and stop == entry:
        raise ValueError(
            f"L3 {idea.get('pair')} {direction} stop_loss must not equal entry_price "
            f"— zero stop (entry={entry}, stop_loss={stop})."
        )
    if direction == "long":
        if not all(tp > entry for tp in tps):
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} take_profit must all be > entry (entry={entry}, tps={tps})"
            )
        if tps != sorted(tps):
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} take_profit must be strictly ascending (entry={entry}, tps={tps})"
            )
        if len(set(tps)) < len(tps):
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} take_profit values must be distinct — "
                f"degenerate ladder (entry={entry}, tps={tps}). "
                f"Sub-$1 rounding collapses all three TPs to the same 2-dp value."
            )
        if tps[-1] < entry * 1.05:
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} TP3 must be ≥ entry × 1.05 "
                f"(entry={entry}, tp3={tps[-1]}, required>={entry * 1.05})"
            )
    elif direction == "short":
        if not all(tp < entry for tp in tps):
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} take_profit must all be < entry (entry={entry}, tps={tps})"
            )
        if tps != sorted(tps, reverse=True):
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} take_profit must be strictly descending (entry={entry}, tps={tps})"
            )
        if len(set(tps)) < len(tps):
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} take_profit values must be distinct — "
                f"degenerate ladder (entry={entry}, tps={tps}). "
                f"Sub-$1 rounding collapses all three TPs to the same 2-dp value."
            )
        if tps[-1] > entry * 0.95:
            raise ValueError(
                f"L3 {idea.get('pair')} {direction} TP3 must be ≤ entry × 0.95 "
                f"(entry={entry}, tp3={tps[-1]}, required<={entry * 0.95})"
            )


def validate_l3_tp_ladder_silent(idea: dict) -> str | None:
    """Wrap :func:`validate_l3_tp_ladder` and return the error message instead of raising.

    Returns ``None`` when the ladder is structurally valid; returns the
    ``ValueError`` message string when invalid. Used by L3 strategies as
    a safety net after the producer-side 5% clamp: if the clamp misses
    (e.g. a future strategy that doesn't apply it), the strategy
    surfaces the rejection as a structured narrative rather than letting
    the cron see a silent ``ideas: []`` (the "second silent-failure
    fingerprint" worked in the 2026-07-03 packet).
    """
    try:
        validate_l3_tp_ladder(idea)
    except ValueError as e:
        return str(e)
    return None


def l3_tp3_dead_zone_floor(entry: float) -> float:
    """Minimum TP3 for a long idea to clear the 5% dead zone after rounding.

    Returns ``entry * 1.05`` plus a magnitude-aware buffer to clear
    :func:`analysis.formatting.round_price`'s precision tier (2dp for
    |value|≥1, 4dp for ≥0.01, 6dp otherwise). Without the buffer, a clamp
    at exactly the boundary can round down past the threshold and trip
    :func:`validate_l3_tp_ladder` — e.g. PAXGUSD entry=4135.9 produces
    4342.695 which Python's banker's rounding drops to 4342.69 at 2dp,
    failing the `tps[-1] < entry * 1.05` check.
    """
    abs_entry = abs(entry)
    if abs_entry >= 1:
        buffer = 0.01
    elif abs_entry >= 0.01:
        buffer = 0.0001
    else:
        buffer = 0.000001
    return entry * 1.05 + buffer


def l3_tp3_dead_zone_ceiling(entry: float) -> float:
    """Maximum TP3 for a short idea to clear the 5% dead zone after rounding.

    Mirror of :func:`l3_tp3_dead_zone_floor` for shorts. Returns
    ``entry * 0.95`` minus the magnitude-aware buffer so the rounded
    value clears ``entry * 0.95`` from below.
    """
    abs_entry = abs(entry)
    if abs_entry >= 1:
        buffer = 0.01
    elif abs_entry >= 0.01:
        buffer = 0.0001
    else:
        buffer = 0.000001
    return entry * 0.95 - buffer


def enforce_min_stop_distance(
    idea: dict,
    min_pct: float = SWING_MIN_STOP_DISTANCE,
) -> tuple[bool, str]:
    """Reject an L3 idea whose stop is closer than ``min_pct`` to entry.

    Sub-2% stops are noise-risk setups in swing mode — they get stopped out on
    normal volatility. Returns ``(ok, narrative)``: ``ok=True`` means the idea
    is fine; ``ok=False`` means the caller should drop the idea and surface
    ``narrative`` (which contains the measured distance and the floor) in
    the strategy's return narrative.

    A missing or zero entry/stop is treated as "nothing to check" and returns
    ``(True, "")`` — degenerate prices are the job of ``validate_l3_tp_ladder``
    and the other validators, not this gate.
    """
    entry = idea.get("entry_price")
    stop = idea.get("stop_loss")
    if not entry or stop is None:
        return True, ""
    dist = abs(entry - stop) / entry
    if dist < min_pct:
        return (
            False,
            f"stop {dist:.2%} below {min_pct:.0%} swing minimum — noise risk",
        )
    return True, ""


def conviction_version(conviction: int) -> str:
    """Map conviction (1-5) to a version tag.

    v1 = weakest (1), v5 = strongest (5). Lets downstream consumers reason about
    conviction version transitions (e.g. "ALGO 1d SHORT cv=4 (upgrade v2→v4)")
    without parsing narrative strings.
    """
    return f"v{max(1, min(5, conviction))}"


def compute_rr_to_tp(idea: dict) -> list[float]:
    """Precompute the R:R to each of the 3 take_profit levels for an L3 idea.

    Returns ``[rr_to_tp1, rr_to_tp2, rr_to_tp3]`` in TP1/TP2/TP3 order, or
    ``[]`` if the idea has no entry/stop/TPs to compute against (same
    defensive behaviour as :func:`validate_l3_tp_ladder` — degenerate prices
    are that validator's job, not this helper's).

    Formula (direction-asymmetric, so consumer code never has to reimplement):

      Long:  rr = (tp - entry) / (entry - stop)
      Short: rr = (entry - tp) / (stop - entry)

    Prefers ``take_profit_ideal`` (unrounded construction) when present, else
    falls back to ``take_profit`` (2dp display). Mirrors the 2026-06-25
    producer-side enrichment pattern (commit d99f05d) so consumers can read
    a canonical R:R value without reimplementing the direction-asymmetric
    formula. Sourced from ``take_profit_ideal`` first to keep precision-clean
    on sub-$1 setups where 2dp rounding would shift the displayed TP enough
    to push ``rr`` across a gate threshold (the ALGO 4h SHORT 2026-06-25
    silent-reject shape).

    Single source of truth: L3 strategies call this from their post-build
    loop so every emitted idea carries ``rr_to_tp``. Consumers (swing-scan,
    position-watchdog, paper-trader, LLM agent brain) read it as a plain
    field — no per-strategy ladder knowledge required.
    """
    entry = idea.get("entry_price")
    stop = idea.get("stop_loss")
    tps = idea.get("take_profit_ideal") or idea.get("take_profit") or []
    if not entry or stop is None or not tps:
        return []
    direction = idea.get("direction")
    if direction == "short":
        denom = stop - entry
        if denom <= 0:
            return []
        return [round((entry - tp) / denom, 6) for tp in tps]
    denom = entry - stop
    if denom <= 0:
        return []
    return [round((tp - entry) / denom, 6) for tp in tps]


# --- AXI output envelope (see ADR-0004 + docs/AXI-REFERENCE.md) ---


class AXIEnvelope(TypedDict, total=False):
    """Canonical on-the-wire envelope emitted by every skill's `--json` mode.

    The envelope is the contract between the CLI surface and the LLM
    agent brain; the in-process TypedDicts above (L1Result, L2Result,
    L3Result, RiskVerdict, FillConfirmation, etc.) describe the
    lib.py contracts and are not affected by this shape.

    Field semantics:

      data   - skill-specific payload. May be a singleton dict, a
               list, or None for empty results (AXI principle 5:
               explicit zero-result, not bare empty).
      count  - canonical item count. Single-item skills use 1;
               list-returning skills use len(data) or a
               pre-computed aggregate (AXI principle 4). None
               when count is undefined (e.g. macro regime).
      errors - list of structured error strings. Always a list
               (empty when unset) so consumers never branch on
               None. Replaces the bare `{"error": "..."}`
               pattern that scattered across per-skill scripts
               (AXI principle 6: structured errors).
      help   - list of next-step command templates the LLM can
               drop into narration verbatim (AXI principle 9:
               contextual disclosure). Always a list, empty
               when unset.
    """

    data: Any
    count: int | None
    errors: list[str]
    help: list[str]
