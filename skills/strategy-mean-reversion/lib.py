"""strategy-mean-reversion — L3 strategy: fade extremes at S/R levels."""

import functools
import importlib.util
import os

from lib.indicators import compute_atr_from_candles


@functools.cache
def _load_l1_skill(name):
    lib_path = os.path.join(os.path.dirname(__file__), "..", name, "lib.py")
    if not os.path.exists(lib_path):
        return None
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def analyze(candles, interval="1d", period="1y"):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    rsi_mod = _load_l1_skill("market-rsi")
    sr_mod = _load_l1_skill("market-s-r")
    volty_mod = _load_l1_skill("market-volatility")

    err = {"error": "unavailable"}
    rsi_result = rsi_mod.analyze(candles, interval=interval, period=period) if rsi_mod else err
    sr_result = sr_mod.analyze(candles, interval=interval, period=period) if sr_mod else err
    volty_result = volty_mod.analyze(candles, interval=interval, period=period) if volty_mod else err

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    rsi_oversold = False
    rsi_overbought = False
    if "error" not in rsi_result:
        rsi = rsi_result.get("rsi_14")
        if rsi is not None:
            rsi_oversold = rsi <= 30
            rsi_overbought = rsi >= 70

    low_vol = "error" not in volty_result and volty_result.get("regime") == "LOW"
    support = sr_result.get("nearest_support") if "error" not in sr_result else None
    resistance = sr_result.get("nearest_resistance") if "error" not in sr_result else None

    if rsi_oversold and support is not None and price <= support * 1.02:
        entry = price
        stop = support - atr * 1
        risk = entry - stop
        mid = (support + (resistance or support * 1.1)) / 2
        conviction = 3 if low_vol else 2
        ideas.append(
            {
                "pair": "...",
                "direction": "long",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(support, 2), round(entry * 1.01, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(mid, 2), round(entry + risk * 2, 2)],
                "reasoning": "Oversold at support with mean-reversion setup.",
                "source_skills": ["market-rsi", "market-s-r", "market-volatility"],
            }
        )

    if rsi_overbought and resistance is not None and price >= resistance * 0.98:
        entry = price
        stop = resistance + atr * 1
        risk = stop - entry
        mid = (resistance + (support or resistance * 0.9)) / 2
        conviction = 3 if low_vol else 2
        ideas.append(
            {
                "pair": "...",
                "direction": "short",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry * 0.99, 2), round(resistance, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(mid, 2), round(entry - risk * 2, 2)],
                "reasoning": "Overbought at resistance with mean-reversion setup.",
                "source_skills": ["market-rsi", "market-s-r", "market-volatility"],
            }
        )

    if ideas:
        narrative = f"Mean-reversion setup: {', '.join(i['direction'] for i in ideas)}."
    else:
        narrative = "No mean-reversion setup — RSI not at extreme or price not at key level."

    return {"ideas": ideas, "narrative": narrative}
