"""run-all-l3 — fetch candles once per ticker, run all L3 strategy skills in-process."""

import hashlib
import inspect

from analysis.contracts import compute_rr_to_tp
from analysis.formatting import round_price
from analysis.registry import l3_strategies
from analysis.skill_loader import load_skill

# Stable tag vocabulary for L3 ``rejection_reasons``. Populated by
# :func:`_classify_rejection` when a strategy returns ``ideas: []``;
# the LLM agent can branch on these tags without parsing the
# ``narrative`` string. New tags must be lower_snake_case and added to
# the dict literal below to keep the LLM-readable taxonomy stable
# across strategy refactors.
_REJECTION_REASON_TAGS = {
    "insufficient_data": "not enough candles to evaluate",
    "missing_pattern": "the upstream L2 pattern did not fire",
    "missing_trend": "trend-quality / trend classification absent",
    "missing_accumulation_pattern": "no accumulation / spring / reaccumulation signal",
    "missing_breakout_confirmation": "breakout signal lacked volume or squeeze confirmation",
    "missing_s_r_alignment": "no price-vs-S/R alignment at decision time",
    "missing_sweep": "liquidity sweep signal absent",
    "missing_exhaustion": "exhaustion / blowoff / capitulation signal absent",
    "missing_oversold": "RSI not at oversold extreme",
    "missing_overbought": "RSI not at overbought extreme",
    "missing_volume_confirmation": "volume_ratio or OBV trend not confirming",
    "missing_funding_rate": "funding rate data returned None",
    "missing_funding_extreme": "funding rate not extreme enough to emit an idea",
    "tp_ladder_invalid": "TP ladder structurally invalid (post-validator rejection)",
    "stop_too_tight": "stop distance below 2% swing-mode minimum",
}


def _strategy_accepts(mod, param_name: str) -> bool:
    """Return True iff ``mod.analyze`` declares ``param_name`` as a parameter.

    Defensive guard against signature drift: callers can safely thread
    kwargs without raising TypeError if a downstream strategy hasn't
    been updated to accept them yet. Lets us batch-pass ``asset_class``
    to the registry without coupling the kwarg list to every strategy
    individually.
    """
    try:
        sig = inspect.signature(mod.analyze)
    except (TypeError, ValueError):
        return False
    return param_name in sig.parameters


def _classify_rejection(strategy_name: str, narrative: str) -> list[str]:
    """Map an L3 strategy's empty-ideas narrative to stable tag list.

    Pure substring match against the strategy's known rejection
    phrases. Tags are stable strings (see ``_REJECTION_REASON_TAGS``
    for the vocabulary); the ``narrative`` string is for human render
    only. Falls back to ``["unknown"]`` when the narrative matches no
    known phrase — preserves visibility of an unexpected shape instead
    of returning ``[]``.
    """
    if not narrative:
        return ["unknown"]
    n = narrative.lower()
    tags: list[str] = []
    if n.startswith("insufficient data"):
        return ["insufficient_data"]
    if "tp" in n and ("dead zone" in n or "must" in n):
        tags.append("tp_ladder_invalid")
    if "stop" in n and ("minimum" in n or "below" in n or "noise risk" in n):
        tags.append("stop_too_tight")

    # Strategy-specific phrase → tag mapping. Keep these conservative:
    # only fire a tag when the narrative explicitly contains the phrase,
    # so we don't misclassify an unexpected narrative.
    phrase_map = {
        "strategy-trend-follow": {
            "no trend-follow setup": ["missing_trend"],
            "trend-quality unavailable": ["missing_trend"],
        },
        "strategy-mean-reversion": {
            "rsi not at extreme": ["missing_oversold", "missing_overbought"],
            "no mean-reversion setup": ["missing_oversold", "missing_overbought"],
        },
        "strategy-breakout-confirm": {
            "no confirmed breakout": ["missing_breakout_confirmation"],
        },
        "strategy-accumulation-swing": {
            "missing accumulation pattern": ["missing_accumulation_pattern"],
            "no accumulation swing setup": ["missing_accumulation_pattern", "missing_trend"],
        },
        "strategy-exhaustion-fade": {
            "no exhaustion fade setup": ["missing_exhaustion", "missing_s_r_alignment"],
            "missing exhaustion pattern": ["missing_exhaustion"],
        },
        "strategy-liquidity-sweep": {
            "sweep, accumulation, or volume confirmation missing": ["missing_sweep"],
            "no liquidity sweep setup": ["missing_sweep"],
        },
        "strategy-funding-carry": {
            "funding rate unavailable": ["missing_funding_rate"],
            "no funding carry setup": ["missing_funding_extreme"],
        },
    }
    strat_phrases = phrase_map.get(strategy_name, {})
    for phrase, phrase_tags in strat_phrases.items():
        if phrase in n:
            tags.extend(phrase_tags)

    if not tags:
        tags.append("unknown")
    # Dedup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _idea_id(strategy_name: str, ticker: str, idea: dict) -> str:
    """Deterministic per-idea id: sha1(strategy|ticker|bracket-signature).

    Same (strategy, ticker, direction, entry, stop, tp1, tp2, tp3)
    always hashes to the same id, so consumers that re-derive ideas
    from cached candles (backtests, paper-traders) get the same id
    without needing uuid persistence. Strips the ``veto_reasons`` and
    ``reasoning`` fields from the hash inputs so a re-run after a
    minor wording change keeps the same id.
    """
    parts = [
        strategy_name,
        ticker,
        str(idea.get("direction", "")),
        f"{idea.get('entry_price')}",
        f"{idea.get('stop_loss')}",
        *[str(t) for t in (idea.get("take_profit") or [])],
    ]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:16]


