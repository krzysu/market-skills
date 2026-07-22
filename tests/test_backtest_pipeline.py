"""Tests for backtest-pipeline contract validation.

Defines the TypedDicts shapes and validators for all five cross-boundary
output files produced by the nightly backtest pipeline. Every validator
must accept valid data and reject the common malformed shapes that a
future producer or consumer change could introduce.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

from analysis.skill_loader import load_skill

_lib = load_skill("backtest-pipeline")

validate_fitness_matrix = _lib.validate_fitness_matrix
validate_regime_brief = _lib.validate_regime_brief
validate_swing_scan_skip = _lib.validate_swing_scan_skip
validate_watchdog_regime = _lib.validate_watchdog_regime

# ── run.py module loader ──────────────────────────────────────────

_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
    "backtest-pipeline",
)


def _load_run_mod(spec_name: str):
    """Re-import scripts/run.py with a unique spec name to avoid module cache."""
    run_path = os.path.join(_SKILLS_DIR, "scripts", "run.py")
    run_spec = importlib.util.spec_from_file_location(spec_name, run_path)
    run_mod = importlib.util.module_from_spec(run_spec)
    sys.modules[spec_name] = run_mod
    run_spec.loader.exec_module(run_mod)
    return run_mod


# ── fitness_matrix ─────────────────────────────────────────────────


_VALID_FITNESS_MATRIX = {
    "intervals": {
        "1d": {
            "tickers": ["BTCUSD", "ETHUSD"],
            "strategies": ["strategy-trend-follow", "strategy-mean-reversion"],
            "values": [
                [0.5, -0.3],
                [0.2, None],
            ],
        },
    },
    "generated_at": "2026-01-01T00:00:00+00:00",
}


class TestValidateFitnessMatrix:
    def test_valid(self):
        data, err = validate_fitness_matrix(_VALID_FITNESS_MATRIX)
        assert data is not None, err
        assert err is None

    def test_not_a_dict(self):
        data, err = validate_fitness_matrix([])
        assert data is None
        assert "expected a JSON object" in err

    def test_missing_intervals(self):
        data, err = validate_fitness_matrix({"generated_at": "..."})
        assert data is None
        assert "intervals" in err

    def test_intervals_not_dict(self):
        data, err = validate_fitness_matrix({"intervals": [], "generated_at": "..."})
        assert data is None
        assert "intervals" in err

    def test_interval_not_dict(self):
        payload = {
            "intervals": {"1d": []},
            "generated_at": "...",
        }
        data, err = validate_fitness_matrix(payload)
        assert data is None
        assert "1d" in err

    def test_missing_tickers(self):
        payload = {
            "intervals": {"1d": {"strategies": ["s"], "values": [[]]}},
            "generated_at": "...",
        }
        data, err = validate_fitness_matrix(payload)
        assert data is None
        assert "tickers" in err

    def test_values_row_count_mismatch(self):
        payload = {
            "intervals": {"1d": {"tickers": ["A", "B"], "strategies": ["s"], "values": [[0.0]]}},
            "generated_at": "...",
        }
        data, err = validate_fitness_matrix(payload)
        assert data is None
        assert "row count" in err

    def test_values_col_count_mismatch(self):
        payload = {
            "intervals": {"1d": {"tickers": ["A"], "strategies": ["s1", "s2"], "values": [[0.0]]}},
            "generated_at": "...",
        }
        data, err = validate_fitness_matrix(payload)
        assert data is None
        assert "col count" in err

    def test_missing_generated_at(self):
        payload = {"intervals": {"1d": {"tickers": ["A"], "strategies": ["s"], "values": [[0.0]]}}}
        data, err = validate_fitness_matrix(payload)
        assert data is None
        assert "generated_at" in err

    def test_null_cells_allowed(self):
        payload = {
            "intervals": {"1d": {"tickers": ["A"], "strategies": ["s"], "values": [[None]]}},
            "generated_at": "...",
        }
        data, err = validate_fitness_matrix(payload)
        assert data is not None, err


# ── watchdog_regime ────────────────────────────────────────────────


_VALID_WATCHDOG = {
    "positions": {
        "ETH": {
            "trend-follow": {
                "ticker": "ETHEUR",
                "sharpe_now": None,
                "sharpe_7n": None,
                "regime_status": "unknown",
                "recommendation": "monitor",
            },
        },
    },
}


class TestValidateWatchdogRegime:
    def test_valid(self):
        data, err = validate_watchdog_regime(_VALID_WATCHDOG)
        assert data is not None, err

    def test_not_a_dict(self):
        data, err = validate_watchdog_regime([])
        assert data is None
        assert "expected a JSON object" in err

    def test_missing_positions(self):
        data, err = validate_watchdog_regime({})
        assert data is None
        assert "positions" in err

    def test_positions_not_dict(self):
        data, err = validate_watchdog_regime({"positions": []})
        assert data is None
        assert "positions" in err

    def test_strategy_not_dict(self):
        payload = {"positions": {"TICKER": {"strat": []}}}
        data, err = validate_watchdog_regime(payload)
        assert data is None
        assert "must be an object" in err

    def test_invalid_regime_status(self):
        payload = {
            "positions": {
                "TICKER": {
                    "strat": {
                        "ticker": "TICKER",
                        "sharpe_now": None,
                        "sharpe_7n": None,
                        "regime_status": "broken",
                        "recommendation": "...",
                    },
                },
            },
        }
        data, err = validate_watchdog_regime(payload)
        assert data is None
        assert "regime_status" in err

    def test_all_valid_statuses_accepted(self):
        for status in ("positive", "negative", "unknown"):
            payload = {
                "positions": {
                    "T": {
                        "s": {
                            "ticker": "T",
                            "sharpe_now": None,
                            "sharpe_7n": None,
                            "regime_status": status,
                            "recommendation": "...",
                        },
                    },
                },
            }
            data, err = validate_watchdog_regime(payload)
            assert data is not None, f"status={status}: {err}"


# ── swing_scan_skip ────────────────────────────────────────────────


_VALID_SWING_SCAN = {
    "skip_tickers": ["A", "B"],
    "keep_tickers": ["C"],
    "reason": "all strategies have negative Sharpe",
}


class TestValidateSwingScanSkip:
    def test_valid(self):
        data, err = validate_swing_scan_skip(_VALID_SWING_SCAN)
        assert data is not None, err

    def test_not_a_dict(self):
        data, err = validate_swing_scan_skip([])
        assert data is None
        assert "expected a JSON object" in err

    def test_missing_skip_tickers(self):
        data, err = validate_swing_scan_skip({"keep_tickers": [], "reason": "..."})
        assert data is None
        assert "skip_tickers" in err

    def test_missing_keep_tickers(self):
        data, err = validate_swing_scan_skip({"skip_tickers": [], "reason": "..."})
        assert data is None
        assert "keep_tickers" in err

    def test_missing_reason(self):
        data, err = validate_swing_scan_skip({"skip_tickers": [], "keep_tickers": []})
        assert data is None
        assert "reason" in err


# ── regime_brief ───────────────────────────────────────────────────


class TestValidateRegimeBrief:
    def test_valid(self):
        text, err = validate_regime_brief("## Regime Health Report\n\ncontent here")
        assert text is not None, err

    def test_empty(self):
        text, err = validate_regime_brief("")
        assert text is None
        assert "empty" in err

    def test_whitespace_only(self):
        text, err = validate_regime_brief("   \n  ")
        assert text is None
        assert "empty" in err

    def test_missing_h2(self):
        text, err = validate_regime_brief("no heading here\njust text")
        assert text is None
        assert "H2" in err


# ── ticker format: provider:ticker in result dict ─────────────────


class TestRunPairTickerFormat:
    """_run_pair must store provider:ticker (not bare ticker) in the result
    dict so that _write_conviction_thresholds produces keys matching
    lookup_min_conviction's provider:ticker expectation."""

    def test_result_dict_uses_provider_ticker(self, monkeypatch, tmp_path):
        run_mod = _load_run_mod("bp_ticker_fmt")

        envelope = json.dumps(
            {
                "data": {
                    "metrics": {
                        "strategy": {
                            "sharpe": 1.2,
                            "trade_count": 5,
                            "total_return": 0.15,
                            "max_drawdown": -0.08,
                            "profit_factor": 2.1,
                        },
                        "benchmark": {"sharpe": 0.8, "total_return": 0.10},
                    },
                    "bars": 300,
                    "windows": 200,
                }
            }
        )

        class FakeResult:
            returncode = 0
            stdout = envelope

        monkeypatch.setattr(run_mod.subprocess, "run", lambda *a, **kw: FakeResult())
        res = run_mod._run_pair(
            "strategy-trend-follow",
            "BTCUSD",
            "kraken:BTCUSD",
            interval="1d",
        )
        assert res is not None
        assert res["ticker"] == "kraken:BTCUSD"

    def test_conviction_thresholds_use_provider_ticker(self, monkeypatch, tmp_path):
        run_mod = _load_run_mod("bp_conv_thresh_ticker")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        current = {
            "1d\u00d7strategy-trend-follow\u00d7BTCUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:BTCUSD",
                "strategy_sharpe": 1.0,
                "insufficient_data": False,
            },
            "1d\u00d7strategy-trend-follow\u00d7ETHUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:ETHUSD",
                "strategy_sharpe": 0.3,
                "insufficient_data": False,
            },
        }
        state = {"baseline": {}}
        run_mod._write_conviction_thresholds(current, state, out_dir)

        path = out_dir / "conviction_thresholds_private.json"
        data = json.loads(path.read_text())
        strat_table = data["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"]["strategy-trend-follow"]
        assert "kraken:BTCUSD" in strat_table
        assert "kraken:ETHUSD" in strat_table
        assert strat_table["kraken:BTCUSD"]["1d"] == 1
        assert strat_table["kraken:ETHUSD"]["1d"] == 4


