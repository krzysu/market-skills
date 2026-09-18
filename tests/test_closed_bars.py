"""Per-fix test fixture for as-of-last-closed-bar trimming (bead market-skills-4n0).

Providers return the venue's raw series including the current, partially-elapsed
bar; every volume ratio computed against that partial bar is understated by the
elapsed-time fraction. These fixtures pin the fix: ``analysis.data.fetch_ohlc``
drops the non-closed trailing bar by default (uniform across providers, on both
the cache-hit and live paths, trim applied on read of a raw-payload cache), and
volume signals therefore stop reading partial bars.

Every fixture here is discriminating: on the pre-fix code the module is absent
(``analysis.bars``), ``fetch_ohlc`` rejects ``include_partial`` with a
``TypeError``, or the returned series still contains the partial bar — so the
volume-ratio and breakout-confirmation assertions fail against the measured
08:42 Swing-Scan behaviour (HYPE declined at "volume 0.37x SMA20" on a ~50%-elapsed
bar whose last CLOSED bar showed ~1.9x).

All tests are network-free and wall-clock-independent: providers are stubbed,
the disk cache is bypassed or pointed at ``tmp_path``, and the clock seam
(``analysis.bars.now_epoch``) is monkeypatched.
"""

from __future__ import annotations

import importlib.util
import os
from unittest.mock import MagicMock

import pytest

from analysis.bars import INTERVAL_SECONDS, closed_bars, last_bar_is_closed
from analysis.data import fetch_ohlc

FIXED_NOW = 1_760_000_000  # arbitrary fixed Unix second; tests never read the wall clock
FOUR_H = INTERVAL_SECONDS["4h"]

# The HYPE-shaped fixture: 221 bars, trailing in-progress bar opened 2h ago
# (50% elapsed). Last CLOSED bar is a breakout bar with 2x volume; the partial
# bar holds 0.37x-worth of volume — exactly the 08:42 Swing-Scan shape.
BREAKOUT_VOLUME = 2_000_000.0
BASE_VOLUME = 1_000_000.0
PARTIAL_VOLUME = 370_000.0

N_BARS = 221  # 220 closed bars + the forming bar; market-trend needs 200 for FULL_BULL


def _hype_series() -> list[list]:
    """4h series: steady uptrend (FULL_BULL), breakout close bar, thin partial bar.

    Bar i: open = previous close, high = close + 3, low = open - 3, so
    true range ~6.5 and ATR stays well above the 2% swing stop floor.
    """
    base_ts = FIXED_NOW - 7200 - (N_BARS - 1) * FOUR_H
    series: list[list] = []
    close = 100.0
    for i in range(N_BARS):
        open_ = close
        close = 100.0 + i * 0.5
        volume = BASE_VOLUME
        if i == N_BARS - 2:  # last CLOSED bar: the breakout
            volume = BREAKOUT_VOLUME
        elif i == N_BARS - 1:  # trailing in-progress bar, ~50% elapsed
            volume = PARTIAL_VOLUME
        series.append([base_ts + i * FOUR_H, open_, close + 3.0, open_ - 3.0, close, volume])
    return series


def _stub_registry(monkeypatch, series: list[list]) -> list[MagicMock]:
    """Patch analysis.data._REGISTRY with fakes that all serve ``series``."""
    fakes = []
    for name in ("hyperliquid", "ccxt", "kraken", "yfinance"):
        p = MagicMock()
        p.name = name
        p.supports = MagicMock(return_value=True)
        p.fetch = MagicMock(return_value=[row[:] for row in series])
        fakes.append(p)
    monkeypatch.setattr("analysis.data._REGISTRY", fakes)
    return fakes


def _disable_cache(monkeypatch) -> None:
    """Bypass the disk cache so fetch tests need no network and no state."""
    monkeypatch.setattr("analysis.data.cache_ttl_seconds", lambda: 0)


def _freeze_clock(monkeypatch) -> None:
    """Pin the bar-closure clock seam so trim decisions are deterministic."""
    monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW)


