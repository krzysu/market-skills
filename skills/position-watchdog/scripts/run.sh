#!/usr/bin/env bash
# position-watchdog wrapper — activates the uv venv and runs the watchdog.
# Cron job should point to this script.
#
# --venue-stops is enabled for the scheduled tick: the venue's closed-order
# history is read each tick so a venue-executed stop fill gets reported (a
# resting stop never passes through the execution skill). Pass
# --no-venue-stops to turn it back off.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"
exec uv run --no-sync python "$SKILL_DIR/scripts/run.py" --venue-stops "$@"
