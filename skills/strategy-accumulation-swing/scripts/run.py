#!/usr/bin/env python3
"""strategy-accumulation-swing — L3 accumulation swing strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "ACCUMULATION SWING"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
