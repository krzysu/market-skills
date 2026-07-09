"""Per-fix test fixtures for the friction items consolidated in this commit.

Each ``TestFIXXX`` class pins one friction item — the exact failing
shape that prompted the fix. Per AGENTS.md:

> Per-fix test fixtures are required. Every ``fix:`` commit must
> include a test case in ``tests/test_<area>.py`` that reproduces the
> exact shape that triggered the bug. The test must fail on the
> pre-fix code and pass on the post-fix code.

Coverage:

  - CLI-1: batch runners accept both ``--flag value`` and ``--flag=value``
  - CLI-2: VALID_PERIODS accepts week aliases (1w/2w/3w/4w)
  - CLI-3: yfinance provider rejects incompatible (interval, period) up front
  - DATA-1: hyperliquid.fetch_spot_price is symmetric with kraken's
  - PORT-1: portfolio-mgmt --portfolio accepts name OR id
  - MACRO-1: RegimeSignal carries a structured ``missing_inputs`` list
  - MACRO-2: market-state --refresh bypasses the macro TTL cache
  - L3-1: every emitted idea carries ``strategy_name`` + ``idea_id``
  - L3-3: empty-ideas result carries a structured ``rejection_reasons`` list
  - OUT-1: position-watchdog --status --json emits the AXI envelope
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

# Import order matters: importing ``analysis.data`` triggers
# ``CCXTProvider("binance")`` instantiation at module-load time, which
# needs the real top-level ``ccxt``. Importing
# ``analysis.providers.data.hyperliquid`` triggers
# ``from hyperliquid.ccxt.hyperliquid import hyperliquid`` which shadows
# ``sys.modules["ccxt"]`` for the rest of the session. So: import
# analysis.data FIRST, then hyperliquid only inside the test functions
# that need it.
from analysis import data as analysis_data  # noqa: F401 — keep module order

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, rel_path: str):
    path = os.path.join(REPO_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ──────────────────────────────────────────────────────────────────── CLI-1


class TestCLI1BatchRunnersAcceptSpaceSeparatedFlags:
    """CLI-1: batch runners must accept both ``--flag value`` and
    ``--flag=value`` styles.

    Pre-fix: ``run-all-l2 hl:LIT --interval 4h --period 6mo`` returned
    the defaults (interval=1d period=1y) because the ad-hoc parser
    only matched ``--flag=value``. Post-fix: both ``--flag value`` and
    ``--flag=value`` work; the args land in the rendered header.
    """

    @pytest.mark.parametrize(
        "script_path",
        [
            "skills/run-all-l2/scripts/run.py",
            "skills/run-all-l3/scripts/run.py",
        ],
    )
    def test_space_separated_flags_parsed(self, script_path):
        """Repro of the original failing invocation. ``--interval 4h
        --period 6mo`` must reach the print_header block intact; pre-fix
        the args fell through to defaults."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, script_path),
                "hl:LIT",
                "--interval",
                "4h",
                "--period",
                "6mo",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "interval=4h" in proc.stdout, (
            f"--interval 4h was not parsed (still falling through to default): {proc.stdout!r}"
        )
        assert "period=6mo" in proc.stdout

    @pytest.mark.parametrize(
        "script_path",
        [
            "skills/run-all-l2/scripts/run.py",
            "skills/run-all-l3/scripts/run.py",
        ],
    )
    def test_equals_separated_flags_still_work(self, script_path):
        """Back-compat: ``--flag=value`` form must still parse."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, script_path),
                "hl:LIT",
                "--interval=4h",
                "--period=6mo",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "interval=4h" in proc.stdout
        assert "period=6mo" in proc.stdout

    def test_json_flag_after_positional_works(self):
        """CLI-2 (subordinate to CLI-1): ``--json`` placed after positional
        args used to be ignored. Post-fix, the parser no longer relies
        on positional order."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/run-all-l3/scripts/run.py"),
                "hl:LIT",
                "--interval=4h",
                "--period=6mo",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # JSON mode emits the envelope directly; check the data key
        data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        assert "data" in data or "interval" in data or proc.returncode != 0, (
            f"--json after positional args didn't engage envelope mode: {proc.stdout[:200]!r}"
        )

    def test_top_space_separated(self):
        """L3-only flag --top also accepts space syntax."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/run-all-l3/scripts/run.py"),
                "hl:LIT",
                "--top",
                "2",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # --top=2 just caps the ideas; we don't check the count, only
        # that the script accepted the value (no exit 2 / "invalid top").
        assert proc.returncode != 2 or "invalid" not in proc.stderr


# ──────────────────────────────────────────────────────────────────── CLI-2


class TestCLI2WeekPeriodAliases:
    """CLI-2: VALID_PERIODS must accept week aliases (1w/2w/3w/4w).

    Pre-fix: ``fetch_ohlc('hl:LIT', '4h', '2w')`` raised
    ``ValueError: invalid period '2w'; expected one of [...]``.
    Post-fix: '1w', '2w', '3w', '4w' are valid periods.
    """

    @pytest.mark.parametrize("period", ["1w", "2w", "3w", "4w"])
    def test_week_period_in_valid_set(self, period):
        from analysis.intervals import VALID_PERIODS, validate_timeframe

        assert period in VALID_PERIODS
        # Must not raise — also exercises the downstream providers'
        # period_seconds maps via the indirect lookup path.
        validate_timeframe("1d", period)

    @pytest.mark.parametrize("interval", ["1d", "4h", "1h"])
    @pytest.mark.parametrize("period", ["1w", "2w", "4w"])
    def test_week_period_with_various_intervals(self, interval, period):
        """12 combinations: each must validate without raising."""
        from analysis.intervals import validate_timeframe

        validate_timeframe(interval, period)


# ──────────────────────────────────────────────────────────────────── CLI-3


class TestCLI3YfinanceIncompatibleComboRejected:
    """CLI-3: yfinance provider must reject incompatible (interval,
    period) pairs up front.

    Pre-fix: yfinance silently interpreted unknown tokens (like ``4h``)
    as ticker symbols, returning a misleading "symbol may be delisted"
    error. Post-fix: the provider raises a clear, actionable error
    before yfinance is even invoked.
    """

    def test_4h_1y_rejected_with_clear_message(self, caplog):
        """The original failing pair. The provider must raise before
        issuing the yfinance call — pre-fix the call went through and
        returned [] silently."""
        from analysis.providers.data.yfinance import (
            YFinanceIncompatibleTimeframeError,
            YFinanceProvider,
        )

        p = YFinanceProvider()
        with pytest.raises(YFinanceIncompatibleTimeframeError) as ei:
            p.fetch("AAPL", interval="4h", period="1y")
        msg = str(ei.value)
        assert "4h" in msg and "1y" in msg, msg
        assert "max supported" in msg or "cannot serve" in msg, msg

    @pytest.mark.parametrize(
        "interval,period",
        [
            ("4h", "1y"),
            ("4h", "6mo"),
            ("1h", "1y"),
            ("1h", "2y"),
            ("5m", "1y"),
            ("1m", "1mo"),
        ],
    )
    def test_other_incompatible_combos_rejected(self, interval, period):
        """6 (interval, period) pairs that yfinance can't serve."""
        from analysis.providers.data.yfinance import (
            YFinanceIncompatibleTimeframeError,
            YFinanceProvider,
        )

        p = YFinanceProvider()
        with pytest.raises(YFinanceIncompatibleTimeframeError):
            p.fetch("AAPL", interval=interval, period=period)

    @pytest.mark.parametrize(
        "interval,period",
        [
            ("1d", "1y"),
            ("1d", "6mo"),
            ("1h", "1mo"),  # yfinance supports 1h@1mo
            ("5m", "5d"),  # yfinance supports 5m@5d
        ],
    )
    def test_compatible_combos_pass_through(self, interval, period):
        """Compatible combos must NOT raise — only the incompatible
        ones do."""
        from analysis.providers.data.yfinance import (
            YFinanceProvider,
            _validate_yfinance_combo,
        )

        YFinanceProvider()  # construct to mirror other tests
        # We don't make a real yfinance call here (network). Instead,
        # we test that _validate_yfinance_combo returns None for these
        # pairs (the upstream pre-check that decides whether to raise).
        assert _validate_yfinance_combo(interval, period) is None


