#!/usr/bin/env python3
"""strategy-breakout-confirm — L3 breakout momentum strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "BREAKOUT CONFIRM"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