# ── shell quoting in _run_pair ────────────────────────────────────


class TestRunPairShellQuoting:
    """_run_pair must shlex.quote all interpolated values so paths with
    spaces and special characters don't break the shell command."""

    def test_repo_path_quoted(self, monkeypatch, tmp_path):
        run_mod = _load_run_mod("bp_shell_quote")
        space_root = tmp_path / "path with spaces" / "repo"
        space_root.mkdir(parents=True)
        monkeypatch.setattr(run_mod, "_REPO_ROOT", space_root)

        captured_cmd = {}

        def fake_run(cmd, **kw):
            captured_cmd["cmd"] = cmd

            class R:
                returncode = 0
                stdout = ""

            return R()

        monkeypatch.setattr(run_mod.subprocess, "run", fake_run)
        run_mod._run_pair("strategy-trend-follow", "BTCUSD", "kraken:BTCUSD", interval="1d")

        cmd_str = captured_cmd["cmd"][2]
        assert "path with spaces" in cmd_str

    def test_ticker_with_special_chars_not_expanded(self, monkeypatch, tmp_path):
        run_mod = _load_run_mod("bp_shell_dollar")
        monkeypatch.setattr(run_mod, "_REPO_ROOT", tmp_path)

        captured_cmd = {}

        def fake_run(cmd, **kw):
            captured_cmd["cmd"] = cmd

            class R:
                returncode = 0
                stdout = ""

            return R()

        monkeypatch.setattr(run_mod.subprocess, "run", fake_run)
        run_mod._run_pair("strategy-trend-follow", "TEST$TICKER", "hl:TEST$TICKER", interval="1d")

        cmd_str = captured_cmd["cmd"][2]
        assert "TEST$TICKER" in cmd_str


