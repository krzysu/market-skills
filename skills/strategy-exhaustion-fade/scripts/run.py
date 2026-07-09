#!/usr/bin/env python3
"""strategy-exhaustion-fade — L3 exhaustion fade strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "EXHAUSTION FADE"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
