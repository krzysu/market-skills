"""Global test configuration."""

import importlib

import pytest

import analysis.signals.conviction_thresholds as ct


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


@pytest.fixture(autouse=True)
def _hermetic_conviction_gate(monkeypatch):
    """Make the conviction gate hermetic against the ambient pipeline env.

    ``analysis.signals.conviction_thresholds`` loads its overrides at
    import time from ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` or the
    ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR/conviction_thresholds_private.json``
    fallback. In a shell where either var is exported (e.g. a profile
    ``.env``), the live private table (floors up to 99) is pre-loaded and
    strategy tests asserting an emitted idea would be silently suppressed
    — they would be testing the ambient environment, not the strategy.

    This fixture gives every test the shipped empty-table state whatever
    the ambient env: snapshot the current module state, delete BOTH path
    vars (plus the gate kill switch), reload the module so the shipped
    table is what the test sees, then restore on exit.

    Autouse at conftest level is the reusable placement: the gate applies
    through ``finalize_ideas`` to every L3 strategy, so every current and
    future strategy-level test has the same exposure (the same fixture is
    already a local precedent in ``tests/test_conviction_thresholds.py``
    and ``tests/test_conviction_thresholds_notation.py``). The reload is
    cheap (~0.2ms); callers that re-import the module at call time (e.g.
    ``finalize_ideas``'s lazy ``from ... import lookup_min_conviction``,
    resolved through ``sys.modules`` on each call) re-read the reloaded
    module dict, and tests that mutate the table or global via
    ``monkeypatch.setattr`` still work because the fixture rebinds the
    module's attributes BEFORE the test's monkeypatch runs.
    """
    saved_global = ct.GLOBAL_MIN_CONVICTION_TO_EMIT
    saved_table: dict[str, dict[tuple[str, str], int]] = {
        k: dict(v) for k, v in ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.items()
    }
    monkeypatch.delenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", raising=False)
    monkeypatch.delenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", raising=False)
    monkeypatch.delenv("MARKET_SKILLS_CONVICTION_GATE", raising=False)
    importlib.reload(ct)
    yield
    ct.GLOBAL_MIN_CONVICTION_TO_EMIT = saved_global
    ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.clear()
    ct.MIN_CONVICTION_TO_EMIT_BY_STRATEGY.update(saved_table)
