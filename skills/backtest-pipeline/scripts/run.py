#!/usr/bin/env python3
"""backtest-pipeline — nightly backtest pipeline.

Runs every L3 strategy against every active ticker on multiple intervals
(1d, 4h), compares against a rolling 7-night baseline, and produces five
cross-boundary output files consumed by downstream skills.

Schedule: 02:00 CEST (after feedback absorber, before morning brief).
Mode: --no-agent (zero LLM tokens).

Output files (all written to ``$MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR``):
  conviction_thresholds_private.json  → DTP conviction floor
  fitness_matrix.json                 → ESD conviction modulation
  watchdog_regime_state.json          → Position Watchdog alert suppression
  swing_scan_skip_list.json           → Swing Scan ticker triage
  regime_health_brief.md              → Morning Brief injection

Usage:
    uv run skills/backtest-pipeline/scripts/run.py
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from analysis.registry import l3_strategies
from analysis.skill_loader import load_lib_for_script

_lib = load_lib_for_script(__file__)

# ── Repo root resolution ───────────────────────────────────────────

_SKILL_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SKILL_DIR.parents[1]


def _resolve_out_dir() -> Path:
    env = os.environ.get(_lib.ENV_OUT_DIR)
    if env:
        return Path(env).expanduser()
    raise OSError(
        f"{_lib.ENV_OUT_DIR} is not set; point it at the directory "
        f"where the five pipeline output files should be written"
    )


def _resolve_state_file(out_dir: Path) -> Path:
    return out_dir / "backtest-pipeline-state.json"


# ── Pipeline constants ─────────────────────────────────────────────

PRIMARY_STRATEGIES = [
    "strategy-trend-follow",
    "strategy-mean-reversion",
    "strategy-accumulation-swing",
]

SHARPE_DROP_DELTA = 0.5
BENCHMARK_BEAT_DELTA = 1.0
PAIR_TIMEOUT = 120
MIN_FORWARD_BARS = 150

BACKTEST_INTERVALS: list[tuple[str, str, int, int]] = [
    ("1d", "1y", 100, 500),
    ("4h", "3mo", 200, 400),
]

_SOURCE_TO_PREFIX = {
    "hyperliquid": "hl",
    "kraken": "kraken",
    "yfinance": "yf",
}

_BACKTEST_PROVIDER_OVERRIDE: dict[str, str] = {
    "HYPEUSD": "hl:HYPEUSD",
}


def _secondary_strategies() -> list[str]:
    all_strats = l3_strategies()
    return [s for s in all_strats if s not in PRIMARY_STRATEGIES][:3]


# ── Ticker discovery ───────────────────────────────────────────────


def _read_active_tickers(baskets: list[str] | None = None) -> list[tuple[str, str]]:
    """Read tickers from watchlist baskets.

    Args:
        baskets: Explicit basket names. If None, uses all baskets.
    """
    import analysis.watchlist as wl

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    categories = baskets if baskets else wl.categories()
    for basket_name in categories:
        basket = wl.basket(basket_name)
        for ticker_key, meta in basket.items():
            if ticker_key in seen:
                continue
            seen.add(ticker_key)
            if ":" in ticker_key:
                result.append((ticker_key, ticker_key))
                continue
            override = _BACKTEST_PROVIDER_OVERRIDE.get(ticker_key)
            if override:
                result.append((ticker_key, override))
                continue
            source = meta.get("source", "kraken") if isinstance(meta, dict) else "kraken"
            prefix = _SOURCE_TO_PREFIX.get(source, "kraken")
            result.append((ticker_key, f"{prefix}:{ticker_key}"))
    return result


# ── Rolling baseline state ─────────────────────────────────────────


def _load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {"first_run": True, "last_run_ts": None, "baseline": {}, "runs": []}
    with state_file.open() as fh:
        return json.load(fh)


def _save_state(state: dict, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("w") as fh:
        json.dump(state, fh, indent=2)


def _append_run_log(run_record: dict, out_dir: Path) -> None:
    log_file = out_dir / "runs.jsonl"
    with log_file.open("a") as fh:
        fh.write(json.dumps(run_record) + "\n")


# ── Per-pair backtest execution ────────────────────────────────────


def _run_pair(
    strategy: str,
    ticker_key: str,
    provider_ticker: str,
    *,
    interval: str = "1d",
    warmup: int = 100,
    bars: int = 500,
    period: str = "1y",
    demo: bool = False,
) -> dict | None:
    demo_flag = "--demo " if demo else ""
    repo = shlex.quote(str(_REPO_ROOT))
    strategy_q = shlex.quote(strategy)
    ticker_q = shlex.quote(provider_ticker)
    interval_q = shlex.quote(interval)
    period_q = shlex.quote(period)
    cmd = [
        "/bin/bash",
        "-c",
        f"unset VIRTUAL_ENV && cd {repo} && "
        f"uv run skills/backtest-engine/scripts/run.py "
        f"--strategy {strategy_q} --ticker {ticker_q} "
        f"--interval {interval_q} --warmup {warmup} --bars {bars} --period {period_q} "
        f"--fill-sim --metrics --json {demo_flag}2>&1",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=PAIR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    out = r.stdout
    try:
        start = out.find("{")
        envelope = json.loads(out[start:])
        if not envelope.get("data"):
            return None
        d = envelope["data"]
        m = d.get("metrics", {})
        if not m:
            return None
        if "strategy" not in m or "benchmark" not in m:
            return None
        if demo:
            return None

        bars_val = d.get("bars", 0)
        windows = d.get("windows", 0)
        provider = provider_ticker.split(":")[0] if ":" in provider_ticker else "auto"

        trades = m["strategy"].get("trade_count", 0)
        if windows < MIN_FORWARD_BARS and trades == 0:
            return {
                "strategy": strategy,
                "ticker": provider_ticker,
                "asof": datetime.now(UTC).isoformat(timespec="seconds"),
                "ideas": 0,
                "trades": 0,
                "strategy_sharpe": 0.0,
                "strategy_total_return": 0.0,
                "strategy_max_dd": 0.0,
                "strategy_profit_factor": 0.0,
                "benchmark_sharpe": m.get("benchmark", {}).get("sharpe"),
                "benchmark_total_return": m.get("benchmark", {}).get("total_return"),
                "bars": bars_val,
                "windows": windows,
                "provider": provider,
                "insufficient_data": True,
            }

        return {
            "strategy": strategy,
            "ticker": provider_ticker,
            "asof": datetime.now(UTC).isoformat(timespec="seconds"),
            "ideas": d.get("ideas", 0),
            "trades": m["strategy"].get("trade_count", 0),
            "strategy_sharpe": m["strategy"].get("sharpe"),
            "strategy_total_return": m["strategy"].get("total_return"),
            "strategy_max_dd": m["strategy"].get("max_drawdown"),
            "strategy_profit_factor": m["strategy"].get("profit_factor"),
            "benchmark_sharpe": m["benchmark"].get("sharpe"),
            "benchmark_total_return": m["benchmark"].get("total_return"),
            "bars": bars_val,
            "windows": windows,
            "provider": provider,
            "insufficient_data": False,
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


# ── Strategy decay detection ───────────────────────────────────────


def _summarize_strategy_decay(state: dict, current: dict) -> list[str]:
    out = []
    baseline = state.get("baseline", {})
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        cur_sharpe = info.get("strategy_sharpe")
        if cur_sharpe is None or not isinstance(cur_sharpe, (int, float)):
            continue
        prior_entry = baseline.get(key)
        if not isinstance(prior_entry, dict):
            continue
        prior = prior_entry.get("avg_sharpe_7n")
        if prior is None or not isinstance(prior, (int, float)):
            continue
        delta = cur_sharpe - prior
        if prior < 0 < cur_sharpe:
            out.append(f"\U0001f4c8 {key}: Sharpe crossed zero ({prior:+.2f} \u2192 {cur_sharpe:+.2f})")
        elif prior > 0 > cur_sharpe:
            out.append(f"\U0001f4c9 {key}: Sharpe crossed zero inverse ({prior:+.2f} \u2192 {cur_sharpe:+.2f})")
        elif abs(delta) >= SHARPE_DROP_DELTA:
            arrow = "\U0001f4c8" if delta > 0 else "\U0001f4c9"
            out.append(f"{arrow} {key}: Sharpe {delta:+.2f} (7n avg {prior:+.2f} \u2192 now {cur_sharpe:+.2f})")
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        strat_s = info.get("strategy_sharpe")
        bench_s = info.get("benchmark_sharpe")
        if isinstance(strat_s, (int, float)) and isinstance(bench_s, (int, float)):
            beat = bench_s - strat_s
            if beat > BENCHMARK_BEAT_DELTA:
                out.append(
                    f"\u2696\ufe0f  {key}: benchmark beats strategy by {beat:+.2f} Sharpe "
                    f"(buy-and-hold may be alpha-neutral here)"
                )
    return out


# ── Rolling baseline ───────────────────────────────────────────────


def _update_baseline(state: dict, current: dict) -> None:
    baseline = state.setdefault("baseline", {})
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        sharpe = info.get("strategy_sharpe")
        if sharpe is None or not isinstance(sharpe, (int, float)):
            continue
        slot = baseline.setdefault(key, {"history": []})
        slot["history"].append({"ts": info["asof"], "sharpe": sharpe})
        slot["history"] = slot["history"][-7:]
        avg = sum(h["sharpe"] for h in slot["history"]) / len(slot["history"])
        slot["avg_sharpe_7n"] = round(avg, 3)
        slot["n_samples"] = len(slot["history"])


# ═══════════════════════════════════════════════════════════════════
#   Pipeline output writers
# ═══════════════════════════════════════════════════════════════════


def _write_conviction_thresholds(current: dict, state: dict, out_dir: Path) -> None:
    thresholds: dict = {
        "GLOBAL_MIN_CONVICTION_TO_EMIT": 1,
        "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {},
    }
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        strat = info.get("strategy")
        ticker = info.get("ticker")
        sharpe = info.get("strategy_sharpe")
        if not strat or not ticker or sharpe is None or not isinstance(sharpe, (int, float)):
            continue
        if sharpe >= 0.5:
            floor = 1
        elif sharpe > 0:
            floor = 4
        else:
            floor = 99
        strat_dict = thresholds["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"].setdefault(strat, {})
        interval = key.split("\u00d7")[0] if "\u00d7" in key else "1d"
        ticker_entry = strat_dict.setdefault(ticker, {})
        existing = ticker_entry.get(interval, 99)
        if floor < existing:
            ticker_entry[interval] = floor

    path = out_dir / "conviction_thresholds_private.json"
    path.write_text(json.dumps(thresholds, indent=2))
    n_strategies = len(thresholds["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"])
    n_tickers = sum(len(v) for v in thresholds["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"].values())
    print(f"  \u2192 conviction thresholds written ({n_strategies} strategies, {n_tickers} tickers)", flush=True)


def _write_fitness_matrix(current: dict, state: dict, out_dir: Path) -> None:
    intervals_set: set[str] = set()
    tickers_set: set[str] = set()
    strategies_set: set[str] = set()
    sharpe_map: dict[tuple[str, str, str], float] = {}
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        parts = key.split("\u00d7")
        if len(parts) != 3:
            continue
        interval, strategy, ticker = parts
        sharpe = info.get("strategy_sharpe")
        if not isinstance(sharpe, (int, float)):
            continue
        intervals_set.add(interval)
        tickers_set.add(ticker)
        strategies_set.add(strategy)
        sharpe_map[(interval, ticker, strategy)] = sharpe

    tickers_sorted = sorted(tickers_set)
    strategies_sorted = sorted(strategies_set)
    intervals_sorted = sorted(intervals_set)

    matrix_data: dict[str, dict] = {}
    for interval in intervals_sorted:
        values = []
        for t in tickers_sorted:
            row: list[float | None] = []
            for s in strategies_sorted:
                row.append(sharpe_map.get((interval, t, s)))
            values.append(row)
        matrix_data[interval] = {
            "tickers": tickers_sorted,
            "strategies": strategies_sorted,
            "values": values,
        }

    payload = {
        "intervals": matrix_data,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _, validate_err = _lib.validate_fitness_matrix(payload)
    if validate_err:
        print(f"  [WARN] fitness matrix validation failed: {validate_err}", flush=True)
    path = out_dir / "fitness_matrix.json"
    path.write_text(json.dumps(payload, indent=2))
    cells = sum(len(m["tickers"]) * len(m["strategies"]) for m in matrix_data.values())
    print(f"  \u2192 fitness matrix written ({cells} cells across {len(intervals_sorted)} intervals)", flush=True)


def _write_watchdog_regime(current: dict, state: dict, out_dir: Path) -> None:
    open_positions_path = os.environ.get(_lib.ENV_OPEN_POSITIONS_PATH)
    if not open_positions_path:
        print(f"  [WARN] {_lib.ENV_OPEN_POSITIONS_PATH} not set, skipping watchdog regime output", flush=True)
        return
    try:
        with open(os.path.expanduser(open_positions_path)) as fh:
            positions = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        positions = {"watches": []}

    regime: dict = {"positions": {}}
    for watch in positions.get("watches", []):
        name = watch.get("name", "")
        if not watch.get("enabled"):
            continue
        for sig in watch.get("signals", []):
            for strat_name in sig.get("strategies", []):
                provider = watch.get("monitor_provider", "")
                ticker = provider.split(":")[-1] if ":" in provider else provider
                match_candidates = [
                    f"1d\u00d7strategy-{strat_name}\u00d7{ticker}",
                    f"4h\u00d7strategy-{strat_name}\u00d7{ticker}",
                ]
                info = {}
                for mc in match_candidates:
                    i = current.get(mc)
                    if i:
                        info = i
                        break
                base = {}
                for mc in match_candidates:
                    b = state.get("baseline", {}).get(mc)
                    if b:
                        base = b
                        break
                sharpe_now = info.get("strategy_sharpe") if isinstance(info, dict) else None
                sharpe_7n = base.get("avg_sharpe_7n") if isinstance(base, dict) else None
                status = (
                    "positive"
                    if (isinstance(sharpe_7n, (int, float)) and sharpe_7n > 0)
                    else "negative"
                    if isinstance(sharpe_7n, (int, float))
                    else "unknown"
                )
                rec = (
                    "ADD OK"
                    if status == "positive"
                    else "HOLD \u2014 strategy is regime-negative, skip adds"
                    if status == "negative"
                    else "monitor"
                )
                regime["positions"].setdefault(name, {})
                regime["positions"][name][strat_name] = {
                    "ticker": ticker,
                    "sharpe_now": sharpe_now,
                    "sharpe_7n": sharpe_7n,
                    "regime_status": status,
                    "recommendation": rec,
                }

    _, validate_err = _lib.validate_watchdog_regime(regime)
    if validate_err:
        print(f"  [WARN] watchdog regime validation failed: {validate_err}", flush=True)
    path = out_dir / "watchdog_regime_state.json"
    path.write_text(json.dumps(regime, indent=2))
    print(f"  \u2192 watchdog regime written ({len(regime['positions'])} watches)", flush=True)


def _write_swing_scan_skip(current: dict, state: dict, out_dir: Path) -> None:
    ticker_sharpes: dict[str, list[float]] = {}
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        ticker = info.get("ticker")
        sharpe = info.get("strategy_sharpe")
        if ticker and isinstance(sharpe, (int, float)):
            ticker_sharpes.setdefault(ticker, []).append(sharpe)

    skip_tickers = []
    keep_tickers = []
    for ticker, sharpes in ticker_sharpes.items():
        if all(s <= 0 for s in sharpes):
            skip_tickers.append(ticker)
        else:
            keep_tickers.append(ticker)

    payload = {
        "skip_tickers": sorted(skip_tickers),
        "keep_tickers": sorted(keep_tickers),
        "reason": "all strategies have negative Sharpe on these tickers",
    }
    _, validate_err = _lib.validate_swing_scan_skip(payload)
    if validate_err:
        print(f"  [WARN] swing scan skip validation failed: {validate_err}", flush=True)
    path = out_dir / "swing_scan_skip_list.json"
    path.write_text(json.dumps(payload, indent=2))
    print(f"  \u2192 swing scan skip list written ({len(skip_tickers)} skip, {len(keep_tickers)} keep)", flush=True)


def _write_regime_health_brief(current: dict, state: dict, out_dir: Path) -> None:
    lines = ["## \U0001f52c Backtest Regime Health (nightly)", ""]
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*Auto-generated at {ts} \u2014 feeds conviction thresholds, watchdog, and swing scan.*")
    lines.append("")

    strat_agg: dict[str, list[float]] = {}
    for info in current.values():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        s = info.get("strategy_sharpe")
        strat = info.get("strategy")
        if isinstance(s, (int, float)) and strat:
            strat_agg.setdefault(strat, []).append(s)

    lines.append("### Strategy Health (avg Sharpe across all tickers)")
    lines.append("| Strategy | Avg Sharpe | # Tickers | Verdict |")
    lines.append("|----------|-----------|-----------|---------|")
    for strat in sorted(strat_agg):
        vals = strat_agg[strat]
        avg = sum(vals) / len(vals)
        verdict = "\U0001f7e2 healthy" if avg > 0 else "\U0001f534 avoid" if avg < -0.5 else "\U0001f7e1 marginal"
        lines.append(f"| {strat} | {avg:+.2f} | {len(vals)} | {verdict} |")
    lines.append("")

    pairs = []
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        s = info.get("strategy_sharpe")
        if isinstance(s, (int, float)):
            pairs.append((key, s, info.get("trades", 0)))
    pairs.sort(key=lambda x: x[1], reverse=True)

    lines.append("### \U0001f7e2 Top 5 by Sharpe")
    lines.append("| Pair | Sharpe | Trades |")
    lines.append("|------|--------|--------|")
    for key, sharpe, trades in pairs[:5]:
        lines.append(f"| {key} | {sharpe:+.2f} | {trades} |")
    lines.append("")

    lines.append("### \U0001f534 Bottom 5 by Sharpe")
    for key, sharpe, trades in pairs[-5:]:
        lines.append(f"| {key} | {sharpe:+.2f} | {trades} |")
    lines.append("")

    skip_data = {}
    for key, info in current.items():
        if not isinstance(info, dict):
            continue
        if info.get("insufficient_data"):
            continue
        ticker = info.get("ticker")
        s = info.get("strategy_sharpe")
        if ticker and isinstance(s, (int, float)):
            skip_data.setdefault(ticker, []).append(s)
    all_neg = [t for t, ss in skip_data.items() if all(s <= 0 for s in ss)]
    if all_neg:
        lines.append(f"### \u23ed\ufe0f  {len(all_neg)} tickers skipped (all strategies negative)")
        lines.append(", ".join(sorted(all_neg)))
        lines.append("")

    text = "\n".join(lines) + "\n"
    _, validate_err = _lib.validate_regime_brief(text)
    if validate_err:
        print(f"  [WARN] regime brief validation failed: {validate_err}", flush=True)
    path = out_dir / "regime_health_brief.md"
    path.write_text(text)
    print("  \u2192 regime health brief written", flush=True)


# ═══════════════════════════════════════════════════════════════════
#   Main
# ═══════════════════════════════════════════════════════════════════


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="backtest-pipeline")
    p.add_argument(
        "--baskets",
        nargs="*",
        help="Watchlist basket names to backtest (default: all except macro_refs)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    out_dir = _resolve_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    state_file = _resolve_state_file(out_dir)

    state = _load_state(state_file)
    is_first_run = state.get("first_run", True)
    ticker_pairs = _read_active_tickers(baskets=args.baskets)

    strategies = PRIMARY_STRATEGIES + _secondary_strategies()
    intervals = BACKTEST_INTERVALS

    total_possible = len(strategies) * len(ticker_pairs) * len(intervals)
    print(
        f"\U0001f4ca backtest-pipeline: {len(strategies)} strategies \u00d7 "
        f"{len(ticker_pairs)} tickers \u00d7 {len(intervals)} intervals = "
        f"{total_possible} pairs",
        flush=True,
    )

    current: dict[str, dict] = {}
    errors: list[str] = []
    insufficient: list[str] = []
    pair_count = 0
    for interval, period, warmup, bars in intervals:
        for strategy in strategies:
            for ticker_key, provider_ticker in ticker_pairs:
                pair_count += 1
                res = _run_pair(
                    strategy,
                    ticker_key,
                    provider_ticker,
                    interval=interval,
                    warmup=warmup,
                    bars=bars,
                    period=period,
                )
                if res is None:
                    errors.append(f"{interval}\u00d7{strategy}\u00d7{ticker_key}")
                    continue
                key = f"{interval}\u00d7{strategy}\u00d7{ticker_key}"
                current[key] = res
                if res.get("insufficient_data"):
                    insufficient.append(
                        f"{key} (bars={res.get('bars', '?')}, "
                        f"windows={res.get('windows', '?')}, "
                        f"provider={res.get('provider', '?')})"
                    )
                    print(
                        f"  \u26a0 {key}: insufficient data \u2014 "
                        f"bars={res.get('bars', '?')} windows={res.get('windows', '?')} "
                        f"provider={res.get('provider', '?')}",
                        flush=True,
                    )
                    continue
                sharpe_val = res.get("strategy_sharpe")
                sharpe_str = f"{sharpe_val:+.2f}" if isinstance(sharpe_val, (int, float)) else str(sharpe_val)
                print(
                    f"  \u2713 {key}: sharpe={sharpe_str} trades={res['trades']} "
                    f"bars={res.get('bars', '?')} provider={res.get('provider', '?')}",
                    flush=True,
                )

    run_record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategies": strategies,
        "tickers": [tk for tk, _ in ticker_pairs],
        "results": current,
        "errors": errors,
        "insufficient_data": insufficient,
    }
    _append_run_log(run_record, out_dir)

    _write_conviction_thresholds(current, state, out_dir)
    _write_fitness_matrix(current, state, out_dir)
    _write_watchdog_regime(current, state, out_dir)
    _write_swing_scan_skip(current, state, out_dir)
    _write_regime_health_brief(current, state, out_dir)

    if is_first_run:
        state["first_run"] = False
        state["last_run_ts"] = run_record["ts"]
        _update_baseline(state, current)
        _save_state(state, state_file)
        print(f"\n(first run: baseline initialized, {len(errors)} errors suppressed)", flush=True)
        return 0

    findings = _summarize_strategy_decay(state, current)

    state["last_run_ts"] = run_record["ts"]
    _update_baseline(state, current)
    _save_state(state, state_file)

    if findings:
        print("\n\U0001f4cb Backtest findings (vs 7-night baseline):", flush=True)
        for f in findings:
            print(f"  {f}", flush=True)
    if errors:
        print(f"\n\u26a0\ufe0f  {len(errors)} pair(s) errored (likely missing data on Kraken):", flush=True)
        for e in errors[:5]:
            print(f"  - {e}", flush=True)
    if insufficient:
        print(
            f"\n\u26a0\ufe0f  {len(insufficient)} pair(s) had insufficient data "
            f"(below {MIN_FORWARD_BARS} forward bars):",
            flush=True,
        )
        for e in insufficient[:5]:
            print(f"  - {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
