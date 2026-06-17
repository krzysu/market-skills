"""strategy-breakout-confirm — L3 strategy: confirmed breakouts with volume + squeeze."""

from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill


def analyze(candles, *, ticker, interval="1d", period="1y"):
    if not candles or len(candles) < 50:
        cc = len(candles) if candles else 0
        return {"ideas": [], "narrative": f"insufficient data (need 50+ candles, got {cc})"}

    bo_mod = load_skill("market-breakout")
    sqz_mod = load_skill("market-squeeze")
    vol_mod = load_skill("market-volume")

    err = {"error": "unavailable", "pattern": {"present": False}}
    bo_result = bo_mod.analyze(candles, interval=interval, period=period) if bo_mod else err
    sqz_result = sqz_mod.analyze(candles, interval=interval, period=period) if sqz_mod else err
    vol_result = vol_mod.analyze(candles, interval=interval, period=period) if vol_mod else err

    bo_pattern = bo_result.get("pattern", {})
    bo_classification = bo_pattern.get("classification", "")
    bo_present = bo_pattern.get("present", False)

    sqz_signal = sqz_result.get("signal") if "error" not in sqz_result else None

    vol_ratio = vol_result.get("volume_ratio") if "error" not in vol_result else None
    obv_trend = vol_result.get("obv_trend") if "error" not in vol_result else None

    closes = [c[4] for c in candles]
    price = closes[-1]
    atr = compute_atr_from_candles(candles, period=14) or 0

    ideas = []

    volume_ok = vol_ratio is not None and vol_ratio > 1.2
    squeeze_long = sqz_signal in ("BULLISH", "BULLISH FADING")
    squeeze_short = sqz_signal in ("BEARISH", "BEARISH FADING")
    obv_rising = obv_trend == "rising"
    obv_falling = obv_trend == "falling"

    if bo_present and "BULL" in str(bo_classification).upper() and volume_ok and (squeeze_long or obv_rising):
        entry = price
        stop = entry - atr * 1.5
        conviction = min(5, bo_pattern.get("confidence", 3) + (1 if squeeze_long else 0))
        ideas.append(
            {
                "pair": ticker,
                "direction": "long",
                "conviction": conviction,
                "entry_type": "market",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry, 2), round(entry + atr * 0.3, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry + atr * 2, 2), round(entry + atr * 4, 2)],
                "reasoning": f"Bullish breakout confirmed: {bo_classification}, volume {vol_ratio:.1f}x.",
                "source_skills": ["market-breakout", "market-volume", "market-squeeze"],
            }
        )

    if bo_present and "BEAR" in str(bo_classification).upper() and volume_ok and (squeeze_short or obv_falling):
        entry = price
        stop = entry + atr * 1.5
        conviction = min(5, bo_pattern.get("confidence", 3) + (1 if squeeze_short else 0))
        ideas.append(
            {
                "pair": ticker,
                "direction": "short",
                "conviction": conviction,
                "entry_type": "market",
                "entry_price": round(entry, 2),
                "entry_range": [round(entry - atr * 0.3, 2), round(entry, 2)],
                "stop_loss": round(stop, 2),
                "take_profit": [round(entry - atr * 2, 2), round(entry - atr * 4, 2)],
                "reasoning": f"Bearish breakdown confirmed: {bo_classification}, volume {vol_ratio:.1f}x.",
                "source_skills": ["market-breakout", "market-volume", "market-squeeze"],
            }
        )

    if ideas:
        narrative = f"Breakout momentum setup: {', '.join(i['direction'] for i in ideas)}."
    else:
        narrative = "No confirmed breakout — volume or squeeze confirmation missing."

    return {"ideas": ideas, "narrative": narrative}
