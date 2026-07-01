"""run-all-l2 — fetch candles once per ticker, run all L2 pattern skills in-process."""

from analysis.registry import l2_skills
from analysis.skill_loader import load_skill


def analyze(ticker, candles, interval="1d", period="1y"):
    """Run all L2 skills on cached candles.

    Returns dict with structure:
        {
            "ticker": <str>,
            "skills": {<skill_name>: <L2 result dict>, ...}
        }
    """
    skills_out = {}
    for skill_name in l2_skills():
        mod = load_skill(skill_name)
        if mod is None:
            skills_out[skill_name] = {"error": "skill not found"}
            continue
        try:
            skills_out[skill_name] = mod.analyze(candles, interval=interval, period=period)
        except Exception as e:
            skills_out[skill_name] = {"error": str(e)}
    return {"ticker": ticker, "skills": skills_out}
