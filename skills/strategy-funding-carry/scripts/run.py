#!/usr/bin/env python3
"""strategy-funding-carry — L3 funding rate carry strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "FUNDING RATE CARRY"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