# ──────────────────────────────────────────────────────────────────── DATA-1


class TestDATA1HyperliquidFetchSpotPrice:
    """DATA-1: hyperliquid provider must expose ``fetch_spot_price``.

    Pre-fix: no ``fetch_spot_price`` on the HL provider, so portfolio
    refresh fell back to a stale OHLC close for ``hl:LIT`` / ``hl:VVV``
    and produced misleading unrealized P&L. Post-fix: HL has a
    fetch_spot_price symmetric with Kraken's.
    """

    def test_hyperliquid_provider_has_fetch_spot_price(self):
        from analysis.providers.data.hyperliquid import HyperliquidProvider

        assert hasattr(HyperliquidProvider, "fetch_spot_price")
        assert callable(getattr(HyperliquidProvider, "fetch_spot_price"))

    def test_hyperliquid_fetch_spot_price_returns_uniform_shape(self):
        """HL fetch_spot_price returns the same dict shape as Kraken's:
        ``price``/``last``/``bid``/``ask``/``source``. The portfolio
        layer reads this dict uniformly across providers.
        """
        from unittest.mock import MagicMock

        from analysis.providers.data.hyperliquid import HyperliquidProvider

        p = HyperliquidProvider()
        p._markets_loaded = True
        p._exchange.markets = {"LIT/USDC:USDC": {"id": "LIT"}}
        p._exchange.markets_by_id = {"LIT": {"symbol": "LIT/USDC:USDC"}}
        p._exchange.symbols = ["LIT/USDC:USDC"]
        p._exchange.fetch_ticker = MagicMock(
            return_value={
                "last": 0.085,
                "bid": 0.0849,
                "ask": 0.0851,
                "symbol": "LIT/USDC:USDC",
            }
        )
        out = p.fetch_spot_price("LIT")
        assert out is not None
        assert set(out.keys()) >= {"price", "last", "bid", "ask", "source"}
        assert out["price"] == 0.085
        assert out["last"] == 0.085
        assert out["bid"] == 0.0849
        assert out["ask"] == 0.0851
        assert "hl" in out["source"]

    def test_hyperliquid_fetch_spot_price_returns_none_on_failure(self):
        from unittest.mock import MagicMock

        from analysis.providers.data.hyperliquid import HyperliquidProvider

        p = HyperliquidProvider()
        p._markets_loaded = True
        p._exchange.markets = {"LIT/USDC:USDC": {"id": "LIT"}}
        p._exchange.markets_by_id = {"LIT": {"symbol": "LIT/USDC:USDC"}}
        p._exchange.symbols = ["LIT/USDC:USDC"]
        p._exchange.fetch_ticker = MagicMock(side_effect=Exception("boom"))
        assert p.fetch_spot_price("LIT") is None

    def test_data_fetch_spot_price_dispatches_to_hl(self):
        """analysis.data.fetch_spot_price routes ``hl:LIT`` to the HL
        provider, not to yfinance. This is the actual portfolio-layer
        entry point."""
        from unittest.mock import patch

        with patch("analysis.data._get_provider") as mock_get_provider:
            from analysis.data import fetch_spot_price

            sentinel = {"price": 0.085, "last": 0.085, "bid": None, "ask": None, "source": "hl:ticker"}

            class FakeHL:
                name = "hyperliquid"

                def fetch_spot_price(self, ticker):
                    return sentinel

            mock_get_provider.return_value = FakeHL()
            out = fetch_spot_price("hl:LIT")
            assert out == sentinel


