#!/usr/bin/env python3
"""strategy-mean-reversion — L3 mean-reversion strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "MEAN REVERSION"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