def _load_bt_lib():
    """Load skills/backtest-engine/lib.py dynamically (mirror test_l1_skills)."""
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "backtest-engine", "lib.py")
    spec = importlib.util.spec_from_file_location("backtest_engine_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLastBarIsClosed:
    def test_unknown_interval_raises_value_error(self):
        candles = [[FIXED_NOW, 1, 2, 0.5, 1.5, 100]]
        with pytest.raises(ValueError, match="unknown interval"):
            last_bar_is_closed(candles, "1hr")

    def test_empty_list_is_closed(self):
        assert last_bar_is_closed([], "4h") is True

    def test_trailing_4h_bar_opened_2h_ago_is_forming(self):
        candles = [[FIXED_NOW - 7200, 1, 2, 0.5, 1.5, 100]]
        assert last_bar_is_closed(candles, "4h", now=FIXED_NOW) is False

    def test_trailing_4h_bar_opened_5h_ago_is_closed(self):
        candles = [[FIXED_NOW - 5 * 3600, 1, 2, 0.5, 1.5, 100]]
        assert last_bar_is_closed(candles, "4h", now=FIXED_NOW) is True

    def test_boundary_close_time_equals_now_is_closed(self):
        candles = [[FIXED_NOW - FOUR_H, 1, 2, 0.5, 1.5, 100]]
        assert last_bar_is_closed(candles, "4h", now=FIXED_NOW) is True

    def test_future_dated_timestamp_is_forming(self):
        # Clock skew: a bar whose close time lies in the future is not closed.
        candles = [[FIXED_NOW + 3600, 1, 2, 0.5, 1.5, 100]]
        assert last_bar_is_closed(candles, "4h", now=FIXED_NOW) is False

    def test_1m_uses_conservative_31_day_bound(self):
        candles = [[FIXED_NOW - 20 * 86400, 1, 2, 0.5, 1.5, 100]]
        # 20 days < 31 days: still classified as forming under the bound.
        assert last_bar_is_closed(candles, "1M", now=FIXED_NOW) is False

    def test_now_defaults_to_now_epoch_seam(self, monkeypatch):
        candles = [[FIXED_NOW - 7200, 1, 2, 0.5, 1.5, 100]]
        monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW)
        assert last_bar_is_closed(candles, "4h") is False
        monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW + FOUR_H)
        assert last_bar_is_closed(candles, "4h") is True


class TestClosedBars:
    def test_partial_trailing_4h_bar_dropped(self):
        series = _hype_series()
        trimmed = closed_bars(series, "4h", now=FIXED_NOW)
        assert len(trimmed) == len(series) - 1
        assert trimmed[-1][0] == series[-2][0]

    def test_closed_trailing_4h_bar_kept(self):
        series = _hype_series()[:-1]  # drop the forming bar: all closed now
        assert closed_bars(series, "4h", now=FIXED_NOW) is series

    def test_boundary_close_time_equals_now_kept(self):
        candles = [[FIXED_NOW - FOUR_H, 1, 2, 0.5, 1.5, 100]]
        assert closed_bars(candles, "4h", now=FIXED_NOW) is candles

    def test_fully_historical_series_unchanged_and_identity_preserved(self):
        series = _hype_series()[:-1]
        result = closed_bars(series, "4h", now=FIXED_NOW)
        assert result is series
        assert all(a is b for a, b in zip(result, series))

    def test_callers_input_list_not_mutated(self):
        series = _hype_series()
        snapshot = [row[:] for row in series]
        closed_bars(series, "4h", now=FIXED_NOW)
        assert series == snapshot

    def test_never_drops_more_than_one_bar(self):
        # Two trailing bars with future-dated timestamps (clock skew): only
        # the last one is dropped.
        candles = [
            [FIXED_NOW - 2 * FOUR_H, 1, 2, 0.5, 1.5, 100],
            [FIXED_NOW + FOUR_H, 1, 2, 0.5, 1.5, 100],
            [FIXED_NOW + 2 * FOUR_H, 1, 2, 0.5, 1.5, 100],
        ]
        trimmed = closed_bars(candles, "4h", now=FIXED_NOW)
        assert len(trimmed) == len(candles) - 1
        assert trimmed[-1][0] == FIXED_NOW + FOUR_H

    def test_unknown_interval_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown interval"):
            closed_bars(_hype_series(), "90m")

    def test_empty_list_returns_empty(self):
        assert closed_bars([], "4h") == []

    def test_now_defaults_to_now_epoch_seam(self, monkeypatch):
        series = _hype_series()
        monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW)
        assert len(closed_bars(series, "4h")) == len(series) - 1
        monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW + 2 * FOUR_H)
        assert closed_bars(series, "4h") is series

    def test_interval_seconds_covers_every_supported_interval(self):
        from analysis.intervals import VALID_INTERVALS

        assert set(INTERVAL_SECONDS) == set(VALID_INTERVALS)
        assert all(v > 0 for v in INTERVAL_SECONDS.values())
        assert INTERVAL_SECONDS["1M"] == 31 * 86400