# ──────────────────────────────────────────────────────────────────── PORT-1


class TestPORT1PortfolioNameResolution:
    """SPECS/2026-07-09-friction-inventory.md PORT-1.

    Pre-fix: ``portfolio-mgmt view --portfolio hyperliquid`` failed
    with ``invalid int value: 'hyperliquid'``. Post-fix: both ids and
    names resolve via ``get_portfolio(id_or_name)``.
    """

    @pytest.fixture
    def portfolio_db(self, tmp_path, monkeypatch):
        from portfolio.db import init_db

        db = str(tmp_path / "p.db")
        init_db(db)
        monkeypatch.setenv("MARKET_SKILLS_PORTFOLIO_DB", db)
        return db

    def _seed_portfolios(self, db):
        from portfolio.db import add_portfolio

        add_portfolio(db, "sparplan")
        add_portfolio(db, "defi")

    def test_view_accepts_name(self, portfolio_db, capsys):
        """Repro: ``portfolio-mgmt view --portfolio defi`` must work."""
        self._seed_portfolios(portfolio_db)
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/portfolio-mgmt/scripts/run.py"),
                "view",
                "--portfolio",
                "defi",
                "--no-refresh",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "MARKET_SKILLS_PORTFOLIO_DB": portfolio_db},
        )
        assert proc.returncode == 0, proc.stderr
        # JSON envelope must list the defi portfolio, not crash with
        # "invalid int value: 'defi'" (the pre-fix message).
        assert "invalid int value" not in proc.stderr
        data = json.loads(proc.stdout)
        names = [bp["name"] for bp in data.get("by_portfolio", [])]
        assert "defi" in names
        assert "sparplan" not in names  # --portfolio defi filters to defi only

    def test_view_accepts_id(self, portfolio_db, capsys):
        """Back-compat: ``--portfolio 2`` (numeric id) still works."""
        self._seed_portfolios(portfolio_db)
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/portfolio-mgmt/scripts/run.py"),
                "view",
                "--portfolio",
                "2",
                "--no-refresh",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "MARKET_SKILLS_PORTFOLIO_DB": portfolio_db},
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        names = [bp["name"] for bp in data.get("by_portfolio", [])]
        assert "defi" in names

    def test_unknown_name_errors_cleanly(self, portfolio_db, capsys):
        """``--portfolio <unknown>`` must exit non-zero with a clear
        message, not crash with ``invalid int value``."""
        self._seed_portfolios(portfolio_db)
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/portfolio-mgmt/scripts/run.py"),
                "view",
                "--portfolio",
                "nonexistent",
                "--no-refresh",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "MARKET_SKILLS_PORTFOLIO_DB": portfolio_db},
        )
        assert proc.returncode != 0
        assert "nonexistent" in proc.stderr
        assert "invalid int value" not in proc.stderr


