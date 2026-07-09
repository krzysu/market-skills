#!/usr/bin/env python3
"""strategy-liquidity-sweep — L3 liquidity sweep reversal strategy."""

from analysis.strategy_runner import run_strategy_cli

STRATEGY_TITLE = "LIQUIDITY SWEEP"


def main():
    run_strategy_cli(STRATEGY_TITLE, __file__)


if __name__ == "__main__":
    main()
