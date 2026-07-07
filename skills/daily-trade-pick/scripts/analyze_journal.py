#!/usr/bin/env python3
"""DTP journal analysis — what's the hit rate, what's bleeding, where are the gaps?

Run from any session when the user asks "how are daily picks going",
"anything we can learn", or "review the journal". Aggregates the picks.json
state file by hit/miss, conviction, direction, ticker, macro alignment, and
day-by-day trend. Surfaces the actionable cuts (which tickers to cut, which
filters are inverted, what the pick rate looks like).

Usage:
    python3 scripts/analyze_journal.py                # human-readable text
    python3 scripts/analyze_journal.py --json         # machine output
    python3 scripts/analyze_journal.py --journal /path/to/picks.json

Journal path resolution (no host-specific defaults — see AGENTS.md):
  1. --journal=PATH flag (highest precedence)
  2. $MARKET_SKILLS_DAILY_TRADE_PICK_PATH env var
  3. OSError if neither is set

Writes: stdout only. Does NOT modify the journal.

Tolerance for the verdict field: handles both "hit"/"miss" and "expired"
(per the dtp_journal_verifier.py contract — see skill SKILL.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ENV_VAR = "MARKET_SKILLS_DAILY_TRADE_PICK_PATH"


def resolve_journal_path(explicit: str | os.PathLike | None = None) -> Path:
    """Resolve the picks.json path. Raises OSError if neither flag nor env var is set.

    AGENTS.md: library code must not bake host-specific paths into defaults.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    raise OSError(
        f"{ENV_VAR} is not set and no --journal path was provided. Set the env var or pass --journal=PATH explicitly."
    )


def load_ideas(path: Path) -> list[dict]:
    if not path.exists():
        print(f"No journal at {path}", file=sys.stderr)
        return []
    data = json.loads(path.read_text())
    out = []
    for scan in data:
        if scan.get("type") != "scan":
            continue
        for idea in scan.get("ideas", []):
            idea["_scan_id"] = scan.get("id")
            idea["_created"] = scan.get("created_ts", "")
            out.append(idea)
    return out


def split_closed(all_ideas: list[dict]) -> tuple[list[dict], list[dict]]:
    closed = [i for i in all_ideas if i.get("status") == "closed"]
    scored = [i for i in closed if i.get("outcome_verdict") in ("hit", "miss")]
    return closed, scored


def by_strat(d: list[dict], key: str) -> dict:
    bucket = defaultdict(lambda: {"hit": 0, "miss": 0, "ret_sum": 0.0, "extra_sum": 0.0})
    for i in d:
        v = i.get(key, "?")
        v = str(v) if not isinstance(v, str) else v
        verdict = i.get("outcome_verdict")
        if verdict not in ("hit", "miss"):
            continue
        bucket[v]["hit" if verdict == "hit" else "miss"] += 1
        bucket[v]["ret_sum"] += i.get("actual_return_pct", 0) or 0
        if key == "conviction":
            bucket[v]["extra_sum"] += i.get("conviction", 0) or 0
    return dict(bucket)


def recent_days(all_ideas: list[dict], n_days: int) -> list[tuple[str, dict]]:
    dates = sorted({i["_created"][:10] for i in all_ideas if i.get("_created")})
    if not dates:
        return []
    # Clamp the window to what the data actually covers — `dates[-n_days]`
    # raises IndexError when fewer than n_days distinct dates exist.
    cutoff = dates[max(-n_days, -len(dates))]
    recent = sorted([i for i in all_ideas if i.get("_created", "")[:10] >= cutoff], key=lambda x: x["_created"])
    out = []
    for d in sorted({i["_created"][:10] for i in recent}):
        day_ideas = [i for i in recent if i["_created"][:10] == d]
        closed_day = [i for i in day_ideas if i.get("outcome_verdict") in ("hit", "miss")]
        n_picked = sum(1 for i in day_ideas if i.get("picked"))
        n_hit = sum(1 for i in closed_day if i.get("outcome_verdict") == "hit")
        n_miss = sum(1 for i in closed_day if i.get("outcome_verdict") == "miss")
        avg_ret = sum(i.get("actual_return_pct", 0) for i in closed_day) / len(closed_day) if closed_day else 0.0
        out.append((d, {"ideas": len(day_ideas), "picked": n_picked, "hit": n_hit, "miss": n_miss, "avg_ret": avg_ret}))
    return out


