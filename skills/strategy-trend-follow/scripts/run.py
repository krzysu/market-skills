#!/usr/bin/env python3
"""strategy-trend-follow — L3 trend-following strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "TREND FOLLOW"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
