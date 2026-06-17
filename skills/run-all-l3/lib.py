"""run-all-l3 — fetch candles once per ticker, run all L3 strategy skills in-process."""

from analysis.skill_loader import load_skill

L3_STRATEGIES = [
    "strategy-trend-follow",
    "strategy-mean-reversion",
    "strategy-breakout-confirm",
    "strategy-accumulation-swing",
    "strategy-exhaustion-fade",
    "strategy-liquidity-sweep",
]  # noqa: E501


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
        mod = load_skill(strategy_name)
        if mod is None:
            strategies_out[strategy_name] = {"ideas": [], "narrative": "skill not found"}
            continue
        try:
            strategies_out[strategy_name] = mod.analyze(candles, ticker=ticker, interval=interval, period=period)
        except Exception as e:
            strategies_out[strategy_name] = {"ideas": [], "narrative": f"error: {e}"}
    return {"ticker": ticker, "strategies": strategies_out}
