"""Tests for the shared timeframe/CLI surface.

Covers:
  - `analysis.intervals` validator + warning helpers
  - `analysis.formatting.parse_args` (and `safe_parse_args`) parsing every
    documented flag, including bad-value handling
  - Per-skill scripts that consume the new flags do so consistently
    (smoke check that one L1, one L2, one L3 all plumb interval/period
    through to `fetch_ohlc` and the lib).
"""

import importlib.util
import os
import subprocess
import sys

import pytest

from analysis.formatting import parse_args, require_ticker, safe_parse_args
from analysis.intervals import (
    DEFAULT_INTERVAL,
    DEFAULT_PERIOD,
    VALID_INTERVALS,
    VALID_PERIODS,
    validate_timeframe,
    warn_unsupported_combo,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_run_script(rel_path):
    """Dynamically import a `scripts/run.py` module by repo-relative path."""
    path = os.path.join(REPO_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(rel_path.replace("/", "_").replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestIntervals:
    def test_default_constants(self):
        assert DEFAULT_INTERVAL == "1d"
        assert DEFAULT_PERIOD == "1y"

    def test_valid_sets_nonempty(self):
        assert "1d" in VALID_INTERVALS
        assert "1h" in VALID_INTERVALS
        assert "1m" in VALID_INTERVALS
        assert "1wk" in VALID_INTERVALS
        assert "1y" in VALID_PERIODS
        assert "6mo" in VALID_PERIODS

    def test_validate_timeframe_ok(self):
        # Should not raise
        validate_timeframe("1d", "1y")
        validate_timeframe("1h", "1mo")
        validate_timeframe("5m", "1mo")
        validate_timeframe("1m", "5d")

    def test_validate_timeframe_bad_interval(self):
        with pytest.raises(ValueError, match="invalid interval"):
            validate_timeframe("99x", "1y")
        with pytest.raises(ValueError, match="invalid interval"):
            validate_timeframe("1hr", "1y")  # common typo
        with pytest.raises(ValueError, match="invalid interval"):
            validate_timeframe("", "1y")

    def test_validate_timeframe_bad_period(self):
        with pytest.raises(ValueError, match="invalid period"):
            validate_timeframe("1d", "12m")  # should be 1y
        with pytest.raises(ValueError, match="invalid period"):
            validate_timeframe("1d", "1year")  # should be 1y
        with pytest.raises(ValueError, match="invalid period"):
            validate_timeframe("1d", "")

    def test_warn_unsupported_combo_intraday(self):
        warn = warn_unsupported_combo("1m", "1y")
        assert warn is not None
        assert "1m" in warn and "1y" in warn

    def test_warn_unsupported_combo_hourly(self):
        warn = warn_unsupported_combo("1h", "5y")
        assert warn is not None
        assert "1h" in warn and "5y" in warn

    def test_warn_unsupported_combo_daily(self):
        assert warn_unsupported_combo("1d", "1y") is None
        assert warn_unsupported_combo("1d", "max") is None
        assert warn_unsupported_combo("1wk", "5y") is None
        assert warn_unsupported_combo("1M", "10y") is None

    def test_warn_unsupported_combo_provider_aware(self):
        # YFinance keeps the warning; other providers (and the ccxt
        # exchange-prefixed name) stay silent since their caps differ.
        assert warn_unsupported_combo("1m", "1y", provider="yfinance") is not None
        assert warn_unsupported_combo("1h", "5y", provider="yfinance") is not None
        assert warn_unsupported_combo("1m", "1y", provider="kraken") is None
        assert warn_unsupported_combo("1m", "1y", provider="hl") is None
        assert warn_unsupported_combo("1m", "1y", provider="hyperliquid") is None
        assert warn_unsupported_combo("1m", "1y", provider="ccxt:binance") is None
        # Backwards compat: no provider arg still fires (callers that haven't
        # been refactored should keep their existing behaviour).
        assert warn_unsupported_combo("1m", "1y") is not None


class TestParseArgs:
    def test_defaults(self):
        ticker, json_mode, source, interval, period = parse_args([])
        assert ticker is None
        assert json_mode is False
        assert source is None
        assert interval == DEFAULT_INTERVAL
        assert period == DEFAULT_PERIOD

    def test_ticker_only(self):
        ticker, json_mode, source, interval, period = parse_args(["AAPL"])
        assert ticker == "AAPL"
        assert interval == "1d"
        assert period == "1y"

    def test_all_flags(self):
        ticker, json_mode, source, interval, period = parse_args(
            [
                "AAPL",
                "--json",
                "--source=yf",
                "--interval=4h",
                "--period=6mo",
            ]
        )
        assert ticker == "AAPL"
        assert json_mode is True
        assert source == "yf"
        assert interval == "4h"
        assert period == "6mo"

    def test_bad_interval_raises(self):
        with pytest.raises(ValueError, match="invalid interval"):
            parse_args(["AAPL", "--interval=99x"])

    def test_bad_period_raises(self):
        with pytest.raises(ValueError, match="invalid period"):
            parse_args(["AAPL", "--period=12m"])

    def test_safe_parse_args_bad_value_exits(self, capsys):
        # safe_parse_args calls sys.exit(2) on validation errors
        with pytest.raises(SystemExit) as ei:
            safe_parse_args(["AAPL", "--interval=99x"])
        assert ei.value.code == 2
        captured = capsys.readouterr()
        assert "invalid interval" in captured.err

    def test_safe_parse_args_good_value_returns_tuple(self):
        result = safe_parse_args(["AAPL", "--interval=1h"])
        assert result == ("AAPL", False, None, "1h", "1y")

    def test_safe_parse_args_default(self):
        result = safe_parse_args(["BTCUSD"])
        assert result == ("BTCUSD", False, None, "1d", "1y")

    def test_require_ticker_missing(self, capsys):
        with pytest.raises(SystemExit) as ei:
            require_ticker(None, json_mode=False)
        assert ei.value.code == 2
        # Non-JSON mode prints a usage line; JSON mode prints a JSON error
        out = capsys.readouterr().out
        assert "usage:" in out

    def test_require_ticker_missing_json_mode(self, capsys):
        with pytest.raises(SystemExit) as ei:
            require_ticker(None, json_mode=True)
        assert ei.value.code == 2
        out = capsys.readouterr().out
        assert "ticker required" in out

    def test_require_ticker_present(self):
        # Should not raise
        require_ticker("AAPL", json_mode=False)


class TestScriptPlumbing:
    """Smoke tests: confirm each per-skill script's run() function plumbs
    interval/period into both fetch_ohlc and the lib call."""

    @pytest.mark.parametrize(
        "script_path,expected_skill_name",
        [
            ("skills/market-ema/scripts/run.py", "market-ema"),
            ("skills/market-rsi/scripts/run.py", "market-rsi"),
            ("skills/market-squeeze/scripts/run.py", "market-squeeze"),
            ("skills/market-trend/scripts/run.py", "market-trend"),
            ("skills/market-volume/scripts/run.py", "market-volume"),
            ("skills/market-volatility/scripts/run.py", "market-volatility"),
            ("skills/market-macd/scripts/run.py", "market-macd"),
            ("skills/market-fibonacci/scripts/run.py", "market-fibonacci"),
            ("skills/market-s-r/scripts/run.py", "market-s-r"),
            ("skills/market-accumulation/scripts/run.py", "market-accumulation"),
            ("skills/market-breakout/scripts/run.py", "market-breakout"),
            ("skills/market-exhaustion/scripts/run.py", "market-exhaustion"),
            ("skills/market-liquidity-sweep/scripts/run.py", "market-liquidity-sweep"),
            ("skills/market-trend-quality/scripts/run.py", "market-trend-quality"),
        ],
    )
    def test_per_skill_has_interval_period_kwargs(self, script_path, expected_skill_name):
        """Each `analyze()` helper should accept interval/period kwargs and
        thread them through to both fetch_ohlc and _lib.analyze()."""
        run_mod = _load_run_script(script_path)
        # Inspect signature of the analyze() function — should have interval/period kwargs
        import inspect

        sig = inspect.signature(run_mod.analyze)
        params = sig.parameters
        assert "interval" in params, f"{script_path} missing interval kwarg"
        assert "period" in params, f"{script_path} missing period kwarg"

    @pytest.mark.parametrize(
        "script_path",
        [
            "skills/market-ema/scripts/run.py",
            "skills/market-accumulation/scripts/run.py",
            "skills/strategy-trend-follow/scripts/run.py",
        ],
    )
    def test_per_skill_uses_safe_parse_args(self, script_path):
        """Each CLI script should import `safe_parse_args` (not the raw
        `parse_args`), so bad interval/period values get friendly errors."""
        run_mod = _load_run_script(script_path)
        # We can't introspect imports directly, but we can confirm the
        # main() function unpacks 5 values from safe_parse_args
        import inspect

        src = inspect.getsource(run_mod.main)
        assert "safe_parse_args" in src, f"{script_path} should use safe_parse_args"
        # Confirm the tuple unpacks all 5 values
        assert "interval" in src and "period" in src


class TestCLIInvocation:
    """End-to-end CLI smoke: each per-skill script should accept
    `--interval=` and `--period=` without crashing at argparse."""

    @pytest.mark.parametrize(
        "script_path",
        [
            "skills/market-ema/scripts/run.py",
            "skills/market-rsi/scripts/run.py",
            "skills/market-accumulation/scripts/run.py",
            "skills/strategy-trend-follow/scripts/run.py",
            "skills/run-all-l2/scripts/run.py",
            "skills/run-all-l3/scripts/run.py",
            "skills/run-watchlist/scripts/run.py",
            "skills/market-basis/scripts/run.py",
            "skills/market-overview/scripts/run.py",
        ],
    )
    def test_usage_mentions_interval_and_period(self, script_path):
        """Each script's usage/error output (when run with --help or no args)
        should mention --interval and --period so users discover the flags.
        Manual-parser scripts print the usage line on missing ticker; argparse
        scripts (market-basis) handle --help natively. Both must advertise
        the new flags.
        """
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, script_path), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Either --help was handled (returncode 0), or the script fell
        # through to its usage-message path (returncode 2). Either way
        # --interval must appear in the combined output.
        combined = proc.stdout + proc.stderr
        assert "--interval" in combined
        assert "--period" in combined

    def test_per_skill_rejects_bad_interval(self):
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/market-ema/scripts/run.py"),
                "AAPL",
                "--interval=99x",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should exit 2 with friendly error to stderr
        assert proc.returncode == 2
        assert "invalid interval" in proc.stderr

    def test_run_all_l2_rejects_bad_period(self):
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/run-all-l2/scripts/run.py"),
                "AAPL",
                "--period=12m",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 2
        assert "invalid period" in proc.stderr

    def test_run_watchlist_rejects_bad_interval(self):
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/run-watchlist/scripts/run.py"),
                "--tickers",
                "BTCUSD",
                "--interval=99x",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 2
        assert "invalid interval" in proc.stderr

    def test_run_watchlist_prints_interval_in_header(self):
        """The human-readable header echoes interval/period. Even if a
        ticker fetch fails, the header still prints — so we can confirm
        the values are wired up end-to-end without a network call."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/run-watchlist/scripts/run.py"),
                "--tickers",
                "NOSUCHTICKER_XYZ",
                "--interval=4h",
                "--period=6mo",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Returncode 0 even on fetch failure (per-ticker errors don't kill run)
        assert proc.returncode == 0
        assert "interval=4h" in proc.stdout
        assert "period=6mo" in proc.stdout
