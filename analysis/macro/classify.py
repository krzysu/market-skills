"""Cross-asset macro regime classifiers — pure functions, no I/O."""

from analysis.macro._constants import (
    _LABEL_BTC_MCAP,
    _LABEL_COINGECKO,
    _LABEL_DXY,
    _LABEL_FNG,
    _LABEL_US10Y,
    _LABEL_VIX,
)


def _missing_inputs_from_errors(errors: list[str]) -> list[str]:
    if not errors:
        return []
    label_to_inputs: dict[str, tuple[str, ...]] = {
        _LABEL_FNG: ("fng",),
        _LABEL_VIX: ("vix",),
        _LABEL_DXY: ("dxy",),
        _LABEL_US10Y: ("us10y",),
        _LABEL_BTC_MCAP: ("btc_dominance",),
        _LABEL_COINGECKO: ("btc_dominance", "total_mcap_usd"),
    }
    seen: set[str] = set()
    out: list[str] = []
    for err in errors:
        if not isinstance(err, str) or ":" not in err:
            continue
        label = err.split(":", 1)[0].strip()
        for canonical in label_to_inputs.get(label, ()):
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append(canonical)
    return out


def _classify_risk_appetite(vix: float | None, dxy: float | None, us10y: float | None) -> str:
    if vix is None:
        vix_band = "NEUTRAL"
    elif vix < 15:
        vix_band = "RISK_ON"
    elif vix < 25:
        vix_band = "NEUTRAL"
    elif vix < 35:
        vix_band = "RISK_OFF"
    else:
        vix_band = "CRISIS"

    cap_triggered = (dxy is not None and dxy > 105) or (us10y is not None and us10y > 4.5)
    if cap_triggered and vix_band in ("RISK_ON", "NEUTRAL"):
        return "NEUTRAL"
    return vix_band


def _classify_liquidity(us10y: float | None, vix: float | None) -> str:
    if us10y is None:
        return "TIGHTENING"
    if us10y < 3.5:
        base = "EASY"
    elif us10y < 4.5:
        base = "TIGHTENING"
    else:
        base = "TIGHT"
    if vix is not None and vix > 25 and base == "TIGHT":
        return "STRESS"
    return base


def _classify_sentiment(fng: float | None) -> str:
    if fng is None:
        return "NEUTRAL"
    if fng < 25:
        return "EXTREME_FEAR"
    if fng < 45:
        return "FEAR"
    if fng < 55:
        return "NEUTRAL"
    if fng < 75:
        return "GREED"
    return "EXTREME_GREED"


def _format_regime_note(
    risk_appetite: str,
    liquidity: str,
    sentiment: str,
    inputs: dict[str, float | None],
) -> str:
    posture = {
        ("RISK_ON", "EASY"): "risk-on, easy liquidity",
        ("RISK_ON", "TIGHTENING"): "risk-on but liquidity tightening",
        ("RISK_ON", "TIGHT"): "risk-on, tight liquidity",
        ("RISK_ON", "STRESS"): "risk-on but stressed",
        ("NEUTRAL", "EASY"): "neutral, easy liquidity",
        ("NEUTRAL", "TIGHTENING"): "neutral, liquidity tightening",
        ("NEUTRAL", "TIGHT"): "neutral, tight liquidity",
        ("NEUTRAL", "STRESS"): "neutral, liquidity stress",
        ("RISK_OFF", "EASY"): "risk-off, easy liquidity",
        ("RISK_OFF", "TIGHTENING"): "risk-off, liquidity tightening",
        ("RISK_OFF", "TIGHT"): "risk-off, tight liquidity",
        ("RISK_OFF", "STRESS"): "risk-off, stressed",
        ("CRISIS", "EASY"): "crisis, easy liquidity",
        ("CRISIS", "TIGHTENING"): "crisis, liquidity tightening",
        ("CRISIS", "TIGHT"): "crisis, tight liquidity",
        ("CRISIS", "STRESS"): "crisis, stressed",
    }
    core = posture.get((risk_appetite, liquidity), f"{risk_appetite.lower()}, {liquidity.lower()}")
    sent = sentiment.lower().replace("_", " ")
    if risk_appetite in ("RISK_OFF", "CRISIS"):
        posture_hint = "defensive posture recommended"
    elif risk_appetite == "NEUTRAL":
        posture_hint = "selective entries"
    else:
        posture_hint = "constructive"
    vix = inputs.get("vix")
    fng = inputs.get("fng")
    extra = []
    if vix is not None:
        extra.append(f"VIX {vix:.1f}")
    if fng is not None:
        extra.append(f"F&G {fng:.0f}")
    extra_s = f" ({', '.join(extra)})" if extra else ""
    return f"Macro: {core}; sentiment {sent}. {posture_hint}.{extra_s}"