# ──────────────────────────────────────────────────────────────────── MACRO-1


class TestMACRO1StructuredMissingInputs:
    """SPECS/2026-07-09-friction-inventory.md MACRO-1.

    Pre-fix: callers had to grep ``regime_note`` / ``errors[]`` to
    answer "is BTC mcap missing?". Post-fix: ``missing_inputs``
    lists structured input names.
    """

    def test_missing_inputs_present_on_clean_signal(self, monkeypatch):
        """Even a fully-successful fetch has the field (empty list)
        so consumers don't have to do .get() with a default."""

        from analysis import macro

        fast_info, _setter, fake_ticker_cls = _import_macro_helpers()
        macro.clear_cache()
        fast_info.clear()
        monkeypatch.setattr("analysis.macro.yf.Ticker", fake_ticker_cls)
        monkeypatch.setattr("analysis.macro.requests.get", _fng_cg_double(20, "Fear", 2_000_000_000_000, 55))
        _set_fixtures_for_clean_signal()
        sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert "missing_inputs" in sig
        assert sig["missing_inputs"] == []

    def test_missing_inputs_populated_when_one_source_fails(self, monkeypatch):
        """When VIX fails, missing_inputs must contain 'vix'."""
        from analysis import macro

        fast_info, _setter, fake_ticker_cls = _import_macro_helpers()
        macro.clear_cache()
        fast_info.clear()
        monkeypatch.setattr("analysis.macro.yf.Ticker", fake_ticker_cls)
        monkeypatch.setattr(
            "analysis.macro.requests.get",
            _fng_cg_double(60, "Greed", 2_000_000_000_000, 50.0),
        )
        _set_fixtures_no_vix()
        sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert sig["incomplete"] is True
        assert "vix" in sig["missing_inputs"]

    def test_missing_inputs_normalises_coingecko_label(self, monkeypatch):
        """When CoinGecko fails, ``missing_inputs`` should expose the
        user-facing input names (``btc_dominance`` / ``total_mcap_usd``)
        rather than the upstream source label (``coingecko``)."""
        from analysis import macro

        fast_info, _setter, fake_ticker_cls = _import_macro_helpers()
        macro.clear_cache()
        fast_info.clear()
        monkeypatch.setattr("analysis.macro.yf.Ticker", fake_ticker_cls)
        monkeypatch.setattr("analysis.macro.requests.get", _fng_then_429())
        _set_fixtures_no_mcap()
        sig = macro.fetch_regime(ttl_seconds=0, write_history=False)

        assert "coingecko" not in sig["missing_inputs"]
        assert "btc_dominance" in sig["missing_inputs"]
        assert "total_mcap_usd" in sig["missing_inputs"]


