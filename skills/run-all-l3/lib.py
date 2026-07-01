"""run-all-l3 — fetch candles once per ticker, run all L3 strategy skills in-process."""

import inspect

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


def analyze(ticker, candles, interval="1d", period="1y", asset_class=None):
    """Run all L3 strategies on cached candles.

    Returns dict with structure:
        {
            "ticker": <str>,
            "strategies": {<strategy_name>: <L3 result dict>, ...}
        }
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
            strategies_out[strategy_name] = mod.analyze(candles, **kwargs)
        except Exception as e:
            strategies_out[strategy_name] = {"ideas": [], "narrative": f"error: {e}"}
    return {"ticker": ticker, "strategies": strategies_out}
