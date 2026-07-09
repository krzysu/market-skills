"""Regression test for the asset-class argv parsing consistency across strategy scripts.

The six strategy skills share a single CLI runner (``analysis.strategy_runner``)
that defines ``_parse_asset_class(argv)`` and calls it with the
``filtered_argv`` returned from ``parse_axi_flags``. Earlier
``strategy-trend-follow/scripts/run.py`` passed ``sys.argv[1:]`` instead of
``filtered_argv`` — that bug is fixed and the behaviour now lives in
``analysis.strategy_runner``.

This test pins:
- ``_parse_asset_class`` extracts the value from any argv
- The shared runner calls ``_parse_asset_class`` with ``filtered_argv`` (not
  ``sys.argv[1:]``)
- All six strategy scripts delegate to the same runner
"""

import importlib.util
import os
from typing import Any

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")

STRATEGIES = [
    "strategy-trend-follow",
    "strategy-mean-reversion",
    "strategy-liquidity-sweep",
    "strategy-exhaustion-fade",
    "strategy-breakout-confirm",
    "strategy-accumulation-swing",
]


def _load_runner() -> Any:
    runner_path = os.path.join(REPO_ROOT, "analysis", "strategy_runner.py")
    spec = importlib.util.spec_from_file_location("analysis_strategy_runner", runner_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_run_module(skill_name: str) -> Any:
    run_path = os.path.join(REPO_ROOT, "skills", skill_name, "scripts", "run.py")
    spec = importlib.util.spec_from_file_location(f"{skill_name}_run", run_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAssetClassParsing:
    """The shared strategy runner must extract --asset-class from filtered_argv."""

    def test_extracts_class_value(self):
        runner = _load_runner()
        assert runner._parse_asset_class(["TICKER", "--asset-class=futures"]) == "futures"

    def test_returns_none_when_absent(self):
        runner = _load_runner()
        assert runner._parse_asset_class(["TICKER"]) is None

    def test_ignores_unrelated_flags(self):
        runner = _load_runner()
        assert runner._parse_asset_class(["TICKER", "--fields=pair", "--full", "--json"]) is None

    def test_works_after_axi_strip(self):
        """The function must still find --asset-class= when AXI flags precede it."""
        runner = _load_runner()
        argv = ["TICKER", "--fields=pair", "--asset-class=spot", "--json"]
        assert runner._parse_asset_class(argv) == "spot"

    def test_runner_uses_filtered_argv(self):
        """Regression: the runner must pass filtered_argv into _parse_asset_class."""
        runner_path = os.path.join(REPO_ROOT, "analysis", "strategy_runner.py")
        with open(runner_path) as f:
            source = f.read()
        assert "_parse_asset_class(filtered_argv)" in source, (
            "analysis.strategy_runner must pass filtered_argv into _parse_asset_class"
        )
        assert "_parse_asset_class(sys.argv[1:])" not in source, (
            "analysis.strategy_runner must not pass sys.argv[1:] into _parse_asset_class"
        )

    def test_all_six_strategies_use_runner(self):
        """All six strategy scripts should delegate to analysis.strategy_runner."""
        for skill in STRATEGIES:
            run_path = os.path.join(REPO_ROOT, "skills", skill, "scripts", "run.py")
            with open(run_path) as f:
                source = f.read()
            assert "from analysis.strategy_runner import run_strategy_cli" in source, (
                f"{skill} must delegate to analysis.strategy_runner.run_strategy_cli"
            )