def _normalize_idea(idea: dict, *, strategy_name: str, ticker: str) -> dict:
    """Populate the canonical L3Idea schema on every idea.

    Canonical shape (always present after this function returns):
      - ``strategy_name``: the producing L3 strategy
      - ``idea_id``: deterministic sha1-based id (stable across re-runs)
      - ``pair`` (already set by strategies), ``direction``,
        ``conviction``, ``entry_price``, ``stop_loss``,
        ``take_profit``: list of 3 floats (canonical 3-TP ladder)
      - ``entry_range``: [low, high] — from the strategy when present,
        or computed as [entry, entry] when the strategy only emits a
        single entry price
      - ``rr_to_tp``: [rr_tp1, rr_tp2, rr_tp3] — computed if missing

    Plus the flat mirror fields consumers prefer (back-compat for the
    ``l3-conviction-scan`` extractor):
      - ``stop``, ``tp1``/``tp2``/``tp3``, ``rr_tp1``/``rr_tp2``/``rr_tp3``,
        ``tp1_pct``

    Idempotent: existing fields (strategy override) are preserved.

    Skipped for degenerate ideas: when the idea has neither
    ``entry_price`` nor ``take_profit``, we treat it as a stub and pass
    it through untouched. This preserves the legacy ``{"direction":
    "long"}`` shape that downstream tests + the l3-conviction-scan
    extractor rely on for sentinel values, and avoids adding placeholder
    fields that would mislead a consumer reading an actually-degenerate
    idea.
    """
    if not isinstance(idea, dict):
        return idea

    has_bracket = idea.get("entry_price") is not None or idea.get("take_profit")
    if not has_bracket:
        return idea

    if not idea.get("strategy_name"):
        idea["strategy_name"] = strategy_name

    if not idea.get("idea_id"):
        idea["idea_id"] = _idea_id(strategy_name, ticker, idea)

    if not idea.get("pair"):
        idea["pair"] = ticker

    # entry_range — strategies emit either [low, high] or rely on
    # entry_price alone. When only entry_price is set, mirror it so
    # consumers reading entry_range[0] don't see a malformed [None, None].
    if not idea.get("entry_range") and idea.get("entry_price") is not None:
        e = idea["entry_price"]
        idea["entry_range"] = [e, e]

    # take_profit — pad to 3 entries with None if a strategy emitted
    # fewer TPs. The validator downstream rejects <3, but the
    # normalization runs first so the JSON envelope shape is stable
    # for LLM agents regardless of strategy quirks.
    tps = list(idea.get("take_profit") or [])
    while len(tps) < 3:
        tps.append(None)
    idea["take_profit"] = tps

    # rr_to_tp — compute from entry/stop/TP if the strategy didn't
    # already set it AND didn't already provide individual ``rr_tp*``
    # flat fields. Strategies that emit hand-built envelopes (e.g. the
    # l3-conviction-scan extractor fanning out a manual R:R) get their
    # values preserved; only "raw" canonical-only ideas get the
    # auto-computed ladder. Mirrors analysis.contracts.compute_rr_to_tp.
    # Compute from the *original* (unpadded) TP list — None sentinels
    # would crash the direction-asymmetric arithmetic.
    has_individual_rr = any(idea.get(f"rr_tp{i}") is not None for i in (1, 2, 3))
    if not idea.get("rr_to_tp") and not has_individual_rr:
        pre_pad_idea = dict(idea)
        pre_pad_idea["take_profit"] = [t for t in tps if t is not None]
        idea["rr_to_tp"] = compute_rr_to_tp(pre_pad_idea)

    # Flat-mirror fields for the conviction-scan extractor and any
    # downstream consumer that prefers them.
    if "stop" not in idea and idea.get("stop_loss") is not None:
        idea["stop"] = idea["stop_loss"]

    targets = idea.get("take_profit") or []
    for i, flat_key in enumerate(("tp1", "tp2", "tp3")):
        if flat_key in idea:
            continue
        if i < len(targets) and targets[i] is not None:
            idea[flat_key] = round_price(targets[i])

    rr_list = idea.get("rr_to_tp") or []
    for i, flat_key in enumerate(("rr_tp1", "rr_tp2", "rr_tp3")):
        if flat_key in idea:
            continue
        if i < len(rr_list) and rr_list[i] is not None:
            idea[flat_key] = rr_list[i]

    if "tp1_pct" not in idea and idea.get("entry_price"):
        entry = idea["entry_price"]
        tp1 = idea.get("tp1")
        if tp1 is not None and entry:
            idea["tp1_pct"] = round_price(abs(tp1 - entry) / entry * 100, ndigits=4)

    return idea


