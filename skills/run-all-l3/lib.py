"""run-all-l3 — fetch candles once per ticker, run all L3 strategy skills in-process."""

import functools
import importlib.util
import os

L3_STRATEGIES = [
    "strategy-trend-follow",
    "strategy-mean-reversion",
    "strategy-breakout-confirm",
    "strategy-accumulation-swing",
    "strategy-exhaustion-fade",
    "strategy-liquidity-sweep",
]


@functools.cache
def _load_skill(name):
    lib_path = os.path.join(os.path.dirname(__file__), "..", name, "lib.py")
    if not os.path.exists(lib_path):
        return None
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def analyze(ticker, candles, interval="1d", period="1y"):
    """Run all L3 strategies on cached candles.

    Returns dict with structure:
        {
            "ticker": <str>,
            "strategies": {<strategy_name>: <L3 result dict>, ...}
        }
    """
    strategies_out = {}
    for strategy_name in L3_STRATEGIES:
        mod = _load_skill(strategy_name)
        if mod is None:
            strategies_out[strategy_name] = {"ideas": [], "narrative": "skill not found"}
            continue
        try:
            result = mod.analyze(candles, interval=interval, period=period)
            for idea in result.get("ideas", []):
                idea["pair"] = ticker
            strategies_out[strategy_name] = result
        except Exception as e:
            strategies_out[strategy_name] = {"ideas": [], "narrative": f"error: {e}"}
    return {"ticker": ticker, "strategies": strategies_out}
