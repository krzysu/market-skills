"""Tests for skills/l3-conviction-scan/lib.py — pure ranking + extraction."""

import importlib.util
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB_PATH = os.path.join(_REPO_ROOT, "skills", "l3-conviction-scan", "lib.py")
_spec = importlib.util.spec_from_file_location("l3_conviction_scan_lib", _LIB_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
extract_ideas = _mod.extract_ideas
rank_ideas = _mod.rank_ideas
render_text = _mod.render_text
render_json = _mod.render_json


def _idea(
    *,
    direction="long",
    conviction=3,
    entry=100.0,
    stop=95.0,
    tp1=110.0,
    tp2=120.0,
    tp3=130.0,
    rr_to_tp=None,
    rr_to_tp2=None,
    mover_pct=None,
    veto=None,
    take_profit="default",
    targets=None,
):
    """Build a minimal L3Idea-shaped dict for tests.

    ``take_profit`` is tri-state: ``"default"`` (the list ``[tp1, tp2, tp3]``),
    ``None`` (the key is omitted entirely — used to simulate older strategies
    that emit ``targets`` instead of ``take_profit``), or an explicit list.
    """
    idea = {
        "direction": direction,
        "conviction": conviction,
        "entry_price": entry,
        "stop_loss": stop,
    }
    if take_profit != "default":
        if take_profit is not None:
            idea["take_profit"] = take_profit
    else:
        idea["take_profit"] = [tp1, tp2, tp3]
    if targets is not None:
        idea["targets"] = targets
    if rr_to_tp is not None:
        idea["rr_to_tp"] = rr_to_tp
    if rr_to_tp2 is not None:
        idea["rr_to_tp2"] = rr_to_tp2
    if mover_pct is not None:
        idea["move_maturity_pct"] = mover_pct
    if veto is not None:
        idea["veto_reasons"] = veto
    return idea


def _envelope(*ticker_strategy_ideas):
    """Build a run-all-l3-style envelope from (ticker, metadata, strategy, ideas) tuples."""
    tickers: dict = {}
    for tkr, meta, strat, ideas in ticker_strategy_ideas:
        tickers.setdefault(tkr, {"metadata": meta, "strategies": {}})
        tickers[tkr]["strategies"][strat] = {"ideas": ideas, "narrative": f"narrative for {strat}"}
    return {"tickers": tickers}


# -- extract_ideas -------------------------------------------------------------


def test_extract_ideas_walks_canonical_envelope():
    payload = _envelope(
        (
            "BTCUSD",
            {"label": "Bitcoin", "tier": 2, "asset_class": "majors"},
            "strategy-trend-follow",
            [_idea(conviction=5, rr_to_tp=[1.2, 2.4, 3.6])],
        ),
        (
            "AAPL",
            {"label": "Apple", "tier": 1, "asset_class": "majors"},
            "strategy-breakout-confirm",
            [_idea(conviction=4, entry=187.0)],
        ),
    )
    rows = extract_ideas(payload)
    assert len(rows) == 2
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["BTCUSD"]["label"] == "Bitcoin"
    assert by_ticker["BTCUSD"]["tier"] == 2
    assert by_ticker["BTCUSD"]["strategy"] == "strategy-trend-follow"
    assert by_ticker["BTCUSD"]["conviction"] == 5
    assert by_ticker["BTCUSD"]["rr_tp2"] == 2.4
    assert by_ticker["BTCUSD"]["rr_tp3"] == 3.6
    assert by_ticker["AAPL"]["entry"] == 187.0


def test_extract_ideas_tolerates_rr_to_tp2_scalar_fallback():
    """Legacy ideas may emit a scalar ``rr_to_tp2`` instead of ``rr_to_tp: list``."""
    payload = _envelope(
        (
            "ETHUSD",
            {"tier": 2},
            "strategy-mean-reversion",
            [_idea(conviction=3, rr_to_tp=None, rr_to_tp2=1.8)],
        ),
    )
    rows = extract_ideas(payload)
    assert rows[0]["rr_tp2"] == 1.8
    assert rows[0]["rr_tp1"] is None
    assert rows[0]["rr_tp3"] is None


def test_extract_ideas_handles_targets_alias():
    """Older strategies may emit ``targets`` instead of ``take_profit``."""
    payload = _envelope(
        (
            "SOLUSD",
            {"tier": 3},
            "strategy-trend-follow",
            [_idea(take_profit=None, targets=[210.0, 225.0, 240.0])],
        ),
    )
    rows = extract_ideas(payload)
    assert rows[0]["tp1"] == 210.0
    assert rows[0]["tp2"] == 225.0
    assert rows[0]["tp3"] == 240.0


def test_extract_ideas_empty_envelope():
    assert extract_ideas({}) == []
    assert extract_ideas({"tickers": {}}) == []


def test_extract_ideas_tolerates_l3_wrapper():
    """run-watchlist wraps strategies under ``l3`` rather than ``strategies``."""
    payload = {
        "tickers": {
            "HYPEUSD": {
                "metadata": {"tier": 2, "asset_class": "perp_dex"},
                "l3": {
                    "strategy-trend-follow": {
                        "ideas": [_idea(conviction=4)],
                        "narrative": "wrapped",
                    }
                },
            }
        }
    }
    rows = extract_ideas(payload)
    assert len(rows) == 1
    assert rows[0]["strategy"] == "strategy-trend-follow"
    assert rows[0]["asset_class"] == "perp_dex"


# -- rank_ideas ----------------------------------------------------------------


def test_rank_ideas_sorts_by_conviction_desc():
    rows = [
        {"ticker": "AAA", "conviction": 2},
        {"ticker": "ZZZ", "conviction": 5},
        {"ticker": "MMM", "conviction": 3},
    ]
    ordered = rank_ideas(rows)
    assert [r["ticker"] for r in ordered] == ["ZZZ", "MMM", "AAA"]


def test_rank_ideas_breaks_ties_alphabetically():
    rows = [
        {"ticker": "ZZZ", "conviction": 4},
        {"ticker": "AAA", "conviction": 4},
        {"ticker": "MMM", "conviction": 4},
    ]
    ordered = rank_ideas(rows)
    assert [r["ticker"] for r in ordered] == ["AAA", "MMM", "ZZZ"]


def test_rank_ideas_handles_none_conviction():
    rows = [
        {"ticker": "AAA", "conviction": None},
        {"ticker": "ZZZ", "conviction": 3},
    ]
    ordered = rank_ideas(rows)
    assert ordered[0]["ticker"] == "ZZZ"


def test_rank_ideas_top_n_caps():
    rows = [{"ticker": f"T{i}", "conviction": i} for i in range(1, 6)]
    ordered = rank_ideas(rows, top=2)
    assert [r["ticker"] for r in ordered] == ["T5", "T4"]


# -- render_text ---------------------------------------------------------------


def test_render_text_empty():
    assert render_text([]) == "No L3 ideas surfaced."


def test_render_text_includes_required_columns():
    rows = extract_ideas(
        _envelope(
            (
                "BTCUSD",
                {"tier": 2},
                "strategy-trend-follow",
                [
                    _idea(
                        conviction=5,
                        entry=67500.0,
                        stop=66200.0,
                        tp1=70500.0,
                        tp2=73000.0,
                        rr_to_tp2=2.44,
                    )
                ],
            ),
        )
    )
    for r in rows:
        r["_tf"] = "1d"
        r["_basket"] = "tier_1"
    out = render_text(rows, tf="1d")
    assert "BTCUSD" in out
    assert "strategy-trend-follow" in out
    assert "long" in out
    assert "5" in out  # conviction column
    # f"{x:.4g}" formats 67500.0 as "6.75e+04"; assert the magnitude survives
    assert "6.75e+04" in out
    assert "2.44" in out
    assert "tier_1" in out


def test_render_text_truncates_veto_column():
    long_veto = ",".join(f"reason_{i}" for i in range(50))
    rows = extract_ideas(
        _envelope(
            (
                "X",
                {"tier": 1},
                "strategy-trend-follow",
                [_idea(veto=[long_veto])],
            ),
        )
    )
    for r in rows:
        r["_tf"] = "1d"
        r["_basket"] = "b"
    out = render_text(rows, tf="1d")
    assert "reason_0" in out
    # The 30-char cap prevents reason_29+ from appearing in the veto column
    assert "reason_29" not in out


# -- render_json ---------------------------------------------------------------


def test_render_json_envelope_shape():
    rows = [
        {"ticker": "BTCUSD", "strategy": "s1", "conviction": 5},
        {"ticker": "AAPL", "strategy": "s2", "conviction": 3},
    ]
    out = render_json(
        rows,
        baskets=["tier_1"],
        interval="1d",
        period="1y",
    )
    assert out["interval"] == "1d"
    assert out["period"] == "1y"
    assert out["baskets"] == ["tier_1"]
    assert out["count"] == 2
    assert [r["ticker"] for r in out["ideas"]] == ["BTCUSD", "AAPL"]


def test_render_json_top_n_caps_and_keeps_count_consistent():
    rows = [{"ticker": f"T{i}", "strategy": "s", "conviction": i} for i in range(1, 6)]
    out = render_json(
        rows,
        baskets=["b"],
        interval="1d",
        period="1y",
        top=2,
    )
    assert out["count"] == 2
    assert [r["ticker"] for r in out["ideas"]] == ["T5", "T4"]