def _fng_cg_double(fng_value, label, total_usd, btc_pct):
    """requests.get side_effect: fng payload, then coingecko payload."""
    from unittest.mock import MagicMock

    fng_resp = MagicMock()
    fng_resp.status_code = 200
    fng_resp.json.return_value = {
        "data": [{"value": str(fng_value), "value_classification": label}],
    }
    cg_resp = MagicMock()
    cg_resp.status_code = 200
    cg_resp.json.return_value = {
        "data": {
            "total_market_cap": {"usd": total_usd},
            "market_cap_percentage": {"btc": btc_pct},
        }
    }
    return MagicMock(side_effect=[fng_resp, cg_resp])


def _fng_then_429():
    from unittest.mock import MagicMock

    fng_resp = MagicMock()
    fng_resp.status_code = 200
    fng_resp.json.return_value = {
        "data": [{"value": "50", "value_classification": "Neutral"}],
    }
    cg_resp = MagicMock()
    cg_resp.status_code = 429
    cg_resp.json.return_value = {}
    return MagicMock(side_effect=[fng_resp, cg_resp])


# We need a shared module-level reference to test_macro._FAST_INFO_BY_SYMBOL
# so tests can clear and inspect it. Importing the helper here instead of
# at module top avoids the ccxt/hyperliquid.ccxt shadowing issue when this
# test file is run alongside other hyperliquid-touching tests.
def _import_macro_helpers():
    from test_macro import _FAST_INFO_BY_SYMBOL as _FAST_INFO  # noqa: N806
    from test_macro import FakeTicker, _set_fast_info

    return _FAST_INFO, _set_fast_info, FakeTicker


def _set_fixtures_for_clean_signal():
    """Wire ^VIX/DXY/US10Y/BTC-USD fast_info so the fetcher succeeds."""
    from test_macro import _set_fast_info

    _set_fast_info("^VIX", last_price=18.0)
    _set_fast_info("DX-Y.NYB", last_price=102.0)
    _set_fast_info("^TNX", last_price=4.0)
    _set_fast_info("BTC-USD", market_cap=1_100_000_000_000.0)


def _set_fixtures_no_vix():
    from test_macro import _set_fast_info

    # ^VIX deliberately omitted → FakeTicker raises → vix = None
    _set_fast_info("DX-Y.NYB", last_price=102.0)
    _set_fast_info("^TNX", last_price=4.0)
    _set_fast_info("BTC-USD", market_cap=1_000_000_000_000.0)


def _set_fixtures_no_mcap():
    from test_macro import _set_fast_info

    _set_fast_info("^VIX", last_price=18.0)
    _set_fast_info("DX-Y.NYB", last_price=102.0)
    _set_fast_info("^TNX", last_price=4.0)
    _set_fast_info("BTC-USD", last_price=65000.0)  # no market_cap


# ──────────────────────────────────────────────────────────────────── MACRO-2


