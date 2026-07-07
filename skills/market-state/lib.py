"""market-state — session-start cross-skill dashboard.

Reads the per-skill state caches written by phase 3 home views
($XDG_DATA_HOME/market-skills/<skill>_last.json) and composes a
single dashboard so the LLM can read the state of the world in one
call at session start. The 6 sources are:

  1. market-macro      — cross-asset regime (risk_appetite / liquidity / sentiment)
  2. market-valuation  — SP500 Shiller CAPE regime
  3. market-movers     — CoinGecko gainers / losers / trending
  4. run-watchlist     — last batch L2+L3 scan
  5. l3-conviction-scan — last ranked L3 ideas
  6. market-notes      — active user notes

Each source contributes a slim view (a `summary` one-liner plus a
handful of headline fields) and a `cached_at` ISO timestamp. A
`freshness` map at the top level lists every source's age so the
LLM can decide which to refresh before acting.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from analysis.output import _age_human, _state_cache_path

SOURCE_KEYS = [
    "market-macro",
    "market-valuation",
    "market-movers",
    "run-watchlist",
    "l3-conviction-scan",
    "market-notes",
]

SOURCE_LABELS = {
    "market-macro": "regime",
    "market-valuation": "valuation",
    "market-movers": "movers",
    "run-watchlist": "watchlist",
    "l3-conviction-scan": "conviction",
    "market-notes": "notes",
}


def _read_cache(skill_name: str) -> dict[str, Any] | None:
    path = _state_cache_path(skill_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _slim_macro(payload: dict) -> dict:
    regime = payload.get("regime") or {}
    return {
        "risk_appetite": regime.get("risk_appetite"),
        "liquidity": regime.get("liquidity"),
        "sentiment": regime.get("sentiment"),
        "regime_note": payload.get("regime_note"),
        "incomplete": payload.get("incomplete", False),
        "summary": _summary_macro(regime, payload),
    }


def _summary_macro(regime: dict, payload: dict) -> str:
    parts = [
        str(regime.get("risk_appetite", "?")),
        str(regime.get("liquidity", "?")),
        str(regime.get("sentiment", "?")),
    ]
    s = " / ".join(parts)
    if payload.get("incomplete"):
        s += " (incomplete)"
    return s


def _slim_valuation(payload: dict) -> dict:
    regime = payload.get("regime") or {}
    inputs = payload.get("inputs") or {}
    return {
        "regime": regime.get("regime"),
        "cape_zscore": regime.get("cape_zscore"),
        "sp500": inputs.get("sp500"),
        "cape": inputs.get("cape"),
        "regime_note": payload.get("regime_note"),
        "summary": _summary_valuation(regime),
    }


def _summary_valuation(regime: dict) -> str:
    z = regime.get("cape_zscore")
    label = regime.get("regime", "?")
    if isinstance(z, (int, float)):
        return f"SP500 {label} (z={z:+.2f})"
    return f"SP500 {label}"


def _slim_movers(payload: dict) -> dict:
    g = payload.get("gainers") or []
    losers = payload.get("losers") or []
    t = payload.get("trending") or []
    return {
        "gainers_count": len(g),
        "losers_count": len(losers),
        "trending_count": len(t),
        "fetched_at": payload.get("fetched_at"),
        "summary": _summary_movers(g, losers, t),
    }


def _summary_movers(g: list, losers: list, t: list) -> str:
    bits = []
    if g:
        bits.append(f"{len(g)} gainers")
    if losers:
        bits.append(f"{len(losers)} losers")
    if t:
        bits.append(f"{len(t)} trending")
    return ", ".join(bits) if bits else "no panels"


def _slim_watchlist(payload: dict) -> dict:
    return {
        "scope": payload.get("scope"),
        "summary": payload.get("summary"),
        "tickers_scanned": payload.get("tickers_scanned"),
        "fired_skills_total": payload.get("fired_skills_total"),
        "ideas_count": payload.get("ideas_count"),
    }


def _slim_conviction(payload: dict) -> dict:
    ideas = payload.get("ideas") or []
    top = ideas[:5]
    return {
        "total": payload.get("total") or len(ideas),
        "baskets": payload.get("baskets"),
        "interval": payload.get("interval"),
        "period": payload.get("period"),
        "top_ideas": top,
        "summary": payload.get("summary"),
    }


def _slim_notes(payload: dict) -> dict:
    pairs = payload.get("pairs") or {}
    return {
        "pair_count": len(pairs),
        "pairs": sorted(pairs.keys()),
        "summary": payload.get("summary"),
    }


_SLIMMERS = {
    "market-macro": _slim_macro,
    "market-valuation": _slim_valuation,
    "market-movers": _slim_movers,
    "run-watchlist": _slim_watchlist,
    "l3-conviction-scan": _slim_conviction,
    "market-notes": _slim_notes,
}


def compose_state() -> dict:
    """Read all source caches and return the composed dashboard.

    Returns a dict shaped:
      {
        "sources": {<label>: <slim or None>},
        "freshness": {<label>: <age-string or "no cache">},
        "summary": "<one-liner>",
        "sources_cached": <int>,
        "sources_total": 6,
      }
    A source is "cached" when its file exists and decodes; otherwise
    the slim view is None and freshness is "no cache".
    """
    sources: dict[str, dict | None] = {}
    freshness: dict[str, str] = {}
    cached_count = 0

    for skill in SOURCE_KEYS:
        label = SOURCE_LABELS[skill]
        payload = _read_cache(skill)
        if payload is None:
            sources[label] = None
            freshness[label] = "no cache"
            continue
        cached_count += 1
        cached_at = payload.get("cached_at")
        if cached_at:
            try:
                datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                freshness[label] = _age_human(cached_at) or "just now"
            except (ValueError, TypeError):
                freshness[label] = "unknown age"
        else:
            freshness[label] = "unknown age"
        slimmer = _SLIMMERS.get(skill)
        sources[label] = slimmer(payload) if slimmer else None

    summary = _compose_summary(sources, cached_count)
    return {
        "sources": sources,
        "freshness": freshness,
        "summary": summary,
        "sources_cached": cached_count,
        "sources_total": len(SOURCE_KEYS),
    }


def _compose_summary(sources: dict, cached: int) -> str:
    total = len(SOURCE_KEYS)
    if cached == 0:
        return f"no cached state for any of {total} sources"
    bits = []
    macro = sources.get("regime")
    if macro and macro.get("risk_appetite"):
        bits.append(f"regime: {macro['risk_appetite']}/{macro['liquidity']}/{macro['sentiment']}")
    valuation = sources.get("valuation")
    if valuation and valuation.get("regime"):
        bits.append(f"valuation: {valuation['regime']}")
    conviction = sources.get("conviction")
    if conviction and conviction.get("total") is not None:
        bits.append(f"{conviction['total']} L3 ideas")
    return ", ".join(bits) if bits else f"{cached}/{total} sources cached"
