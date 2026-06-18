#!/usr/bin/env bash
# position-watchdog wrapper — activates the uv venv and runs the watchdog.
# Cron job should point to this script.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"
exec uv run --no-sync python "$SKILL_DIR/scripts/run.py" "$@"