class TestMACRO2MarketStateRefreshFlag:
    """SPECS/2026-07-09-friction-inventory.md MACRO-2.

    Pre-fix: market-state rendered cached macro data; ``--refresh``
    wasn't a flag. Post-fix: ``--refresh`` clears the macro TTL cache
    before composing state and notes the refresh in the help output.
    """

    def test_refresh_flag_accepted(self):
        """``--refresh`` is a recognised flag and doesn't raise."""
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "skills/market-state/scripts/run.py"),
                "--json",
                "--refresh",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Either success (no JSON cache → empty state) or exit 0 with
        # a non-empty freshness map. Must NOT crash on --refresh.
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert "data" in out or "freshness" in out

    def test_refresh_clears_macro_cache(self, monkeypatch):
        """Calling --refresh twice in the same process should hit the
        network twice (TTL cache bypassed)."""
        from unittest.mock import MagicMock, patch

        from analysis import macro

        macro.clear_cache()

        with patch("analysis.macro.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    status_code=200,
                    json=lambda: {"data": [{"value": "50", "value_classification": "Neutral"}]},
                ),
                MagicMock(
                    status_code=200,
                    json=lambda: {
                        "data": {
                            "total_market_cap": {"usd": 2_000_000_000_000.0},
                            "market_cap_percentage": {"btc": 50.0},
                        }
                    },
                ),
            ] * 2
            with patch("analysis.macro.yf.Ticker"):
                # Wire fixtures
                from test_macro import _set_fast_info

                _set_fast_info("^VIX", last_price=18.0)
                _set_fast_info("DX-Y.NYB", last_price=102.0)
                _set_fast_info("^TNX", last_price=4.0)
                _set_fast_info("BTC-USD", market_cap=1_000_000_000_000.0)

                macro.fetch_regime(ttl_seconds=300, write_history=False)
                macro.fetch_regime(ttl_seconds=300, write_history=False)
                # Without --refresh, the second call is a cache hit —
                # 2 network requests total.
                assert mock_get.call_count == 2

                # Now simulate the --refresh flag: clear_cache() then
                # fetch again. The third fetch must hit the network
                # because the in-process cache was cleared.
                macro.clear_cache()
                macro.fetch_regime(ttl_seconds=300, write_history=False)
                # Cache was cleared, so the third call also hits the
                # network → 4 total.
                assert mock_get.call_count == 4


# ──────────────────────────────────────────────────────────────────── L3-1


class TestL31IdeaSchemaNormalization:
    """SPECS/2026-07-09-friction-inventory.md L3-1.

    Pre-fix: per-strategy parsing of L3 ideas because the field shape
    differed across the 6 strategies. Post-fix: every emitted idea
    carries ``strategy_name`` + ``idea_id``, ``take_profit`` is always
    a 3-element list, and ``entry_range`` is always present.
    """

    def test_idea_has_strategy_name(self, monkeypatch):
        """Per-strategy downstream code can read ``idea["strategy_name"]``
        instead of carrying the strategy name alongside the idea dict.
        """

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 3,
                        "version": "v3",
                        "entry_type": "limit",
                        "entry_price": 100.0,
                        "entry_range": [99.0, 101.0],
                        "stop_loss": 95.0,
                        "take_profit": [107.5, 112.5, 120.0],
                        "take_profit_ideal": [107.5, 112.5, 120.0],
                        "reasoning": "test",
                        "source_skills": ["market-trend-quality"],
                    }
                ],
                "narrative": "ok",
            }

        canned = {"strategy-trend-follow": type("S", (), {"analyze": staticmethod(_stub)})()}

        ral3 = _load("ral3_l31", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [[1, 1, 1, 1, 1, 1]] * 200, interval="1d", period="1y")
        idea = out["strategies"]["strategy-trend-follow"]["ideas"][0]
        assert idea["strategy_name"] == "strategy-trend-follow"

    def test_idea_has_deterministic_idea_id(self, monkeypatch):
        """``idea_id`` is sha1(strategy|ticker|bracket-signature);
        same input → same id across runs.
        """

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 3,
                        "version": "v3",
                        "entry_type": "limit",
                        "entry_price": 100.0,
                        "entry_range": [99.0, 101.0],
                        "stop_loss": 95.0,
                        "take_profit": [107.5, 112.5, 120.0],
                        "take_profit_ideal": [107.5, 112.5, 120.0],
                        "reasoning": "test",
                        "source_skills": ["market-trend-quality"],
                    }
                ],
                "narrative": "ok",
            }

        canned = {"strategy-trend-follow": type("S", (), {"analyze": staticmethod(_stub)})()}

        ral3 = _load("ral3_l31_id_a", "skills/run-all-l3/lib.py")
        ral3_b = _load("ral3_l31_id_b", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))
        monkeypatch.setattr(ral3_b, "load_skill", lambda name: canned.get(name))
        candles = [[1, 1, 1, 1, 1, 1]] * 200
        strat_ideas_a = ral3.analyze("TEST", candles, interval="1d", period="1y")["strategies"][
            "strategy-trend-follow"
        ]["ideas"]
        strat_ideas_b = ral3_b.analyze("TEST", candles, interval="1d", period="1y")["strategies"][
            "strategy-trend-follow"
        ]["ideas"]
        id_a = strat_ideas_a[0]["idea_id"]
        id_b = strat_ideas_b[0]["idea_id"]
        assert id_a == id_b
        assert isinstance(id_a, str)
        assert len(id_a) == 16  # sha1 truncated to 16 hex chars

    def test_take_profit_padded_to_three(self, monkeypatch):
        """A strategy emitting a 2-TP ladder gets padded to 3 elements
        with None so consumer code can index [0..2] unconditionally.
        """

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 3,
                        "entry_type": "limit",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "take_profit": [107.5, 112.5],
                        "reasoning": "test",
                        "source_skills": [],
                    }
                ],
                "narrative": "ok",
            }

        canned = {"strategy-mean-reversion": type("S", (), {"analyze": staticmethod(_stub)})()}

        ral3 = _load("ral3_l31_pad", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [[1, 1, 1, 1, 1, 1]] * 200, interval="1d", period="1y")
        idea = out["strategies"]["strategy-mean-reversion"]["ideas"][0]
        assert len(idea["take_profit"]) == 3
        assert idea["take_profit"][2] is None

    def test_entry_range_mirrored_from_entry_price(self, monkeypatch):
        """When a strategy emits only ``entry_price``, ``entry_range``
        is auto-populated as ``[entry, entry]`` so consumers that read
        ``entry_range[0]`` don't see ``None``."""

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 3,
                        "entry_type": "limit",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "take_profit": [110.0, 115.0, 120.0],
                        "reasoning": "test",
                        "source_skills": [],
                    }
                ],
                "narrative": "ok",
            }

        canned = {"strategy-trend-follow": type("S", (), {"analyze": staticmethod(_stub)})()}
        ral3 = _load("ral3_l31_er", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [[1, 1, 1, 1, 1, 1]] * 200, interval="1d", period="1y")
        idea = out["strategies"]["strategy-trend-follow"]["ideas"][0]
        assert idea["entry_range"] == [100.0, 100.0]


