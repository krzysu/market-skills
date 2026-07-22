"""backtest-pipeline — nightly backtest pipeline contracts.

Defines the TypedDict shapes and validation functions for every
cross-boundary output file produced by this pipeline. The pipeline is
the sole producer; downstream consumers read these files.

Env vars
--------

  ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR`` (**required**) — base
  directory for all five output files and the rolling baseline state.
  Every file is written to ``<OUT_DIR>/<filename>``.

Consumer-side overrides (all optional; default to ``<OUT_DIR>/<filename>``):

  ``MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH`` — conviction overrides
  ``MARKET_SKILLS_REGIME_STATE_PATH`` — watchdog regime state

Only set a consumer-side override when the file lives at a different
path than ``<OUT_DIR>/<filename>``. The typical config is a single
``OUT_DIR`` env var and the four optional overrides stay unset.
"""

from __future__ import annotations

from typing import TypedDict

# ── Env var constants ──────────────────────────────────────────────

ENV_OUT_DIR = "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR"
ENV_OPEN_POSITIONS_PATH = "MARKET_SKILLS_BACKTEST_PIPELINE_OPEN_POSITIONS_PATH"

ENV_CONVICTION_THRESHOLDS = "MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH"
ENV_REGIME_STATE = "MARKET_SKILLS_REGIME_STATE_PATH"


# ── fitness_matrix.json ────────────────────────────────────────────


class FitnessInterval(TypedDict):
    tickers: list[str]
    strategies: list[str]
    values: list[list[float | None]]


class FitnessMatrix(TypedDict):
    intervals: dict[str, FitnessInterval]
    generated_at: str


def validate_fitness_matrix(data: object) -> tuple[FitnessMatrix | None, str | None]:
    if not isinstance(data, dict):
        return None, "fitness_matrix: expected a JSON object"
    intervals = data.get("intervals")
    if not isinstance(intervals, dict):
        return None, "fitness_matrix: missing or invalid 'intervals' (expected object)"
    for iv_name, iv_data in intervals.items():
        if not isinstance(iv_data, dict):
            return None, f"fitness_matrix: intervals.{iv_name} must be an object"
        tickers = iv_data.get("tickers")
        strategies = iv_data.get("strategies")
        values = iv_data.get("values")
        if not isinstance(tickers, list):
            return None, f"fitness_matrix: intervals.{iv_name}.tickers missing or not a list"
        if not isinstance(strategies, list):
            return None, f"fitness_matrix: intervals.{iv_name}.strategies missing or not a list"
        if not isinstance(values, list):
            return None, f"fitness_matrix: intervals.{iv_name}.values missing or not a list"
        if len(values) != len(tickers):
            return None, (
                f"fitness_matrix: intervals.{iv_name} values row count ({len(values)}) "
                f"!= tickers count ({len(tickers)})"
            )
        for ri, row in enumerate(values):
            if not isinstance(row, list):
                return None, f"fitness_matrix: intervals.{iv_name}.values[{ri}] is not a list"
            if len(row) != len(strategies):
                return None, (
                    f"fitness_matrix: intervals.{iv_name}.values[{ri}] col count ({len(row)}) "
                    f"!= strategies count ({len(strategies)})"
                )
    generated_at = data.get("generated_at")
    if not isinstance(generated_at, str):
        return None, "fitness_matrix: missing or invalid 'generated_at' (expected string)"
    return data, None  # type: ignore[return-value]


# ── watchdog_regime_state.json ─────────────────────────────────────


class StrategyRegime(TypedDict):
    ticker: str
    sharpe_now: float | None
    sharpe_7n: float | None
    regime_status: str
    recommendation: str


class WatchdogRegimeState(TypedDict):
    positions: dict[str, dict[str, StrategyRegime]]


_VALID_REGIME_STATUSES = frozenset({"positive", "negative", "unknown"})


def validate_watchdog_regime(data: object) -> tuple[WatchdogRegimeState | None, str | None]:
    if not isinstance(data, dict):
        return None, "watchdog_regime: expected a JSON object"
    positions = data.get("positions")
    if not isinstance(positions, dict):
        return None, "watchdog_regime: missing or invalid 'positions' (expected object)"
    for ticker, strategies in positions.items():
        if not isinstance(strategies, dict):
            return None, f"watchdog_regime: positions.{ticker} must be an object"
        for strat_name, strat_entry in strategies.items():
            if not isinstance(strat_entry, dict):
                return None, f"watchdog_regime: positions.{ticker}.{strat_name} must be an object"
            status = strat_entry.get("regime_status")
            if not isinstance(status, str) or status not in _VALID_REGIME_STATUSES:
                return None, (
                    f"watchdog_regime: positions.{ticker}.{strat_name}.regime_status "
                    f"must be one of {sorted(_VALID_REGIME_STATUSES)}, got {status!r}"
                )
    return data, None  # type: ignore[return-value]


# ── swing_scan_skip_list.json ──────────────────────────────────────


class SwingScanSkipList(TypedDict):
    skip_tickers: list[str]
    keep_tickers: list[str]
    reason: str


def validate_swing_scan_skip(data: object) -> tuple[SwingScanSkipList | None, str | None]:
    if not isinstance(data, dict):
        return None, "swing_scan_skip: expected a JSON object"
    skip = data.get("skip_tickers")
    keep = data.get("keep_tickers")
    reason = data.get("reason")
    if not isinstance(skip, list):
        return None, "swing_scan_skip: missing or invalid 'skip_tickers' (expected list)"
    if not isinstance(keep, list):
        return None, "swing_scan_skip: missing or invalid 'keep_tickers' (expected list)"
    if not isinstance(reason, str):
        return None, "swing_scan_skip: missing or invalid 'reason' (expected string)"
    return data, None  # type: ignore[return-value]


# ── conviction_thresholds_private.json ─────────────────────────────

# Shape contract is owned by analysis/conviction_thresholds.py (env var,
# TypedDict docstring, _coerce_threshold validator).  This module only
# re-exports the env-var constant for producer-side wiring.


# ── regime_health_brief.md ─────────────────────────────────────────

# Markdown — no TypedDict needed.  Validation is structural: the file
# must be non-empty and start with the expected H2 heading.


def validate_regime_brief(text: str) -> tuple[str | None, str | None]:
    if not text.strip():
        return None, "regime_brief: empty markdown"
    if not text.startswith("## "):
        return None, "regime_brief: missing opening H2 heading"
    return text, None
