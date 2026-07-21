"""Quick QC: run tests + ruff on the strategy-fitness-heatmap worktree."""
import subprocess
import sys

worktree = "/Users/bulka/agents/market-skills/.worktrees/t_12caeb5b"

commands = [
    ["uv", "run", "pytest", "tests/test_strategy_fitness_heatmap.py", "-q"],
    ["uv", "run", "ruff", "check", "tests/test_strategy_fitness_heatmap.py"],
    ["uv", "run", "ruff", "check", "skills/strategy-fitness-heatmap/scripts/run.py"],
]

any_failed = False
for cmd in commands:
    result = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True)
    label = " ".join(cmd)
    print(f"--- {label} ---")
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    print(f"RC={result.returncode}")
    print()
    if result.returncode != 0:
        any_failed = True

sys.exit(1 if any_failed else 0)
