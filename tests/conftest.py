"""Global test configuration."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--skip-network",
        action="store_true",
        default=False,
        help="Skip tests that require live network access",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "network: marks tests that require live network access (skip with --skip-network)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--skip-network"):
        skip_network = pytest.mark.skip(reason="skipping network tests (--skip-network)")
        for item in items:
            if "network" in item.keywords:
                item.add_marker(skip_network)
