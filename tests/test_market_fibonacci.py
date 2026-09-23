"""Tests for market-fibonacci L1: fib levels from swing high/low, hermetic."""

import importlib.util
import os
import re
import sys


def _load_fib_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-fibonacci", "lib.py")
    spec = importlib.util.spec_from_file_location("market_fibonacci_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sub_cent_candles(n=60, base=0.0030):
    """Sub-cent series ``[ts, open, high, low, close, volume]`` shaped like the real fetch.

    A two-regime series (fall then rise) guarantees detectable swing high and
    swing low within the window, all values inside the sub-cent band.
    """
    out = []
    ts = 0
    price = base
    for _ in range(n // 2):
        price -= 0.000002  # falling leg
        out.append([ts, price + 0.000002, price + 0.000004, price - 0.000002, price, 200_000])
        ts += 86400
    for _ in range(n - n // 2):
        price += 0.000004  # rising leg
        out.append([ts, price - 0.000002, price + 0.000002, price - 0.000004, price, 200_000])
        ts += 86400
    return out


class TestSubCentFibonacci:
    def test_sub_cent_fib_levels_not_collapsed_to_zero(self):
        """Sub-cent asset: every fib level must be non-zero at 6dp.

        Discriminating: pre-fix the levels were rounded to 2dp (both in the
        lib and in ``compute_fib_levels``), which collapses any value below
        $0.005 to 0.0 — the skill's entire output was unusable.
        """
        mod = _load_fib_lib()
        candles = _sub_cent_candles(n=60)
        last_close = candles[-1][4]
        result = mod.analyze(candles, interval="1d", period="1y")

        assert "error" not in result
        assert result["current_price"] != 0.0, "sub-cent price collapsed to 0.0 by 2dp rounding"
        assert result["current_price"] == round(last_close, 6)
        assert result["swing_high"] != 0.0
        assert result["swing_low"] != 0.0
        for key, value in result["fib_levels"].items():
            assert value != 0.0, f"fib level {key} collapsed to 0.0 by 2dp rounding"
            assert value == round(value, 6)
        assert result["nearest_fib_support"] != 0.0
        assert result["nearest_fib_resistance"] != 0.0

    def test_sub_cent_nearest_levels_bracket_current_price(self):
        """Sanity: the nearest support sits below and the nearest resistance above price."""
        mod = _load_fib_lib()
        result = mod.analyze(_sub_cent_candles(n=60), interval="1d", period="1y")
        price = result["current_price"]
        assert result["nearest_fib_support"] < price
        assert result["nearest_fib_resistance"] > price


def _load_run():
    run_path = os.path.join(os.path.dirname(__file__), "..", "skills", "market-fibonacci", "scripts", "run.py")
    spec = importlib.util.spec_from_file_location("market_fibonacci_run", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSubCentTextDisplay:
    def test_non_json_display_shows_sub_cent_digits(self, tmp_path, monkeypatch, capsys):
        """Non-JSON (default) output must render sub-cent prices with real digits.

        Discriminating: pre-repair the text display hardcoded `,.2f`, so
        `market-fibonacci SUBX` printed `price: 0.00` and every fib level
        as `0.00` even though the JSON payload carried full precision.
        """
        run = _load_run()
        monkeypatch.setattr(run, "fetch_ohlc", lambda *a, **kw: _sub_cent_candles(n=60))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["run.py", "SUBX"])
        run.main()
        out = capsys.readouterr().out

        assert "price: 0.00)" not in out, "sub-cent price collapsed to 0.00 in text display"
        assert "price: 0.003" in out
        # A collapsed level renders as `:  <spaces>0.00` (then EOL or marker);
        # a real 6dp value like `0.003512` has a digit right after `0.00`.
        assert re.search(r":\s+0\.00(?:\s|$)", out) is None, "a fib level collapsed to 0.00 in text display"
        assert "Fibonacci Levels:" in out
