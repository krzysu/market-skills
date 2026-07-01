# Playbook — Sentiment vs Structure

Fear & Greed is contrarian only when structure isn't broken.

## Rule

EXTREME_FEAR (F&G &lt; 25, per `analysis.macro._classify_sentiment`) is a
contrarian signal **only when the broader trend structure is not already
bearish**. When structure is FULL_BEAR (EMA alignment, `market-trend`),
fear is a _trailing_ indicator — price keeps going after fear maxes out,
and doubling size into it adds risk when the setup is least mean-reverting.

The hard cases (FULL_BEAR alignment, score ≤ -3) are mechanical —
`market-trend` will show clean DOWNTREND. This rule is about the
**borderline zone**: `TRANSITION` alignment (TANGLED EMAs, mixed MTF)
or trend score at -1/-2. In that zone, judgment replaces the check.

## How to apply

When `RegimeSignal.inputs.fng ≤ 24` and the trend structure is ambiguous:

1. **Read the weekly timeframe** — if 1w is FULL_BEAR regardless of what
   4h and 1d say, treat fear as trailing, not contrarian.
2. **Look for a divergence tell** — fear + bullish divergence (OBV trend
   flipping, squeeze momentum releasing positive) is the _real_ contrarian
   setup. Fear without divergence is just fear.
3. **Run `market-overview` for a second opinion** — it scores 0-100 across
   multiple tickers and can show whether the broader basket agrees.
4. **Default to normal sizing when in doubt** — no conviction boost without
   affirmative evidence that fear is mispricing.
5. **Fear alone is never a thesis** — if the only argument is "F&G is 12",
   the setup is too thin.

## When this matters most

- Borderline structure (TRANSITION alignment, mixed MTF across timeframes)
- Weekly timeframe disagreeing with daily/intraday
- Any instinct to size up "because everyone is scared"

## Related

- `analysis/macro.py` — `_classify_sentiment()` for F&G bucket thresholds
- `market-ema` — alignment concept (FULL_BEAR / FULL_BULL / TANGLED / TRANSITION)
- `risk-engine` — the policies that size and gate the intent after this judgment
