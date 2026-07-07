"""run-all-l3 — fetch candles once per ticker, run all L3 strategy skills in-process."""

import inspect

from analysis.formatting import round_price
from analysis.registry import l3_strategies
from analysis.skill_loader import load_skill


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


def _normalize_idea(idea: dict) -> dict:
    """Populate the flat envelope fields consumers expect.

    The canonical L3 idea dict uses ``stop_loss``, ``take_profit[i]``,
    ``rr_to_tp[i]``, and ``take_profit_ideal[i]``. Some consumers (and
    the l3-conviction-scan ``extract_ideas`` shim) prefer the flat shape
    with ``stop``, ``tp1``/``tp2``/``tp3``, ``rr_tp1``/``rr_tp2``/``rr_tp3``,
    and ``tp1_pct``. This function populates the flat fields when the
    canonical ones exist, so consumers don't have to fork on shape.

    Idempotent: if the flat fields are already present they're preserved
    (consumer override wins). If neither form is present the idea is
    passed through untouched — a strategy that genuinely has no bracket
    data shouldn't get fake zeros.
    """
    if not isinstance(idea, dict):
        return idea

    if "stop" not in idea and idea.get("stop_loss") is not None:
        idea["stop"] = idea["stop_loss"]

    targets = idea.get("take_profit") or []
    ideal = idea.get("take_profit_ideal") or []
    for i, flat_key in enumerate(("tp1", "tp2", "tp3")):
        if flat_key in idea:
            continue
        if i < len(targets) and targets[i] is not None:
            idea[flat_key] = round_price(targets[i])
        elif i < len(ideal) and ideal[i] is not None:
            idea[flat_key] = round_price(ideal[i])

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


def _normalize_result(result: dict) -> dict:
    """Apply :func:`_normalize_idea` to every idea in an L3 strategy result."""
    if not isinstance(result, dict):
        return result
    ideas = result.get("ideas")
    if isinstance(ideas, list):
        result["ideas"] = [_normalize_idea(i) for i in ideas]
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
            strategies_out[strategy_name] = _normalize_result(mod.analyze(candles, **kwargs))
        except Exception as e:
            strategies_out[strategy_name] = {"ideas": [], "narrative": f"error: {e}"}
    return {"ticker": ticker, "strategies": strategies_out}
