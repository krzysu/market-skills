#!/usr/bin/env python3
"""liq-sweep conviction-formula calibration grid search (bead market-skills-7eq).

Scaffolds the calibration investigation recommended by the liq-sweep report
(Recommendation 1). For each candidate formula ``mode`` it walks a candle
series the same way ``strategy-liquidity-sweep`` would, applies the strategy's
gate, and tallies the conviction distribution the formula would have produced.
The actual constant change in ``lib.py`` is DEFERRED — this tool only reports;
it never edits the shipped formula.

Modes (see ``conviction_from_confidences`` in lib.py):
  current      min(5, sweep + accum // 2)   (shipped default)
  add          min(5, sweep + accum)
  add_minus_one min(5, sweep + accum - 1)
  max_plus_one min(5, max(sweep, accum) + 1)

Journal validation (optional, opt-in): pass ``--validate-journal`` to also
report the per-conviction-band hit rate from the operator's journal. The
journal path comes from the ``LIQ_SWEEP_JOURNAL_PATH`` environment variable; if
unset the script raises rather than falling back to a host-specific default.

Usage:
  uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py --demo
  uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py \
      --tickers BTCUSD,ETHUSD --interval 1d --period 2y --warmup 200
  uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py \
      --demo --validate-journal   # requires LIQ_SWEEP_JOURNAL_PATH
  uv run skills/strategy-liquidity-sweep/scripts/conviction_grid.py \
      --tickers BTCUSD,ETHUSD --interval 1d --period 1y --warmup 200 --holdout
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

from analysis.skill_loader import load_skill

_MODES = ["current", "add", "add_minus_one", "max_plus_one"]

# DTP tier-1/2 conviction gate from the liq-sweep investigation.
DTP_GATE = 3


def _make_demo_candles(n: int, seed: int = 7) -> list[list]:
    """Deterministic synthetic OHLC — a swing range that can trip sweep+accum.

    Not guaranteed to fire liq-sweep (the L2s need a real sweep + accumulation
    signature); it exists so the tool runs offline. Real tickers give signal.
    """
    rng = random.Random(seed)
    candles: list[list] = []
    price = 100.0
    for i in range(n):
        if i % 20 < 10:
            price += rng.uniform(-1.5, 0.5)  # drift down (accumulation-ish)
        else:
            price += rng.uniform(-0.5, 1.5)  # recover (sweep-ish)
        candles.append([i * 86400, price, price + 1.0, price - 1.0, price, rng.randint(100000, 500000)])
    return candles


def _gate_fires(sweep_mod, accum_mod, vol_mod, prefix, *, interval, period):
    """Replicate strategy-liquidity-sweep's fire gate; return (branch1, branch2).

    branch1 = sweep + accum + volume all confirm (conviction comes from the
    formula). branch2 = sweep + volume confirm but no accumulation (hardcoded
    conv=2). Returns the two L2 confidences for branch1 and a bool for branch2.
    """
    err = {"error": "unavailable", "pattern": {"present": False}}
    sweep_result = sweep_mod.analyze(prefix, interval=interval, period=period) if sweep_mod else err
    accum_result = accum_mod.analyze(prefix, interval=interval, period=period) if accum_mod else err
    vol_result = vol_mod.analyze(prefix, interval=interval, period=period) if vol_mod else err

    from analysis.contracts import l2_fired

    sweep_present = l2_fired(sweep_result)
    accum_present = l2_fired(accum_result)
    vol_ratio = vol_result.get("volume_ratio") if "error" not in vol_result else None
    obv_trend = vol_result.get("obv_trend") if "error" not in vol_result else None
    volume_confirms = vol_ratio is not None and vol_ratio > 1.0 and obv_trend == "rising"

    sweep_conf = sweep_result.get("pattern", {}).get("confidence", 3)
    accum_conf = accum_result.get("pattern", {}).get("confidence", 3)

    branch1 = sweep_present and accum_present and volume_confirms
    branch2 = sweep_present and not accum_present and volume_confirms
    return branch1, branch2, sweep_conf, accum_conf


def _fired_convictions_per_mode(candles, *, interval, period, warmup, modes, start_index=0):
    """Walk the series; for each fired bar collect conviction under every mode.

    Bars before ``start_index`` are skipped for tallying (used by ``--holdout``
    to evaluate only the out-of-sample tail) but remain available as leading
    context for the L2 detectors. Returns {mode: [conviction, ...]} for
    branch1 (formula-driven), plus the count of branch2 (hardcoded conv=2) fires.
    """
    from analysis.skill_loader import load_skill as _load

    strat_mod = _load("strategy-liquidity-sweep")
    conviction_from_confidences = strat_mod.conviction_from_confidences

    sweep_mod = load_skill("market-liquidity-sweep")
    accum_mod = load_skill("market-accumulation")
    vol_mod = load_skill("market-volume")

    per_mode: dict[str, list[int]] = {m: [] for m in modes}
    branch2_count = 0
    n = len(candles)
    start = max(start_index, warmup, 0)
    for t in range(start, n):
        prefix = candles[: t + 1]
        branch1, branch2, sweep_conf, accum_conf = _gate_fires(
            sweep_mod, accum_mod, vol_mod, prefix, interval=interval, period=period
        )
        if branch1:
            for m in modes:
                per_mode[m].append(conviction_from_confidences(sweep_conf, accum_conf, mode=m))
        elif branch2:
            branch2_count += 1
    return per_mode, branch2_count


def _print_histograms(per_mode, branch2_count, modes):
    header = "mode            " + "".join(f"{c:>6}" for c in range(1, 6)) + "   >=gate  total"
    print(header)
    print("-" * len(header))
    for m in modes:
        convs = per_mode[m]
        buckets = [sum(1 for c in convs if c == k) for k in range(1, 6)]
        ge_gate = sum(1 for c in convs if c >= DTP_GATE)
        print(f"{m:<14}" + "".join(f"{b:>6}" for b in buckets) + f"{ge_gate:>8}{len(convs):>7}")
    print(f"{'branch2(=2)':<14}{'-':>6}{'-':>6}{'-':>6}{'-':>6}{'-':>6}{branch2_count:>8}{branch2_count:>7}")
    print()
    print(f"DTP tier-1/2 gate = conv >= {DTP_GATE}. A mode is promising only if it")
    print("produces a healthy number of conv>=gate fires WITHOUT inflating conv=2")
    print("(the negative-EV band per the journal evidence).")


def _validate_journal(modes):
    """Report per-conviction-band hit rate from the operator journal.

    The journal path is read from ``LIQ_SWEEP_JOURNAL_PATH``; we raise if unset
    rather than defaulting to a host-specific path.
    """
    path = os.environ.get("LIQ_SWEEP_JOURNAL_PATH")
    if not path:
        raise RuntimeError(
            "LIQ_SWEEP_JOURNAL_PATH is unset; set it to the journal picks.json "
            "path to validate against outcomes (e.g. "
            "export LIQ_SWEEP_JOURNAL_PATH=<path-to-journal>/picks.json)"
        )
    with open(path) as fh:
        journal = json.load(fh)
    ideas = journal.get("ideas", journal) if isinstance(journal, dict) else journal
    bands: dict[int, list[dict]] = {k: [] for k in range(1, 6)}
    for idea in ideas:
        conv = idea.get("conviction")
        if isinstance(conv, int) and 1 <= conv <= 5:
            bands[conv].append(idea)
    print("Journal per-conviction-band outcome (source: LIQ_SWEEP_JOURNAL_PATH):")
    for k in range(1, 6):
        rows = bands[k]
        closed = [r for r in rows if r.get("status") == "closed"]
        hits = sum(1 for r in closed if (r.get("pnl") or 0) > 0)
        rate = hits / len(closed) if closed else 0.0
        avg = sum(r.get("pnl", 0) or 0 for r in closed) / len(closed) if closed else 0.0
        print(f"  conv={k}: n={len(rows)} closed={len(closed)} hit_rate={rate:.1%} avg_pnl={avg:+.2f}")


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="liq-sweep conviction-formula grid search (scaffold).")
    p.add_argument("--demo", action="store_true", help="Use deterministic synthetic candles (offline).")
    p.add_argument("--tickers", default=None, help="Comma-separated tickers (e.g. BTCUSD,ETHUSD).")
    p.add_argument("--interval", default="1d")
    p.add_argument("--period", default="2y")
    p.add_argument("--warmup", type=int, default=200)
    p.add_argument("--bars", type=int, default=0, help="Use the N most-recent candles (0 = all).")
    p.add_argument("--modes", default=",".join(_MODES), help="Comma-separated modes to score.")
    p.add_argument("--validate-journal", action="store_true", help="Also report journal per-band outcomes.")
    p.add_argument(
        "--holdout",
        action="store_true",
        help="Tally convictions only on the out-of-sample tail of each ticker (last 1-train_frac), "
        "keeping the leading portion as warmup context. Use this to select a formula WITHOUT peeking "
        "at the sample it will be deployed on.",
    )
    p.add_argument("--train-frac", type=float, default=0.7, help="Context fraction kept for --holdout (0.7).")
    return p.parse_args(argv)


def main() -> None:
    args = _parse_argv(sys.argv[1:])
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    if args.validate_journal:
        _validate_journal(modes)

    # Build one candle series per ticker (or a single demo series).
    if args.demo:
        series = [_make_demo_candles(args.bars if args.bars > 0 else 300)]
    elif args.tickers:
        from analysis.data import fetch_ohlc

        series = []
        for tk in args.tickers.split(","):
            raw = fetch_ohlc(tk, interval=args.interval, period=args.period)
            if not raw:
                print(f"warn: no candles for {tk}", file=sys.stderr)
                continue
            if args.bars > 0:
                raw = raw[-args.bars :]
            series.append(raw)
        if not series:
            print("error: no candles fetched", file=sys.stderr)
            sys.exit(2)
    else:
        print("error: pass --demo or --tickers", file=sys.stderr)
        sys.exit(2)

    if args.holdout:
        frac = max(0.0, min(1.0, args.train_frac))
        tag = (
            f"HOLDOUT (out-of-sample tail: last {int((1 - frac) * 100)}% of each series; "
            f"leading {int(frac * 100)}% kept as warmup context)"
        )
    else:
        frac = 0.0
        tag = "FULL SAMPLE (in-sample — use --holdout to validate out-of-sample before selecting a formula)"


    def start_of(n: int) -> int:
        return int(n * frac)

    per_mode: dict[str, list[int]] = {m: [] for m in modes}
    branch2 = 0
    for candles in series:
        si = start_of(len(candles))
        pm, b2 = _fired_convictions_per_mode(
            candles, interval=args.interval, period=args.period,
            warmup=args.warmup, modes=modes, start_index=si,
        )
        for m in modes:
            per_mode[m].extend(pm[m])
        branch2 += b2

    print(f"# {tag}")
    _print_histograms(per_mode, branch2, modes)
    sys.exit(0)


if __name__ == "__main__":
    main()
