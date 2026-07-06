"""l3-conviction-scan — flatten + rank L3 ideas across watchlist baskets.

Reads the canonical run-all-l3 envelope (per-ticker strategies.ideas[]),
flattens it into a list of (ticker, strategy, idea) rows, and renders a
conviction-sorted view. In-process: imports `skills.run-all-l3.lib.analyze`
via `analysis.skill_loader.load_skill` and calls it directly, so no
subprocess, no host-specific paths, and no manual envelope parsing in
ad-hoc scripts.
"""

from __future__ import annotations

from analysis.data import fetch_ohlc
from analysis.skill_loader import load_skill
from analysis.watchlist import by_category, metadata_for

__all__ = [
    "extract_ideas",
    "rank_ideas",
    "render_text",
    "render_json",
    "scan",
]


def _pick_rr(idea: dict, idx: int):
    """Return the R:R to ``ideas.take_profit[idx]``.

    Tolerates the canonical ``rr_to_tp: list[float]`` shape (preferred) and
    the legacy scalar fallback (``rr_to_tp2`` / ``rr``) so older L3
    strategy outputs still render correctly.
    """
    val = idea.get("rr_to_tp")
    if isinstance(val, list) and idx < len(val):
        return val[idx]
    if idx == 1:
        scalar = idea.get("rr_to_tp2") or idea.get("rr")
        return scalar
    return None


def extract_ideas(payload: dict) -> list[dict]:
    """Walk a run-all-l3 / run-watchlist envelope and yield one row per idea.

    The expected envelope is the same shape ``run-all-l3`` emits:

        {
          "tickers": {
            "<TICKER>": {
              "strategies": {
                "<STRATEGY>": {"ideas": [<L3Idea>, ...], "narrative": "..."}
              }
            }
          }
        }

    ``run-watchlist`` wraps this in an extra ``l3`` layer — also tolerated
    by walking either ``td["strategies"]`` or ``td["l3"]``.
    """
    out: list[dict] = []
    for tkr, td in (payload.get("tickers") or {}).items():
        meta = td.get("metadata") or {}
        strategies = td.get("strategies") or td.get("l3") or {}
        for strat, s in strategies.items():
            for idea in s.get("ideas") or []:
                tgts = idea.get("take_profit") or idea.get("targets") or []
                out.append(
                    {
                        "ticker": tkr,
                        "label": meta.get("label", tkr),
                        "tier": meta.get("tier"),
                        "asset_class": meta.get("asset_class"),
                        "strategy": strat,
                        "direction": idea.get("direction"),
                        "conviction": idea.get("conviction"),
                        "version": idea.get("version"),
                        "entry": idea.get("entry_price"),
                        "stop": idea.get("stop_loss"),
                        "tp1": tgts[0] if len(tgts) > 0 else None,
                        "tp2": tgts[1] if len(tgts) > 1 else None,
                        "tp3": tgts[2] if len(tgts) > 2 else None,
                        "rr_tp1": _pick_rr(idea, 0),
                        "rr_tp2": _pick_rr(idea, 1),
                        "rr_tp3": _pick_rr(idea, 2),
                        "mover_pct": idea.get("move_maturity_pct"),
                        "veto": idea.get("veto_reasons") or [],
                        "narrative": (s.get("narrative") or "")[:200],
                    }
                )
    return out


def rank_ideas(rows: list[dict], *, top: int | None = None) -> list[dict]:
    """Sort by ``conviction`` desc, ties broken by ticker asc. Cap to ``top`` if given."""
    ordered = sorted(rows, key=lambda r: (-(r["conviction"] or 0), r["ticker"]))
    if top is not None:
        return ordered[:top]
    return ordered


def render_text(
    rows: list[dict],
    *,
    top: int | None = None,
    basket: str | None = None,
    tf: str | None = None,
) -> str:
    """Human-readable table. Pre-render rows through :func:`rank_ideas`."""
    rows = rank_ideas(rows, top=top)
    if not rows:
        return "No L3 ideas surfaced."

    def fmt(x, w=9):
        if x is None:
            return "—"
        if isinstance(x, float):
            return f"{x:.4g}"
        return str(x)[:w]

    header = (
        f"{'TF':<4} {'BASKET':<10} {'TICKER':<10} {'STRATEGY':<26} "
        f"{'DIR':<5} {'CONV':<4} {'ENTRY':>9} {'STOP':>9} {'TP1':>9} "
        f"{'TP2':>9} {'RRT2':>6}  VETO"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        veto = ",".join(r["veto"])[:30] if r["veto"] else ""
        lines.append(
            f"{r.get('_tf', '?'):<4} {r.get('_basket', '?'):<10} {r['ticker']:<10} "
            f"{r['strategy']:<26} {str(r['direction']):<5} {str(r['conviction']):<4} "
            f"{fmt(r['entry']):>9} {fmt(r['stop']):>9} {fmt(r['tp1']):>9} "
            f"{fmt(r['tp2']):>9} {fmt(r['rr_tp2']):>6}  {veto}"
        )
    if top is None and len(rank_ideas(rows)) == len(rows):
        lines.append("")
        lines.append(f"Total ideas surfaced: {len(rows)}")
    return "\n".join(lines)


def render_json(
    rows: list[dict],
    *,
    baskets: list[str],
    interval: str,
    period: str,
    top: int | None = None,
) -> dict:
    """Machine-readable envelope. Pre-render rows through :func:`rank_ideas`."""
    ordered = rank_ideas(rows, top=top)
    return {
        "interval": interval,
        "period": period,
        "baskets": baskets,
        "count": len(ordered),
        "ideas": ordered,
    }


def _load_l3_analyze():
    """Load ``skills.run-all-l3.lib.analyze`` in-process.

    Returns ``None`` if the sibling skill is missing (defensive — same
    fallback shape ``run-all-l3`` uses internally).
    """
    return load_skill("run-all-l3")


def scan(
    baskets: list[str],
    *,
    interval: str = "1d",
    period: str = "1y",
    source: str | None = None,
    watchlist_path: str | None = None,
) -> list[dict]:
    """Fetch once per ticker, run L3 in-process, flatten to ranked rows.

    Returns the flat row list (``extract_ideas`` output) annotated with
    ``_basket`` and ``_tf`` so renderers can group by source. Does not
    apply ``--top``; that's a render-time concern.
    """
    l3 = _load_l3_analyze()
    if l3 is None:
        raise RuntimeError("skills.run-all-l3 not found — cannot run L3 conviction scan")

    out: list[dict] = []
    for basket in baskets:
        tickers = by_category(basket, path=watchlist_path)
        for tkr in tickers:
            candles = fetch_ohlc(tkr, interval=interval, period=period, source=source)
            if not candles:
                continue
            meta = metadata_for(tkr, path=watchlist_path)
            asset_class = meta.get("asset_class")
            envelope = l3.analyze(tkr, candles, interval=interval, period=period, asset_class=asset_class)
            payload = {
                "tickers": {
                    tkr: {
                        "metadata": meta,
                        "strategies": envelope.get("strategies") or {},
                    }
                }
            }
            for row in extract_ideas(payload):
                row["_basket"] = basket
                row["_tf"] = interval
                out.append(row)
    return out