class TestFetchOhlcTrimsAcrossProviders:
    """Provider uniformity: the trim happens once, in the data layer."""

    @pytest.mark.parametrize(
        ("ticker", "source"),
        [
            ("kraken:BTCUSD", None),
            ("hl:HYPE", None),
            ("yf:SPY", None),
            ("BTCUSD", "ccxt"),
        ],
    )
    def test_explicit_routing_trims_partial_bar(self, monkeypatch, ticker, source):
        _freeze_clock(monkeypatch)
        _disable_cache(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)

        trimmed = fetch_ohlc(ticker, interval="4h", period="1y", source=source)
        assert len(trimmed) == len(raw) - 1
        assert trimmed[-1][0] == raw[-2][0]

        untrimmed = fetch_ohlc(ticker, interval="4h", period="1y", source=source, include_partial=True)
        assert untrimmed == raw
        assert len(untrimmed) == len(raw)

    def test_auto_detect_routing_trims_partial_bar(self, monkeypatch):
        _freeze_clock(monkeypatch)
        _disable_cache(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)

        trimmed = fetch_ohlc("BTCUSD", interval="4h", period="1y")
        assert len(trimmed) == len(raw) - 1
        assert trimmed[-1][0] == raw[-2][0]

    def test_cache_hit_path_trims_on_read(self, monkeypatch):
        _freeze_clock(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)
        monkeypatch.setattr("analysis.data.cache_ttl_seconds", lambda: 3600)
        monkeypatch.setattr(
            "analysis.data.get_cached",
            lambda key, ttl: [row[:] for row in raw],
        )
        put_calls: list[list[list]] = []
        monkeypatch.setattr("analysis.data.put_cached", lambda key, candles, ttl: put_calls.append(candles))

        trimmed = fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")
        # Trim is applied on the read of the raw cached payload...
        assert len(trimmed) == len(raw) - 1
        # ...not at write time: the cache would have stored the raw payload.
        assert put_calls == []

    def test_cache_stores_raw_payload_never_trimmed(self, monkeypatch):
        _freeze_clock(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)
        monkeypatch.setattr("analysis.data.cache_ttl_seconds", lambda: 3600)
        monkeypatch.setattr("analysis.data.get_cached", lambda key, ttl: None)
        stored: dict[str, list[list]] = {}
        monkeypatch.setattr("analysis.data.put_cached", lambda key, candles, ttl: stored.setdefault(key, candles))

        trimmed = fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")

        (cached,) = stored.values()
        assert cached == raw  # the raw provider payload, partial bar included
        assert len(trimmed) == len(raw) - 1  # but the caller sees closed bars

    def test_cached_partial_bar_admitted_after_close(self, monkeypatch):
        """A bar that closes while an entry is cached is admitted on next read."""
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)
        monkeypatch.setattr("analysis.data.cache_ttl_seconds", lambda: 3600)
        monkeypatch.setattr("analysis.data.get_cached", lambda key, ttl: [row[:] for row in raw])

        monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW)
        trimmed = fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")
        assert len(trimmed) == len(raw) - 1  # partial bar dropped at read time

        monkeypatch.setattr("analysis.bars.now_epoch", lambda: FIXED_NOW + 2 * FOUR_H)
        admitted = fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")
        assert admitted == raw  # same cache entry, bar now closed -> full series

    def test_cached_list_not_mutated_by_trim(self, monkeypatch):
        _freeze_clock(monkeypatch)
        raw = _hype_series()
        cached_copy = [row[:] for row in raw]
        _stub_registry(monkeypatch, raw)
        monkeypatch.setattr("analysis.data.cache_ttl_seconds", lambda: 3600)
        monkeypatch.setattr("analysis.data.get_cached", lambda key, ttl: cached_copy)

        fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")
        assert cached_copy == raw

    def test_trimmed_series_is_exact_raw_prefix(self, monkeypatch):
        _freeze_clock(monkeypatch)
        _disable_cache(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)

        trimmed = fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")
        assert trimmed == raw[: len(raw) - 1]