def render_text(ideas: list[dict]) -> str:
    if not ideas:
        return "No journal data."
    closed, scored = split_closed(ideas)
    dates = sorted({i["_created"][:10] for i in ideas if i.get("_created")})
    out = []
    out.append(f"Total scans: {len({i['_scan_id'] for i in ideas})}")
    out.append(f"Total ideas logged: {len(ideas)}")
    if dates:
        out.append(f"Date range: {dates[0]} to {dates[-1]} ({len(dates)} days)\n")
    out.append("=== Volume ===")
    picked = [i for i in ideas if i.get("picked")]
    out.append(f"  Picked (acted on):     {len(picked):3d} ({len(picked) / max(1, len(ideas)) * 100:.0f}%)")
    out.append(f"  Closed (outcomes known): {len(closed):3d}\n")

    rej = Counter()
    for i in ideas:
        for r in i.get("rejection_reasons", []) or []:
            rej[r] += 1
    out.append("=== Top rejection reasons ===")
    for r, c in rej.most_common(8):
        out.append(f"  {c:3d}  {r}")
    out.append("")

    hits = [i for i in scored if i.get("outcome_verdict") == "hit"]
    misses = [i for i in scored if i.get("outcome_verdict") == "miss"]
    out.append("=== Outcomes (closed, scored only) ===")
    out.append(f"  Hits:  {len(hits):3d}")
    out.append(f"  Misses: {len(misses):3d}")
    if scored:
        out.append(f"  Hit rate: {len(hits) / len(scored) * 100:.1f}%")
    if hits:
        out.append(f"  Avg return on hit: {sum(i.get('actual_return_pct', 0) for i in hits) / len(hits):+.2f}%")
    if misses:
        out.append(f"  Avg return on miss: {sum(i.get('actual_return_pct', 0) for i in misses) / len(misses):+.2f}%")
    out.append("")

    out.append("=== Outcomes by ticker (top 15 by volume) ===")
    b = by_strat(scored, "pair")
    b = {k: v for k, v in b.items() if k}
    top = sorted(b.items(), key=lambda kv: -(kv[1]["hit"] + kv[1]["miss"]))[:15]
    for t, d in top:
        total = d["hit"] + d["miss"]
        hr = d["hit"] / total * 100 if total else 0
        avg = d["ret_sum"] / total if total else 0
        out.append(f"  {t:<10} {total:>2d} picks  hit rate {hr:>3.0f}%   avg ret {avg:+5.2f}%")
    out.append("")

    out.append("=== Outcomes by direction ===")
    for d, dd in by_strat(scored, "direction").items():
        total = dd["hit"] + dd["miss"]
        if total == 0:
            continue
        hr = dd["hit"] / total * 100
        avg = dd["ret_sum"] / total
        out.append(f"  {d:<6} {total:>2d} picks  hit rate {hr:>3.0f}%   avg ret {avg:+5.2f}%")
    out.append("")

    out.append("=== Outcomes by conviction ===")
    b = by_strat(scored, "conviction")
    for c in sorted(b.keys(), reverse=True):
        d = b[c]
        total = d["hit"] + d["miss"]
        if total == 0:
            continue
        hr = d["hit"] / total * 100
        avg = d["ret_sum"] / total
        out.append(f"  conv={c}  {total:>2d} picks  hit rate {hr:>3.0f}%   avg ret {avg:+5.2f}%")
    out.append("")

    out.append("=== Outcomes by macro_aligned_count ===")
    b = by_strat(scored, "macro_aligned_count")
    for m, d in sorted(b.items(), key=lambda kv: str(kv[0])):
        total = d["hit"] + d["miss"]
        if total == 0:
            continue
        hr = d["hit"] / total * 100
        avg = d["ret_sum"] / total
        out.append(f"  macro={m:<50} {total:>2d} picks  hit rate {hr:>3.0f}%   avg ret {avg:+5.2f}%")
    out.append("")

    out.append("=== Last 7 days ===")
    for d, stats in recent_days(ideas, 7):
        out.append(
            f"  {d}  ideas={stats['ideas']:2d}  picked={stats['picked']}  "
            f"hit/miss={stats['hit']}/{stats['miss']}  avg_ret={stats['avg_ret']:+.2f}%"
        )
    out.append("")

    out.append("=== Picked vs not-picked (scored only) ===")
    for flag in [True, False]:
        bucket = [i for i in scored if i.get("picked") == flag]
        if bucket:
            hr = sum(1 for i in bucket if i.get("outcome_verdict") == "hit") / len(bucket) * 100
            avg = sum(i.get("actual_return_pct", 0) for i in bucket) / len(bucket)
            out.append(f"  picked={flag!s:<5}  {len(bucket):>2d} closed  hit rate {hr:.0f}%   avg ret {avg:+.2f}%")
    return "\n".join(out)


def render_json(ideas: list[dict]) -> dict:
    closed, scored = split_closed(ideas)
    return {
        "total_ideas": len(ideas),
        "total_scans": len({i["_scan_id"] for i in ideas}),
        "picked": len([i for i in ideas if i.get("picked")]),
        "closed": len(closed),
        "scored": len(scored),
        "hit_rate": (sum(1 for i in scored if i.get("outcome_verdict") == "hit") / len(scored) if scored else None),
        "by_ticker": by_strat(scored, "pair"),
        "by_direction": by_strat(scored, "direction"),
        "by_conviction": by_strat(scored, "conviction"),
        "by_macro": by_strat(scored, "macro_aligned_count"),
        "recent_7d": [{"date": d, **stats} for d, stats in recent_days(ideas, 7)],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    ap.add_argument("--journal", type=Path, default=None, help=f"Path to picks.json (overrides ${ENV_VAR})")
    args = ap.parse_args()

    try:
        journal = resolve_journal_path(args.journal)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    ideas = load_ideas(journal)
    if args.json:
        print(json.dumps(render_json(ideas), indent=2, default=str))
    else:
        print(render_text(ideas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