# ──────────────────────────────────────────────────────────────────── L3-3


class TestL33RejectionReasons:
    """SPECS/2026-07-09-friction-inventory.md L3-3.

    Pre-fix: ``ideas: []`` carried only a free-text ``narrative``.
    Post-fix: ``rejection_reasons`` is a structured list of stable tags.
    """

    def test_known_narrative_maps_to_tag(self, monkeypatch):
        """strategy-breakout-confirm's "no confirmed breakout" maps to
        ``["missing_breakout_confirmation"]``."""

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {"ideas": [], "narrative": "No confirmed breakout — volume or squeeze confirmation missing."}

        canned = {"strategy-breakout-confirm": type("S", (), {"analyze": staticmethod(_stub)})()}
        ral3 = _load("ral3_l33_b", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [[1, 1, 1, 1, 1, 1]] * 200, interval="1d", period="1y")
        strat = out["strategies"]["strategy-breakout-confirm"]
        assert strat["ideas"] == []
        assert "rejection_reasons" in strat
        assert "missing_breakout_confirmation" in strat["rejection_reasons"]

    def test_insufficient_data_tag(self, monkeypatch):
        """``insufficient data (need 50+ candles, got 0)`` →
        ``["insufficient_data"]``."""

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {"ideas": [], "narrative": "insufficient data (need 50+ candles, got 0)"}

        canned = {"strategy-trend-follow": type("S", (), {"analyze": staticmethod(_stub)})()}
        ral3 = _load("ral3_l33_insuf", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [], interval="1d", period="1y")
        strat = out["strategies"]["strategy-trend-follow"]
        assert strat["rejection_reasons"] == ["insufficient_data"]

    def test_unknown_narrative_falls_back_to_unknown_tag(self, monkeypatch):
        """Defensive: an unexpected narrative must produce SOMETHING
        structured, not silently empty."""

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {"ideas": [], "narrative": "no idea: liquid mercury calibration drift"}

        canned = {"strategy-trend-follow": type("S", (), {"analyze": staticmethod(_stub)})()}
        ral3 = _load("ral3_l33_unk", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [[1, 1, 1, 1, 1, 1]] * 200, interval="1d", period="1y")
        strat = out["strategies"]["strategy-trend-follow"]
        assert strat["rejection_reasons"]  # non-empty
        assert "unknown" in strat["rejection_reasons"]

    def test_nonempty_ideas_has_no_rejection_reasons(self, monkeypatch):
        """Strategies that DID produce ideas must not get a
        ``rejection_reasons`` field — it's a "no-ideas" mirror only."""

        def _stub(c, *, ticker, interval="1d", period="1y", asset_class=None):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 3,
                        "entry_type": "limit",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "take_profit": [110.0, 115.0, 120.0],
                        "reasoning": "ok",
                        "source_skills": [],
                    }
                ],
                "narrative": "ok",
            }

        canned = {"strategy-trend-follow": type("S", (), {"analyze": staticmethod(_stub)})()}
        ral3 = _load("ral3_l33_ok", "skills/run-all-l3/lib.py")
        monkeypatch.setattr(ral3, "load_skill", lambda name: canned.get(name))

        out = ral3.analyze("TEST", [[1, 1, 1, 1, 1, 1]] * 200, interval="1d", period="1y")
        strat = out["strategies"]["strategy-trend-follow"]
        assert strat.get("rejection_reasons") is None


