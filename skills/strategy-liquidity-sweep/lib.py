"""strategy-liquidity-sweep — L3 strategy: sweep + accumulation reversal."""

from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill


def analyze(candles, *, ticker, interval="1d", period="1y"):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    sweep_mod = load_skill("market-liquidity-sweep")
    accum_mod = load_skill("market-accumulation")
    vol_mod = load_skill("market-volume")

    err = {"error": "unavailable", "pattern": {"present": False}}
    sweep_result = sweep_mod.analyze(candles, interval=interval, period=period) if sweep_mod else err
    accum_result = accum_mod.analyze(candles, interval=interval, period=period) if accum_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err

    sweep_pattern = sweep_result.get("pattern", {})
    sweep_present = sweep_pattern.get("present", False)

    accum_pattern = accum_result.get("pattern", {})
    accum_present = accum_pattern.get("present", False)

    vol_ratio = vol_result.get("volume_ratio") if "error" not in vol_result else None
    obv_trend = vol_result.get("obv_trend") if "error" not in vol_result else None

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    volume_confirms = vol_ratio is not None and vol_ratio > 1.0 and obv_trend == "rising"

    if sweep_present and accum_present and volume_confirms:
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        conviction = min(5, sweep_pattern.get("confidence", 3) + accum_pattern.get("confidence", 3) // 2)
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry - atr * 0.3, 2), round(entry + atr * 0.3, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry + risk * 2, 2), round(entry + risk * 3, 2)],
                "reasoning": "Liquidity sweep with accumulation and volume confirmation — reversal setup.",
                "source_skills": ["market-liquidity-sweep", "market-accumulation", "market-volume"],
            }
        )
    elif sweep_present and not accum_present and volume_confirms:
        entry = price
        stop = entry - atr * 1.5
        risk = entry - stop
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": 2,
                "entry_type": "limit",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry - atr * 0.3, 2), round(entry + atr * 0.3, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry + risk * 2, 2), round(entry + risk * 3, 2)],
                "reasoning": "Liquidity sweep with volume confirmation (no accumulation) — speculative reversal.",
                "source_skills": ["market-liquidity-sweep", "market-volume"],
            }
        )

    if ideas:
        narrative = f"Liquidity sweep setup: long. {sweep_result.get('narrative', '')}"
    else:
        narrative = "No liquidity sweep setup — sweep, accumulation, or volume confirmation missing."

    return {"ideas": ideas, "narrative": narrative}