# ── error reporting without findings ──────────────────────────────


class TestErrorReporting:
    """On non-first runs, errors must be reported to stdout even when
    _summarize_strategy_decay returns no findings."""

    def test_errors_printed_when_no_findings(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("bp_err_report")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_file.write_text(json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None}))

        monkeypatch.setattr(run_mod, "_resolve_out_dir", lambda: out_dir)
        monkeypatch.setattr(run_mod, "_resolve_state_file", lambda _d: state_file)
        monkeypatch.setattr(run_mod, "_save_state", lambda *a: None)
        monkeypatch.setattr(run_mod, "_update_baseline", lambda *a: None)
        monkeypatch.setattr(run_mod, "_summarize_strategy_decay", lambda *a, **kw: [])
        monkeypatch.setattr(run_mod, "_write_conviction_thresholds", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_fitness_matrix", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_watchdog_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_swing_scan_skip", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_regime_health_brief", lambda *a: None)
        monkeypatch.setattr(run_mod, "PRIMARY_STRATEGIES", ["strategy-trend-follow"])
        monkeypatch.setattr(run_mod, "BACKTEST_INTERVALS", [("1d", "1y", 100, 500)])
        monkeypatch.setattr(
            run_mod,
            "_read_active_tickers",
            lambda: [("BTCUSD", "kraken:BTCUSD"), ("ETHUSD", "kraken:ETHUSD")],
        )

        pair_results = [
            None,
            {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:ETHUSD",
                "strategy_sharpe": 1.0,
                "insufficient_data": False,
                "trades": 3,
                "bars": 300,
                "windows": 200,
                "provider": "kraken",
                "asof": "2026-01-01T00:00:00+00:00",
                "ideas": 1,
                "strategy_total_return": 0.1,
                "strategy_max_dd": -0.05,
                "strategy_profit_factor": 2.0,
                "benchmark_sharpe": 0.5,
                "benchmark_total_return": 0.08,
            },
        ]
        pair_idx = [0]

        def mock_run_pair(*args, **kwargs):
            idx = pair_idx[0]
            pair_idx[0] += 1
            return pair_results[idx] if idx < len(pair_results) else None

        monkeypatch.setattr(run_mod, "_run_pair", mock_run_pair)
        monkeypatch.setattr(run_mod, "_append_run_log", lambda *a: None)

        capsys.readouterr()
        run_mod.main()
        captured = capsys.readouterr()
        assert "pair(s) errored" in captured.out
