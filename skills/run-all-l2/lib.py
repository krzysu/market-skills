"""run-all-l2 — fetch candles once per ticker, run all L2 pattern skills in-process."""

import functools
import importlib.util
import os

L2_SKILLS = [
    "market-accumulation",
    "market-breakout",
    "market-exhaustion",
    "market-liquidity-sweep",
    "market-trend-analysis",
    "market-trend-quality",
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
    """Run all L2 skills on cached candles.

    Returns dict with structure:
        {
            "ticker": <str>,
            "skills": {<skill_name>: <L2 result dict>, ...}
        }
    """
    skills_out = {}
    for skill_name in L2_SKILLS:
        mod = _load_skill(skill_name)
        if mod is None:
            skills_out[skill_name] = {"error": "skill not found"}
            continue
        try:
            skills_out[skill_name] = mod.analyze(candles, interval=interval, period=period)
        except Exception as e:
            skills_out[skill_name] = {"error": str(e)}
    return {"ticker": ticker, "skills": skills_out}
