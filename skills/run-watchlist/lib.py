"""run-watchlist — bulk-run L2 + L3 skills across every ticker in a basket.

Fetches candles once per ticker, then runs all L2 patterns and L3 strategies
in-process. Notes auto-load by default (use `--no-notes` to skip). Tracking-only
tickers are still analyzed but a flag surfaces them in output.

Returns the same JSON shape that `run-all-l2` + `run-all-l3` would emit
independently, just merged per ticker.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable

from analysis.registry import l2_skills, l3_strategies
from analysis.skill_loader import load_skill

_SKIP_REASON = "excluded: all strategies have negative Sharpe on this ticker"


def _load_skip_tickers(path: str | None) -> tuple[list[str], str | None]:
    """Read ``skip_tickers`` and ``reason`` from a skip-list JSON file.

    Silently returns ``([], None)`` on any failure (missing file, invalid
    JSON, missing key, wrong shape) so a skip-list issue never aborts the
    scan.
    """
    if not path:
        return [], None
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return [], None
    if not isinstance(data, dict):
        return [], None
    skip = data.get("skip_tickers", [])
    if not isinstance(skip, list):
        return [], None
    reason_raw = data.get("reason")
    reason = reason_raw if isinstance(reason_raw, str) and reason_raw.strip() else None
    return [t for t in skip if isinstance(t, str)], reason


def filter_skip_list(
    tickers: list[str],
    skip_tickers: list[str],
    wl_path: str | None,
    *,
    metadata_for: Callable[[str, str | None], dict] | None = None,
    reason: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Apply skip-list filtering with a watchlist override.

    A ticker is excluded when it appears in ``skip_tickers`` AND its
    watchlist metadata does not put it at tier 1 or tier 2 (tier-1/2
    tickers are always scanned — watchlist wins).

    ``reason`` overrides the default skip message; falls back to
    ``_SKIP_REASON`` when ``None``.

    Returns ``(kept_tickers, skipped)`` where ``skipped`` maps
    ``ticker -> reason``.
    """
    if not skip_tickers:
        return list(tickers), {}

    skip_set = set(skip_tickers)
    if metadata_for is None:
        from analysis.watchlist import metadata_for as _default_metadata_for

        metadata_for = _default_metadata_for

    use_reason = reason if reason else _SKIP_REASON
    kept: list[str] = []
    skipped: dict[str, str] = {}
    for t in tickers:
        if t in skip_set:
            try:
                meta = metadata_for(t, wl_path) or {}
            except Exception:
                meta = {}
            tier = meta.get("tier")
            if tier in (1, 2):
                kept.append(t)
            else:
                skipped[t] = use_reason
        else:
            kept.append(t)
    return kept, skipped


def _strategy_accepts(mod, param_name: str) -> bool:
    """Return True iff ``mod.analyze`` declares ``param_name`` as a parameter.

    Defensive guard against signature drift: only pass ``asset_class`` to
    strategies that declare it. See ``skills/run-all-l3/lib.py`` for the
    canonical implementation.
    """
    try:
        sig = inspect.signature(mod.analyze)
    except (TypeError, ValueError):
        return False
    return param_name in sig.parameters


def analyze_ticker(
    ticker: str,
    candles,
    *,
    metadata: dict | None = None,
    include_l2: bool = True,
    include_l3: bool = True,
    include_notes: bool = True,
    notes_loader=None,
    interval: str = "1d",
    period: str = "1y",
) -> dict:
    """Run L2+L3+notes for a single ticker with cached candles.

    `notes_loader` is a callable `notes_loader(ticker) -> list[dict]` injected
    so this lib doesn't depend on `analysis.notes` (avoid circular imports in
    the test suite). The CLI passes `analysis.notes.load_active`.
    """
    out: dict = {"ticker": ticker}

    meta = metadata or {}
    if meta:
        out["metadata"] = meta

    if include_l2:
        l2_out = {}
        for skill_name in l2_skills():
            mod = load_skill(skill_name)
            if mod is None:
                l2_out[skill_name] = {"error": "skill not found"}
                continue
            try:
                l2_out[skill_name] = mod.analyze(candles, interval=interval, period=period)
            except Exception as e:
                l2_out[skill_name] = {"error": str(e)}
        out["l2"] = l2_out

    if include_l3:
        l3_out = {}
        asset_class = meta.get("asset_class")
        for strat_name in l3_strategies():
            mod = load_skill(strat_name)
            if mod is None:
                l3_out[strat_name] = {"ideas": [], "narrative": "skill not found"}
                continue
            kwargs = {"ticker": ticker, "interval": interval, "period": period}
            if _strategy_accepts(mod, "asset_class"):
                kwargs["asset_class"] = asset_class
            try:
                l3_out[strat_name] = mod.analyze(candles, **kwargs)
            except Exception as e:
                l3_out[strat_name] = {"ideas": [], "narrative": f"error: {e}"}
        out["l3"] = l3_out

    if include_notes and notes_loader is not None:
        try:
            out["notes"] = notes_loader(ticker)
        except Exception as e:
            out["notes"] = []
            out["notes_error"] = f"{type(e).__name__}: {e}"

    return out