class TestVolumeRatioInvariance:
    def _short_series(self) -> list[list]:
        """30-bar 4h series whose final bar is partial and thin."""
        n = 30
        base_ts = FIXED_NOW - 7200 - (n - 1) * FOUR_H
        series = []
        for i in range(n):
            volume = BASE_VOLUME if i < n - 1 else PARTIAL_VOLUME
            series.append([base_ts + i * FOUR_H, 100.0, 101.0, 99.0, 100.5, volume])
        return series

    def test_trimmed_signal_equals_series_without_partial_bar(self, monkeypatch):
        _freeze_clock(monkeypatch)
        _disable_cache(monkeypatch)
        series = self._short_series()
        assert len(series) >= 30
        _stub_registry(monkeypatch, series)

        from analysis.skill_loader import load_skill

        vol_mod = load_skill("market-volume")

        trimmed = fetch_ohlc("kraken:BTCUSD", interval="4h", period="1y")
        assert trimmed == series[:-1]

        via_fetch = vol_mod.analyze(trimmed, interval="4h", period="1y")["volume_ratio"]
        via_slice = vol_mod.analyze(series[:-1], interval="4h", period="1y")["volume_ratio"]
        assert via_fetch == via_slice

        untrimmed = vol_mod.analyze(series, interval="4h", period="1y")["volume_ratio"]
        assert untrimmed < via_fetch  # the partial bar drags the ratio down

    def test_discriminating_magnitudes(self):
        """The fixture must not pass vacuously: 0.37x vs ~1.9x on one bar of diff."""
        from analysis.skill_loader import load_skill

        vol_mod = load_skill("market-volume")
        series = self._short_series()
        with_partial = vol_mod.analyze(series, interval="4h", period="1y")["volume_ratio"]
        without = vol_mod.analyze(series[:-1], interval="4h", period="1y")["volume_ratio"]
        assert with_partial < 0.5
        assert without > 0.9
        assert with_partial < without * 0.6


class TestLiveScanMatchesBacktestWindow:
    def test_fetch_ohlc_equals_walk_forward_prefix_and_idea_identical(self, monkeypatch):
        _freeze_clock(monkeypatch)
        _disable_cache(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)

        bt = _load_bt_lib()
        from analysis.skill_loader import load_skill

        strategy = load_skill("strategy-breakout-confirm")

        live_candles = fetch_ohlc("hl:HYPE", interval="4h", period="1y")

        # Backtest replay window: strict prefix ending at the final closed bar.
        last_closed_index = len(raw) - 2
        backtest_prefix = raw[: last_closed_index + 1]
        assert live_candles == backtest_prefix

        runner = bt.WalkForwardRunner()
        windows = runner.run(
            strategy,
            "hl:HYPE",
            backtest_prefix,
            warmup=len(backtest_prefix) - 1,
            interval="4h",
            period="1y",
            asset_class="crypto",
        )
        assert len(windows) == 1
        window = windows[0]
        assert window["asof_ts"] == live_candles[-1][0]

        live_result = strategy.analyze(
            live_candles,
            ticker="hl:HYPE",
            interval="4h",
            period="1y",
            asset_class="crypto",
        )
        assert live_result["ideas"] != []  # the live scan actually fires
        assert window["idea"] is not None
        assert live_result["ideas"] == [window["idea"]]
        assert (
            live_result["narrative"]
            == strategy.analyze(backtest_prefix, ticker="hl:HYPE", interval="4h", period="1y", asset_class="crypto")[
                "narrative"
            ]
        )


class TestHypeShapedFalseDecline:
    """The measured 08:42 shape: breakout on the last CLOSED bar, thin partial bar."""

    def test_breakout_with_partial_bar_no_longer_declined(self, monkeypatch):
        _freeze_clock(monkeypatch)
        _disable_cache(monkeypatch)
        raw = _hype_series()
        _stub_registry(monkeypatch, raw)

        from analysis.skill_loader import load_skill

        vol_mod = load_skill("market-volume")
        strategy = load_skill("strategy-breakout-confirm")

        trimmed = fetch_ohlc("hl:HYPE", interval="4h", period="1y")

        fixed_ratio = vol_mod.analyze(trimmed, interval="4h", period="1y")["volume_ratio"]
        assert fixed_ratio >= 1.5  # last closed bar ~1.9x SMA20

        result = strategy.analyze(trimmed, ticker="hl:HYPE", interval="4h", period="1y", asset_class="crypto")
        assert len(result["ideas"]) == 1
        assert result["ideas"][0]["direction"] == "long"
        assert "unconfirmed" not in result["ideas"][0]["reasoning"].lower()
        assert result["narrative"] == "Breakout momentum setup: long."

    def test_untrimmed_series_reproduces_the_false_decline(self):
        """The pre-fix computation: the partial bar drags the ratio to ~0.37x."""
        from analysis.skill_loader import load_skill

        vol_mod = load_skill("market-volume")
        strategy = load_skill("strategy-breakout-confirm")

        raw = _hype_series()
        untrimmed_ratio = vol_mod.analyze(raw, interval="4h", period="1y")["volume_ratio"]
        assert untrimmed_ratio <= 1.2

        result = strategy.analyze(raw, ticker="hl:HYPE", interval="4h", period="1y", asset_class="crypto")
        assert result["ideas"] == []
        assert result["narrative"] == ("No confirmed breakout — volume or squeeze confirmation missing.")
