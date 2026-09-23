"""Regression: the spot-perp basis must be derived from UNROUNDED closes.

`price` is a magnitude-rounded DISPLAY field (2dp for anything >= $1). The basis
is a difference of two nearly-equal prices, so deriving it from the rounded
`price` quantizes it to whole cents and silently zeroes it across the $1-$1000
asset range — the skill's core metric. These tests pin the basis to the raw
closes.

Hermetic — `fetch_ohlc` / `fetch_funding_rate` are stubbed, no network.
"""

from __future__ import annotations

import importlib.util
import os


def _load_run():
    run_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-basis", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("market_basis_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _candles(close, n=60):
    """OHLCV rows shaped like the provider's: [ts, open, high, low, close, volume]."""
    return [[i * 60_000, close, close, close, close, 1000.0] for i in range(n)]


def _patch(run, *, spot_close, perp_close):
    """Stub the provider calls. ``analyze()`` appends ``:USDT`` to build the perp
    ticker, so the colon distinguishes the perp fetch from the spot fetch."""

    def _fake_fetch(ticker, *, interval="1d", period="6mo", source=None, **kw):
        return _candles(perp_close if ":" in ticker else spot_close)

    run.fetch_ohlc = _fake_fetch
    run.fetch_funding_rate = lambda *a, **k: None
    return run


def test_sub_cent_basis_on_dollar_pair_is_not_zeroed():
    """A $118 pair with a sub-cent spot/perp gap must not report a zero basis."""
    run = _load_run()
    _patch(run, spot_close=118.3603, perp_close=118.3633)
    out = run.analyze("TEST/USDT", source="ccxt:binance")

    assert out["basis"]["absolute"] != 0.0, "basis collapsed to zero — derived from rounded prices"
    assert out["basis"]["percent"] != 0.0
    assert abs(out["basis"]["absolute"] - 0.0030) < 1e-9
    assert abs(out["basis"]["percent"] - 0.0025) < 1e-3


def test_sub_cent_basis_on_low_dollar_pair_is_not_zeroed():
    """Same shape at $15, where 2dp rounding is most destructive."""
    run = _load_run()
    _patch(run, spot_close=15.0003, perp_close=15.0033)
    out = run.analyze("TEST/USDT", source="ccxt:binance")

    assert out["basis"]["absolute"] != 0.0
    assert abs(out["basis"]["absolute"] - 0.0030) < 1e-9
    assert abs(out["basis"]["percent"] - 0.0200) < 1e-3


def test_large_pair_basis_still_correct():
    """Control: a wide basis on a high-priced pair is unaffected."""
    run = _load_run()
    _patch(run, spot_close=84091.50, perp_close=84095.00)
    out = run.analyze("TEST/USDT", source="ccxt:binance")

    assert abs(out["basis"]["absolute"] - 3.5) < 1e-9


def test_price_field_stays_magnitude_rounded():
    """The display field keeps the new magnitude-aware precision."""
    run = _load_run()
    _patch(run, spot_close=0.004364, perp_close=0.004364)
    out = run.analyze("TEST/USDT", source="ccxt:binance")

    assert out["spot"]["price"] == 0.004364
