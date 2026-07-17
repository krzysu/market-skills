"""breakout-confirm gate probe, v2 contract (bead market-skills-k4c, post-fix).

Companion to ``probe_breakout_gates.py`` from parent hsg. Walks the same candle
window the original "0 trades" report used and tallies how many bars pass each
conjunct of ``strategy-breakout-confirm`` AFTER the k4c fix.

Pre-fix (per hsg): all conjuncts reach 0 because ``l2_classification()``
returns a status (FRESH/FAILED/STALE/CONFIRMED), and the L3's
``"BULL"/"BEAR" in classification`` substring gate is dead against that.

Post-fix (this script): the L2 returns ``pattern.direction`` (bull/bear/None)
and the L3 reads it. The probe confirms non-zero entries land.

Usage:
    uv run skills/strategy-breakout-confirm/scripts/probe_v2_contract.py \\
        --tickers BTCUSD,ETHUSD,SOLUSD --interval 1d --period 1y
"""

from __future__ import annotations

import argparse
import sys

from analysis.contracts import enforce_min_stop_distance
from analysis.data import fetch_ohlc
from analysis.indicators import compute_atr_from_candles
from analysis.skill_loader import load_skill

VOL_RATIO_FLOOR = 1.2
ATR_K = 1.5
DEFAULT_WARMUP = 60


def _evaluate(prefix, *, interval, period, bo_mod, vol_mod, sqz_mod):
    bo = bo_mod.analyze(prefix, interval=interval, period=period)
    vol = vol_mod.analyze(prefix, interval=interval, period=period)
    sqz = sqz_mod.analyze(prefix, interval=interval, period=period)

    bo_pattern = bo.get("pattern", {}) if "error" not in bo else {}
    bo_direction = bo_pattern.get("direction")

    vol_ratio = vol.get("volume_ratio") if "error" not in vol else None
    obv_trend = vol.get("obv_trend") if "error" not in vol else None
    sqz_signal = sqz.get("signal") if "error" not in sqz else None

    atr = compute_atr_from_candles(prefix, period=14) or 0.0
    price = prefix[-1][4] if prefix else 0.0

    b = vol_ratio is not None and vol_ratio > VOL_RATIO_FLOOR
    sqz_long = sqz_signal in ("BULLISH", "BULLISH FADING")
    sqz_short = sqz_signal in ("BEARISH", "BEARISH FADING")
    obv_rising = obv_trend == "rising"
    obv_falling = obv_trend == "falling"

    c_long = sqz_long or obv_rising
    c_short = sqz_short or obv_falling

    long_stop = price - atr * ATR_K
    short_stop = price + atr * ATR_K
    d_long = enforce_min_stop_distance({"entry_price": price, "stop_loss": long_stop})[0]
    d_short = enforce_min_stop_distance({"entry_price": price, "stop_loss": short_stop})[0]

    a_long = bo_direction == "bull"
    a_short = bo_direction == "bear"

    return {
        "bo_direction": bo_direction,
        "vol_ratio": vol_ratio,
        "sqz_signal": sqz_signal,
        "atr": atr,
        "long": {
            "a": a_long,
            "b": b,
            "c": c_long,
            "d": d_long,
            "ab": a_long and b,
            "abc": a_long and b and c_long,
            "abcd": a_long and b and c_long and d_long,
        },
        "short": {
            "a": a_short,
            "b": b,
            "c": c_short,
            "d": d_short,
            "ab": a_short and b,
            "abc": a_short and b and c_short,
            "abcd": a_short and b and c_short and d_short,
        },
    }


def _walk(candles, *, interval, period, warmup, bo_mod, vol_mod, sqz_mod):
    counts = {d: {"a": 0, "b": 0, "c": 0, "d": 0, "ab": 0, "abc": 0, "abcd": 0, "n": 0} for d in ("long", "short")}
    n = len(candles)
    start = max(warmup, 0)
    n_bo_dir_set = 0
    for t in range(start, n):
        prefix = candles[: t + 1]
        ev = _evaluate(
            prefix,
            interval=interval,
            period=period,
            bo_mod=bo_mod,
            vol_mod=vol_mod,
            sqz_mod=sqz_mod,
        )
        for d in ("long", "short"):
            counts[d]["n"] += 1
            for k in ("a", "b", "c", "d", "ab", "abc", "abcd"):
                if ev[d][k]:
                    counts[d][k] += 1
        if ev["bo_direction"] in ("bull", "bear"):
            n_bo_dir_set += 1
    return counts, n_bo_dir_set, n


def _print_tally(ticker, counts, n_bo_dir_set, n):
    evaluated = counts["long"]["n"]
    print(f"\n{ticker}: bars={n} (evaluated={evaluated} post-warmup)")
    print(f"  market-breakout reported direction on {n_bo_dir_set}/{evaluated} bars")
    print(f"  {'layer':<14} {'long':>8} {'short':>8}")
    for key, label in [
        ("a", "(a) direction"),
        ("b", "(b) vol>1.2"),
        ("c", "(c) sqz/OBV"),
        ("d", "(d) stop dist"),
        ("ab", "(a)+b"),
        ("abc", "(a)+b+c"),
        ("abcd", "(a)+b+c+d"),
    ]:
        print(f"  {label:<14} {counts['long'][key]:>8} {counts['short'][key]:>8}")


def main(argv=None):
    p = argparse.ArgumentParser(description="breakout-confirm v2-contract probe.")
    p.add_argument("--tickers", default="BTCUSD,ETHUSD,SOLUSD")
    p.add_argument("--interval", default="1d")
    p.add_argument("--period", default="1y")
    p.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    args = p.parse_args(argv)

    bo_mod = load_skill("market-breakout")
    vol_mod = load_skill("market-volume")
    sqz_mod = load_skill("market-squeeze")

    for tk in args.tickers.split(","):
        tk = tk.strip()
        if not tk:
            continue
        raw = fetch_ohlc(tk, interval=args.interval, period=args.period)
        if not raw:
            print(f"warn: no candles for {tk} ({args.interval}/{args.period})", file=sys.stderr)
            continue
        counts, n_bo_dir_set, n = _walk(
            raw,
            interval=args.interval,
            period=args.period,
            warmup=args.warmup,
            bo_mod=bo_mod,
            vol_mod=vol_mod,
            sqz_mod=sqz_mod,
        )
        _print_tally(tk, counts, n_bo_dir_set, n)


if __name__ == "__main__":
    main()