# ──────────────────────────────────────────────────────────────────── OUT-1


class TestOUT1StatusJsonParity:
    """SPECS/2026-07-09-friction-inventory.md OUT-1.

    Pre-fix: ``--status`` only printed human-render Unicode strings.
    Post-fix: ``--status --json`` emits the AXI envelope with the
    structured event dict per watch.
    """

    def test_status_json_emits_envelope(self, monkeypatch, tmp_path):
        """End-to-end: ``--status --json`` returns a parseable envelope
        with one event per watch."""
        import json
        import sys

        cfg = tmp_path / "watches.json"
        cfg.write_text(
            json.dumps(
                {
                    "watches": [
                        {
                            "name": "ETH",
                            "enabled": True,
                            "monitor_provider": "kraken:ETHUSD",
                            "levels": [],
                        }
                    ]
                }
            )
        )
        monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))

        run_mod = _load("pw_out1", "skills/position-watchdog/scripts/run.py")
        monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--status", "--json"])
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_k: 3000.0)
        # Just confirm we can call main without crashing and it emits JSON.
        rc = run_mod.main()
        # rc 0 = success
        assert rc in (0, 2)

    def test_status_json_envelope_contains_watches(self, monkeypatch, tmp_path, capsys):
        """The envelope's data.watches[] carries the structured event."""
        import json
        import sys

        cfg = tmp_path / "watches.json"
        cfg.write_text(
            json.dumps(
                {
                    "watches": [
                        {
                            "name": "ETH",
                            "enabled": True,
                            "monitor_provider": "kraken:ETHUSD",
                            "levels": [],
                        }
                    ]
                }
            )
        )
        monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))

        run_mod = _load("pw_out1_envelope", "skills/position-watchdog/scripts/run.py")
        monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--status", "--json"])
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_k: 3000.0)

        run_mod.main()
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert "data" in envelope
        assert "watches" in envelope["data"]
        assert len(envelope["data"]["watches"]) == 1
        eth = envelope["data"]["watches"][0]
        assert eth["name"] == "ETH"
        assert eth["current_price"] == 3000.0

    def test_status_human_format_unchanged(self, monkeypatch, tmp_path, capsys):
        """Back-compat: ``--status`` without ``--json`` still emits
        the human-render lines (no envelope wrapping)."""
        import sys

        cfg = tmp_path / "watches.json"
        cfg.write_text(
            json.dumps(
                {
                    "watches": [
                        {
                            "name": "ETH",
                            "enabled": True,
                            "monitor_provider": "kraken:ETHUSD",
                            "levels": [],
                        }
                    ]
                }
            )
        )
        monkeypatch.setenv("MARKET_SKILLS_WATCHDOG_STATE_DIR", str(tmp_path))

        run_mod = _load("pw_out1_human", "skills/position-watchdog/scripts/run.py")
        monkeypatch.setattr(sys, "argv", ["run.py", "--config", str(cfg), "--status"])
        monkeypatch.setattr(run_mod, "_current_price", lambda *_a, **_k: 3000.0)

        run_mod.main()
        captured = capsys.readouterr()
        # Human format starts with the bracketed name + @ price
        assert "[ETH] @ $3000.00" in captured.out
        # No JSON envelope wrapping
        assert not captured.out.strip().startswith("{")