def _normalize_result(result: dict, *, strategy_name: str, ticker: str) -> dict:
    """Apply :func:`_normalize_idea` to every idea in an L3 strategy result.

    Also attaches ``rejection_reasons`` when the strategy returned an
    empty ``ideas`` list — the structured mirror of ``narrative`` that
    lets LLM agents branch on ``["missing_breakout_confirmation"]``
    instead of substring-matching the human-render string.
    """
    if not isinstance(result, dict):
        return result
    ideas = result.get("ideas")
    if isinstance(ideas, list):
        result["ideas"] = [_normalize_idea(i, strategy_name=strategy_name, ticker=ticker) for i in ideas]
        if not ideas:
            result["rejection_reasons"] = _classify_rejection(strategy_name, str(result.get("narrative") or ""))
    return result


def analyze(ticker, candles, interval="1d", period="1y", asset_class=None):
    """Run all L3 strategies on cached candles.

    Returns dict with structure:
        {
            "ticker": <str>,
            "strategies": {<strategy_name>: <L3 result dict>, ...}
        }

    Each idea inside ``strategies[<name>]["ideas"]`` is normalized to
    expose both the canonical (``stop_loss`` / ``take_profit[i]`` /
    ``rr_to_tp[i]``) and the flat (``stop`` / ``tp1`` / ``tp2`` / ``tp3``
    / ``rr_tp1`` / ``tp1_pct``) field shapes — so consumers that read
    the envelope directly don't need a separate extraction shim.

    Every idea additionally carries ``strategy_name`` and ``idea_id``
    so downstream consumers (track_record, paper-trader) can address
    a specific idea without re-parsing the outer envelope.
    """
    strategies_out = {}
    for strategy_name in l3_strategies():
        mod = load_skill(strategy_name)
        if mod is None:
            strategies_out[strategy_name] = {"ideas": [], "narrative": "skill not found"}
            continue
        kwargs = {"ticker": ticker, "interval": interval, "period": period}
        if _strategy_accepts(mod, "asset_class"):
            kwargs["asset_class"] = asset_class
        try:
            strategies_out[strategy_name] = _normalize_result(
                mod.analyze(candles, **kwargs),
                strategy_name=strategy_name,
                ticker=ticker,
            )
        except Exception as e:
            strategies_out[strategy_name] = {"ideas": [], "narrative": f"error: {e}"}
    return {"ticker": ticker, "strategies": strategies_out}
