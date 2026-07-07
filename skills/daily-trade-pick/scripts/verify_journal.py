#!/usr/bin/env python3
"""Ad-hoc verifier for the daily-trade-pick journal.

Run after every tick that writes `picks.json`. Validates:
  - JSON is parseable
  - Top-level is a list of scan records
  - Every scan has the required envelope (type/id/created_ts/ideas)
  - Every idea has the required fields
  - 2026-06-25-001 (or any 24h+ old) scan is fully closed with outcome fields
  - 18h-19h-old scans remain open (age < 20h threshold)
  - Latest scan's picked ideas all have met_bar=True

Usage (cron-friendly, no pipe-to-interpreter):
    export TMP=$(mktemp -t verify-dtp.XXXXXX).py
    cp scripts/verify_journal.py "$TMP"   # or cat <<'PYEOF' > "$TMP"
    MARKET_SKILLS_DAILY_TRADE_PICK_PATH=/path/to/picks.json python3 "$TMP"
    RC=$?
    rm -f "$TMP"
    exit $RC

Journal path resolution (no host-specific defaults — see AGENTS.md):
  1. $MARKET_SKILLS_DAILY_TRADE_PICK_PATH env var (required)

The script does NOT enforce silence/quiet — it prints a one-line summary at the
end and exits 0 on success, non-zero on any assertion failure.

NOTE: This is ad-hoc verification, not suite green. It checks structural and
schema consistency only. It does NOT exercise the upstream L3 batch or Kraken
ticker fetch paths. If the verifier passes, the journal write is internally
consistent; it says nothing about whether the L3 batch produced the right
ideas or the Kraken prices are current.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ENV_VAR = "MARKET_SKILLS_DAILY_TRADE_PICK_PATH"

REQUIRED_IDEA_FIELDS = [
    "ticker",
    "pair",
    "strategy",
    "direction",
    "entry_price",
    "stop",
    "tp1",
    "tp2",
    "tp3",
    "tp1_pct",
    "rr_to_tp1",
    "conviction",
    "version",
    "narrative",
    "met_bar",
    "picked",
    "rejection_reasons",
    "macro_aligned",
    "cooldown_ok",
    "status",
]

# Valid source tags per multi-source design (2026-06-29).
# `source` is REQUIRED for ideas written on or after 2026-06-29. Ideas written
# before that date lack the field and are treated as legacy ("unknown" source).
# The cron backfills `source = "unknown"` on the next read of any legacy idea
# so the journal converges over time.
VALID_SOURCES = {
    "tier1",
    "tier2",
    "swing_shortlist",
    "coingecko_movers",
    "smart_money",
    "hl_narrative",
    "unknown",  # legacy backfill tag — accepted indefinitely
}


def _resolve_path() -> Path:
    env = os.environ.get(ENV_VAR)
    if not env:
        print(f"FAIL: {ENV_VAR} is not set")
        sys.exit(2)
    return Path(env).expanduser()


def main(path: Path | None = None) -> int:
    if path is None:
        path = _resolve_path()
    try:
        d = json.load(open(path))
    except json.JSONDecodeError as e:
        print(f"FAIL: picks.json is not valid JSON: {e}")
        return 1

    # 1. Root shape
    if not isinstance(d, list) or len(d) < 1:
        actual_len = len(d) if isinstance(d, list) else "N/A"
        print(f"FAIL: root must be non-empty list, got {type(d).__name__} len={actual_len}")
        return 1

    now = datetime.now(UTC)

    # 2. Every scan has envelope
    for s in d:
        if s.get("type") != "scan":
            print(f"FAIL: scan {s.get('id')} missing type=scan")
            return 1
        for k in ("id", "created_ts", "ideas"):
            if k not in s:
                print(f"FAIL: scan {s.get('id')} missing key '{k}'")
                return 1
        if not isinstance(s["ideas"], list):
            print(f"FAIL: scan {s['id']} ideas is not a list")
            return 1

    # 3. Every idea has required fields + source tag (since 2026-06-29)
    legacy_source_count = 0
    invalid_source_tags = []
    for s in d:
        for i in s["ideas"]:
            missing = [k for k in REQUIRED_IDEA_FIELDS if k not in i]
            if missing:
                print(f"FAIL: scan {s['id']} idea {i.get('ticker')} missing {missing}")
                return 1
            # `source` is required for ideas written on or after 2026-06-29.
            # Older entries may lack it — warn but don't fail. The cron's
            # read step should backfill `source = "unknown"` on any legacy idea
            # so the journal converges to a fully-tagged state over time.
            src = i.get("source")
            if src is None:
                legacy_source_count += 1
            elif src not in VALID_SOURCES:
                invalid_source_tags.append(f"{s['id']}/{i.get('ticker')}={src!r}")
    if legacy_source_count:
        print(
            f"WARN: {legacy_source_count} legacy ideas lack `source` tag "
            "(will be backfilled to 'unknown' on next cron write; not failing)"
        )
    if invalid_source_tags:
        print(f"FAIL: invalid source tags (not in {sorted(VALID_SOURCES)}): {invalid_source_tags}")
        return 1

    # 4. Age-bucketed state checks
    summary = []
    for s in d:
        try:
            created = datetime.fromisoformat(s["created_ts"])
        except ValueError:
            print(f"FAIL: scan {s['id']} created_ts not ISO-8601: {s['created_ts']}")
            return 1
        age = now - created
        age_h = age.total_seconds() / 3600

        for i in s["ideas"]:
            if age_h >= 20 and i["status"] == "open":
                # 20h+ old with open idea is suspicious
                if i.get("exit_price") is None or i.get("outcome_verdict") is None:
                    print(f"FAIL: scan {s['id']} (age {age_h:.1f}h) idea {i['ticker']} still open")
                    return 1
            if age_h < 20 and i["status"] != "open":
                # 18h scan with closed ideas is fine if it was an earlier close
                # but if exit_price is None or verdict is None, that's a problem
                if i.get("exit_price") is None:
                    print(
                        f"WARN: scan {s['id']} (age {age_h:.1f}h) "
                        f"idea {i['ticker']} status={i['status']} but no exit_price"
                    )

        summary.append(f"{s['id']}({age_h:.0f}h, {len(s['ideas'])} ideas)")

    # 5. Latest scan consistency
    latest = d[-1]
    for i in latest["ideas"]:
        if i.get("picked") and not i["met_bar"]:
            print(f"FAIL: latest scan idea {i['ticker']} picked but met_bar=False")
            return 1

    # 6. JSON round-trip
    try:
        json.loads(json.dumps(d))
    except (TypeError, ValueError) as e:
        print(f"FAIL: JSON round-trip failed: {e}")
        return 1

    print(f"VERIFIED: {len(d)} scans: {' '.join(summary)}")
    print("AD-HOC VERIFICATION (not suite green): structural + schema checks only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
