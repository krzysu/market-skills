"""Tests for backtest-pipeline contract validation.

Defines the TypedDicts shapes and validators for all six cross-boundary
output files produced by the nightly backtest pipeline. Every validator
must accept valid data and reject the common malformed shapes that a
future producer or consumer change could introduce.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

import pytest

from analysis.skill_loader import load_skill

_lib = load_skill("backtest-pipeline")

validate_fitness_matrix = _lib.validate_fitness_matrix
validate_hold_regime = _lib.validate_hold_regime
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
    "no_trade_tickers": ["D"],
    "keep_tickers": ["C"],
    "reason": "2 ticker(s): all strategies negative Sharpe; 1 ticker(s): no trade signals",
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
        data, err = validate_swing_scan_skip({"keep_tickers": [], "reason": "...", "no_trade_tickers": []})
        assert data is None
        assert "skip_tickers" in err

    def test_missing_keep_tickers(self):
        data, err = validate_swing_scan_skip({"skip_tickers": [], "reason": "...", "no_trade_tickers": []})
        assert data is None
        assert "keep_tickers" in err

    def test_missing_reason(self):
        data, err = validate_swing_scan_skip({"skip_tickers": [], "keep_tickers": [], "no_trade_tickers": []})
        assert data is None
        assert "reason" in err

    def test_missing_no_trade_tickers(self):
        data, err = validate_swing_scan_skip({"skip_tickers": [], "keep_tickers": [], "reason": "..."})
        assert data is None
        assert "no_trade_tickers" in err

    def test_no_trade_tickers_not_a_list(self):
        payload = {"skip_tickers": [], "keep_tickers": [], "no_trade_tickers": "D", "reason": "..."}
        data, err = validate_swing_scan_skip(payload)
        assert data is None
        assert "no_trade_tickers" in err


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
                "trades": 25,
                "insufficient_data": False,
            },
            "1d\u00d7strategy-trend-follow\u00d7ETHUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:ETHUSD",
                "strategy_sharpe": 0.3,
                "trades": 18,
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

    def test_bankrupted_combo_gets_floor_99_and_insufficient_data_skipped(self, tmp_path):
        """Bead market-skills-ww0: a combo whose equity curve went non-positive
        must not have its Sharpe converted into a conviction floor, and must
        not be skipped either — an absent key falls through to
        GLOBAL_MIN_CONVICTION_TO_EMIT=1 ("trade this"), grading a destroyed
        curve (profit factor 0.31, average trade -$3,180) as tradeable. The
        flagged combo gets an explicit non-tradeable floor of 99 instead.
        An `insufficient_data` combo stays skipped (no entry): absent means
        "no opinion" only for the genuinely-unmeasured case. A healthy combo
        in the same run still gets its floor."""
        run_mod = _load_run_mod("bp_conv_thresh_bankrupt")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        current = {
            "1d\u00d7strategy-trend-follow\u00d7BTCUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:BTCUSD",
                "strategy_sharpe": None,  # engine reports sharpe null for a bankrupt curve
                "bankrupted": True,
                "trades": 12,
                "insufficient_data": False,
            },
            "1d\u00d7strategy-trend-follow\u00d7SOLUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:SOLUSD",
                "strategy_sharpe": 0.0,
                "bankrupted": False,
                "trades": 8,
                "insufficient_data": True,
            },
            "1d\u00d7strategy-trend-follow\u00d7ETHUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:ETHUSD",
                "strategy_sharpe": 0.8,
                "bankrupted": False,
                "trades": 15,
                "insufficient_data": False,
            },
        }
        state = {"baseline": {}}
        run_mod._write_conviction_thresholds(current, state, out_dir)

        data = json.loads((out_dir / "conviction_thresholds_private.json").read_text())
        strat_table = data["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"]["strategy-trend-follow"]
        assert strat_table["kraken:BTCUSD"]["1d"] == 99
        assert "kraken:SOLUSD" not in strat_table
        assert strat_table["kraken:ETHUSD"]["1d"] == 1


# ── minimum-trades guard (bead market-skills-2fb) ─────────────────


class TestMinimumTradesGuard:
    """A combo whose trade count is below the minimum-trades threshold must
    not convert its Sharpe into a permissive conviction floor: a handful of
    trades can score a large Sharpe on a statistically meaningless sample
    (5 trades, Sharpe +3.03, floor 1 → surfaced live as a trade idea).

    Same failure class as market-skills-ww0 (fake +1.12 Sharpe from a
    bankrupted curve) reached by a different route: ww0 removed FAKE
    metrics, this admits REAL metrics computed on noise. The remedy is the
    bankruptcy remedy — an explicit non-tradeable floor of 99, never a
    skip (an absent key falls through to GLOBAL_MIN_CONVICTION_TO_EMIT=1),
    plus exclusion from the regime brief's top-N rankings and a
    ``withheld_low_trades`` entry in the run record."""

    @staticmethod
    def _evidence_current():
        """Exact shape from the 2026-09-17 nightly evidence:
        4h × strategy-accumulation-swing × PENDLEUSD — 5 trades, Sharpe
        +3.03, floor 1 — next to a healthy combo above the threshold."""
        return {
            "4h\u00d7strategy-accumulation-swing\u00d7PENDLEUSD": {
                "strategy": "strategy-accumulation-swing",
                "ticker": "kraken:PENDLEUSD",
                "strategy_sharpe": 3.03,
                "trades": 5,
                "insufficient_data": False,
                "bankrupted": False,
            },
            "4h\u00d7strategy-trend-follow\u00d7BTCUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:BTCUSD",
                "strategy_sharpe": 1.0,
                "trades": 30,
                "insufficient_data": False,
                "bankrupted": False,
            },
        }

    def test_five_trade_combo_gets_floor_99_healthy_keeps_floor_1(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_min_trades_floor")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_conviction_thresholds(self._evidence_current(), {"baseline": {}}, out_dir)

        data = json.loads((out_dir / "conviction_thresholds_private.json").read_text())
        table = data["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"]
        assert table["strategy-accumulation-swing"]["kraken:PENDLEUSD"]["4h"] == 99
        assert table["strategy-trend-follow"]["kraken:BTCUSD"]["4h"] == 1

    def test_run_record_lists_withheld_low_trades(self, monkeypatch, tmp_path, capsys):
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_min_trades_record")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_file.write_text(json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None}))

        monkeypatch.setattr(run_mod, "_resolve_out_dir", lambda: out_dir)
        monkeypatch.setattr(run_mod, "_resolve_state_file", lambda _d: state_file)
        monkeypatch.setattr(run_mod, "_parse_args", lambda: argparse.Namespace(baskets=None))
        monkeypatch.setattr(run_mod, "_save_state", lambda *a: None)
        monkeypatch.setattr(run_mod, "_update_baseline", lambda *a: None)
        monkeypatch.setattr(run_mod, "_summarize_strategy_decay", lambda *a, **kw: [])
        monkeypatch.setattr(run_mod, "_write_conviction_thresholds", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_fitness_matrix", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_watchdog_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_swing_scan_skip", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_hold_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_regime_health_brief", lambda *a: None)
        monkeypatch.setattr(run_mod, "measured_strategies", lambda: ["strategy-accumulation-swing"])
        monkeypatch.setattr(run_mod, "BACKTEST_INTERVALS", [("4h", "3mo", 200, 400)])
        monkeypatch.setattr(
            run_mod,
            "_read_active_tickers",
            lambda baskets=None: [("PENDLEUSD", "kraken:PENDLEUSD")],
        )
        monkeypatch.setattr(
            run_mod,
            "_run_pair",
            lambda *a, **kw: {
                "strategy": "strategy-accumulation-swing",
                "ticker": "kraken:PENDLEUSD",
                "strategy_sharpe": 3.03,
                "trades": 5,
                "insufficient_data": False,
                "bankrupted": False,
                "asof": "2026-09-17T00:04:00+00:00",
                "ideas": 2,
                "bars": 400,
                "windows": 300,
                "provider": "kraken",
            },
        )

        captured_record = {}

        def fake_append(record, _out_dir):
            captured_record.update(record)

        monkeypatch.setattr(run_mod, "_append_run_log", fake_append)

        capsys.readouterr()
        run_mod.main()
        captured = capsys.readouterr()

        withheld = captured_record["withheld_low_trades"]
        assert len(withheld) == 1
        entry = withheld[0]
        assert entry["combo"] == "4h\u00d7strategy-accumulation-swing\u00d7PENDLEUSD"
        assert entry["strategy"] == "strategy-accumulation-swing"
        assert entry["ticker"] == "kraken:PENDLEUSD"
        assert entry["trades"] == 5
        assert entry["min_trades"] == _lib.DEFAULT_MIN_TRADES
        assert isinstance(entry["reason"], str) and entry["reason"]
        # run record shape mirrors excluded_strategies' reason pattern
        assert all(isinstance(e["reason"], str) and e["reason"] for e in captured_record["excluded_strategies"])
        assert "withheld" in captured.out

    def test_brief_excludes_withheld_from_rankings(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_min_trades_brief")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_regime_health_brief(self._evidence_current(), {}, out_dir)

        text = (out_dir / "regime_health_brief.md").read_text()
        # the withheld combo must not appear in either top-N ranking table…
        rankings = text.split("### \U0001f7e2 Top 5 by Sharpe")[1]
        assert "PENDLEUSD" not in rankings.split("###")[0]
        bottom = text.split("### \U0001f534 Bottom 5 by Sharpe")[1]
        assert "PENDLEUSD" not in bottom.split("###")[0]
        # …while the healthy combo is still ranked
        assert "BTCUSD" in text.split("### \U0001f7e2 Top 5 by Sharpe")[1].split("###")[0]
        # …and the absence is explained, not silent
        assert "withheld" in text
        assert "insufficient trades" in text

    def test_env_var_moves_withhold_boundary(self, monkeypatch, tmp_path):
        monkeypatch.setenv(_lib.ENV_MIN_TRADES, "20")
        run_mod = _load_run_mod("bp_min_trades_env")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        current = {
            "4h\u00d7strategy-trend-follow\u00d7BTCUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:BTCUSD",
                "strategy_sharpe": 1.5,
                "trades": 15,
                "insufficient_data": False,
            },
        }
        run_mod._write_conviction_thresholds(current, {"baseline": {}}, out_dir)

        data = json.loads((out_dir / "conviction_thresholds_private.json").read_text())
        # 15 trades clears the default 10 but not the configured 20 → withheld
        table = data["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"]["strategy-trend-follow"]
        assert table["kraken:BTCUSD"]["4h"] == 99

        withheld = run_mod._withheld_low_trades(current, run_mod._resolve_min_trades())
        assert withheld[0]["min_trades"] == 20
        assert withheld[0]["trades"] == 15

    def test_zero_disables_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv(_lib.ENV_MIN_TRADES, "0")
        run_mod = _load_run_mod("bp_min_trades_zero")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_conviction_thresholds(self._evidence_current(), {"baseline": {}}, out_dir)

        data = json.loads((out_dir / "conviction_thresholds_private.json").read_text())
        table = data["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"]
        # guard disabled: the 5-trade +3.03 Sharpe combo is back to floor 1
        assert table["strategy-accumulation-swing"]["kraken:PENDLEUSD"]["4h"] == 1
        assert run_mod._withheld_low_trades(self._evidence_current(), run_mod._resolve_min_trades()) == []

    def test_malformed_value_raises_valueerror(self, monkeypatch):
        monkeypatch.setenv(_lib.ENV_MIN_TRADES, "ten")
        run_mod = _load_run_mod("bp_min_trades_malformed")

        try:
            run_mod._resolve_min_trades()
        except ValueError as e:
            assert _lib.ENV_MIN_TRADES in str(e)
            assert "ten" in str(e)
        else:
            raise AssertionError("expected ValueError for non-integer min-trades value")

    def test_negative_value_raises_valueerror(self, monkeypatch):
        monkeypatch.setenv(_lib.ENV_MIN_TRADES, "-3")
        run_mod = _load_run_mod("bp_min_trades_negative")

        try:
            run_mod._resolve_min_trades()
        except ValueError as e:
            assert _lib.ENV_MIN_TRADES in str(e)
            assert "-3" in str(e)
        else:
            raise AssertionError("expected ValueError for negative min-trades value")

    def test_missing_trades_key_treated_as_zero(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_min_trades_no_key")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        current = {
            "1d\u00d7strategy-trend-follow\u00d7BTCUSD": {
                "strategy": "strategy-trend-follow",
                "ticker": "kraken:BTCUSD",
                "strategy_sharpe": 2.0,
                "insufficient_data": False,
            },
        }
        withheld = run_mod._withheld_low_trades(current, run_mod._resolve_min_trades())
        assert len(withheld) == 1
        assert withheld[0]["trades"] == 0


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
        monkeypatch.setattr(run_mod, "_parse_args", lambda: argparse.Namespace(baskets=None))
        monkeypatch.setattr(run_mod, "_save_state", lambda *a: None)
        monkeypatch.setattr(run_mod, "_update_baseline", lambda *a: None)
        monkeypatch.setattr(run_mod, "_summarize_strategy_decay", lambda *a, **kw: [])
        monkeypatch.setattr(run_mod, "_write_conviction_thresholds", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_fitness_matrix", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_watchdog_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_swing_scan_skip", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_hold_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_regime_health_brief", lambda *a: None)
        monkeypatch.setattr(
            run_mod,
            "measured_strategies",
            lambda: ["strategy-trend-follow"],
        )
        monkeypatch.setattr(run_mod, "BACKTEST_INTERVALS", [("1d", "1y", 100, 500)])
        monkeypatch.setattr(
            run_mod,
            "_read_active_tickers",
            lambda baskets=None: [("BTCUSD", "kraken:BTCUSD"), ("ETHUSD", "kraken:ETHUSD")],
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


# ── measured-strategy selection (bead market-skills-gqi) ──────────


class TestMeasuredStrategy:
    """The nightly backtest must measure the whole L3 registry minus the
    explicitly declared UNMEASURABLE_STRATEGIES — never a positional slice.

    Pre-fix, a ``[:3]`` cap on secondary strategies silently dropped
    ``strategy-liquidity-sweep`` (last in registry order), leaving it with
    no fitness data, no conviction floor, and no regime-brief coverage,
    while ``strategy-funding-carry`` burned a secondary slot erroring on
    every combo (no funding data in the backtest)."""

    def test_measured_set_is_registry_minus_declared_unmeasurable(self):
        run_mod = _load_run_mod("bp_measured_real_registry")

        measured = run_mod.measured_strategies()

        from analysis.registry import l3_strategies

        assert measured == [s for s in l3_strategies() if s not in run_mod.UNMEASURABLE_STRATEGIES]
        assert "strategy-liquidity-sweep" in measured
        assert "strategy-funding-carry" not in measured

    def test_added_registry_entry_is_measured_not_displaced(self, monkeypatch):
        """A new registry entry must be measured, not displace an existing
        one. Pre-fix the ``[:3]`` secondary slice dropped the last two
        registry names when an eighth entry was inserted."""
        run_mod = _load_run_mod("bp_measured_new_entry")
        from analysis.registry import l3_strategies

        real = l3_strategies()
        patched = real[:3] + ["strategy-new-thing"] + real[3:]
        monkeypatch.setattr(run_mod, "l3_strategies", lambda: patched)

        measured = run_mod.measured_strategies()

        assert measured == [s for s in patched if s not in run_mod.UNMEASURABLE_STRATEGIES]
        assert "strategy-new-thing" in measured
        assert "strategy-liquidity-sweep" in measured
        assert "strategy-funding-carry" not in measured

    def test_run_record_lists_measured_strategies_and_exclusions(self, monkeypatch, tmp_path, capsys):
        """End-to-end through main(): the run record's ``strategies`` key
        holds the measured set (including liquidity-sweep) and
        ``excluded_strategies`` names funding-carry with a non-empty reason."""
        run_mod = _load_run_mod("bp_measured_run_record")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_file.write_text(json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None}))

        from analysis.registry import l3_strategies

        real = l3_strategies()
        patched = real[:3] + ["strategy-new-thing"] + real[3:]
        monkeypatch.setattr(run_mod, "l3_strategies", lambda: patched)

        monkeypatch.setattr(run_mod, "_resolve_out_dir", lambda: out_dir)
        monkeypatch.setattr(run_mod, "_resolve_state_file", lambda _d: state_file)
        monkeypatch.setattr(run_mod, "_parse_args", lambda: argparse.Namespace(baskets=None))
        monkeypatch.setattr(run_mod, "_save_state", lambda *a: None)
        monkeypatch.setattr(run_mod, "_update_baseline", lambda *a: None)
        monkeypatch.setattr(run_mod, "_summarize_strategy_decay", lambda *a, **kw: [])
        monkeypatch.setattr(run_mod, "_write_conviction_thresholds", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_fitness_matrix", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_watchdog_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_swing_scan_skip", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_hold_regime", lambda *a: None)
        monkeypatch.setattr(run_mod, "_write_regime_health_brief", lambda *a: None)
        monkeypatch.setattr(run_mod, "_read_active_tickers", lambda baskets=None: [("BTCUSD", "kraken:BTCUSD")])
        monkeypatch.setattr(run_mod, "_run_pair", lambda *a, **kw: None)

        captured_record = {}

        def fake_append(record, _out_dir):
            captured_record.update(record)

        monkeypatch.setattr(run_mod, "_append_run_log", fake_append)

        capsys.readouterr()
        run_mod.main()
        captured = capsys.readouterr()

        run_record = captured_record
        assert "strategy-liquidity-sweep" in run_record["strategies"]
        assert "strategy-new-thing" in run_record["strategies"]
        assert "strategy-funding-carry" not in run_record["strategies"]
        assert len(run_record["strategies"]) == len(patched) - 1

        excluded = run_record["excluded_strategies"]
        assert [e["strategy"] for e in excluded] == ["strategy-funding-carry"]
        assert all(isinstance(e["reason"], str) and e["reason"] for e in excluded)

        assert "strategy-funding-carry" in captured.out
        assert "unmeasurable" in captured.out

    def test_no_stale_unmeasurable_entries(self):
        run_mod = _load_run_mod("bp_measured_no_stale")
        from analysis.registry import l3_strategies

        registry = set(l3_strategies())
        for name, reason in run_mod.UNMEASURABLE_STRATEGIES.items():
            assert name in registry, f"{name} is not in the registry"
            assert isinstance(reason, str) and reason.strip(), f"{name} has an empty reason"


# ── fail-loud empty pair grid (bead market-skills-kwu) ────────────


class TestEmptyPairGridFailLoud:
    """A zero-pair night must exit non-zero with a FATAL line on stderr,
    BEFORE the run record is appended, any of the six output files is
    written, or the rolling-baseline state file is mutated.

    Pre-fix (bead market-skills-kwu): an empty watchlist resolved 0
    tickers, the pipeline computed 0 pairs, wrote empty artifacts,
    appended a ``results 0 / errors []`` record, and exited 0 — so the
    cron reported ``ok`` while every downstream consumer saw empty edge
    artifacts. Writers are deliberately NOT patched here: if the guard
    fails to fire, the real writers append ``runs.jsonl`` and write the
    six output files into ``out_dir``, and the no-artifacts assertion
    fails."""

    _OUTPUT_FILES = [
        "conviction_thresholds_private.json",
        "fitness_matrix.json",
        "watchdog_regime_state.json",
        "swing_scan_skip_list.json",
        "hold_regime.json",
        "regime_health_brief.md",
        "runs.jsonl",
    ]

    @staticmethod
    def _setup(monkeypatch, run_mod, out_dir, state_file):
        monkeypatch.setattr(run_mod, "_resolve_out_dir", lambda: out_dir)
        monkeypatch.setattr(run_mod, "_resolve_state_file", lambda _d: state_file)
        monkeypatch.setattr(run_mod, "_parse_args", lambda: argparse.Namespace(baskets=None))
        monkeypatch.setattr(run_mod, "BACKTEST_INTERVALS", [("1d", "1y", 100, 500)])

    def test_empty_ticker_list_fatal(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("bp_empty_tickers")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_before = json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None})
        state_file.write_text(state_before)

        self._setup(monkeypatch, run_mod, out_dir, state_file)
        monkeypatch.setattr(run_mod, "measured_strategies", lambda: ["strategy-trend-follow"])
        monkeypatch.setattr(run_mod, "_read_active_tickers", lambda baskets=None: [])

        rc = run_mod.main()
        captured = capsys.readouterr()

        assert rc != 0
        assert "FATAL" in captured.err
        assert "MARKET_SKILLS_WATCHLIST_PATH" in captured.err
        assert "0" in captured.err
        # Nothing written: no output file, no run record, no state mutation.
        leftovers = [p.name for p in out_dir.iterdir() if p.name != "backtest-pipeline-state.json"]
        assert leftovers == []
        assert state_file.read_text() == state_before

    def test_watchlist_error_causes_fatal_not_traceback(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("bp_watchlist_fatal")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_before = json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None})
        state_file.write_text(state_before)

        self._setup(monkeypatch, run_mod, out_dir, state_file)
        monkeypatch.setattr(run_mod, "measured_strategies", lambda: ["strategy-trend-follow"])

        def boom(baskets=None):
            raise run_mod.WatchlistUnavailableError("watchlist unavailable (file not found): /tmp/nowhere.json")

        monkeypatch.setattr(run_mod, "_read_active_tickers", boom)

        rc = run_mod.main()
        captured = capsys.readouterr()

        assert rc != 0
        assert "FATAL" in captured.err
        assert "watchlist unavailable" in captured.err
        # No traceback escaped into stderr.
        assert "Traceback" not in captured.err
        leftovers = [p.name for p in out_dir.iterdir() if p.name != "backtest-pipeline-state.json"]
        assert leftovers == []
        assert state_file.read_text() == state_before

    def test_empty_measured_strategies_fatal(self, monkeypatch, tmp_path, capsys):
        run_mod = _load_run_mod("bp_empty_strategies")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_before = json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None})
        state_file.write_text(state_before)

        self._setup(monkeypatch, run_mod, out_dir, state_file)
        monkeypatch.setattr(run_mod, "measured_strategies", lambda: [])
        monkeypatch.setattr(run_mod, "_read_active_tickers", lambda baskets=None: [("BTCUSD", "kraken:BTCUSD")])

        rc = run_mod.main()
        captured = capsys.readouterr()

        assert rc != 0
        assert "FATAL" in captured.err
        leftovers = [p.name for p in out_dir.iterdir() if p.name != "backtest-pipeline-state.json"]
        assert leftovers == []
        assert state_file.read_text() == state_before


# ── conviction-gate self-pollution isolation (bead market-skills-0rk) ──


def _load_fresh_ct(spec_name: str):
    """Execute analysis/signals/conviction_thresholds.py as a fresh module
    object (unique spec name) so the import-time ``_load_overrides()`` runs
    against the current ``os.environ`` without disturbing the canonical
    ``ct`` module — mirroring how the engine child process starts."""
    ct_origin = importlib.util.find_spec("analysis.signals.conviction_thresholds").origin
    spec = importlib.util.spec_from_file_location(spec_name, ct_origin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestEngineChildEnv:
    """``_engine_child_env`` must strip both leak paths and set the gate marker."""

    def test_leak_vars_removed_gate_marker_set(self, monkeypatch):
        run_mod = _load_run_mod("bp_child_env")
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", "/tmp/leaky/thresholds.json")
        monkeypatch.setenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", "/tmp/leaky/pipeline-out")

        child_env = run_mod._engine_child_env()

        assert "MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH" not in child_env
        assert "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR" not in child_env
        assert child_env["MARKET_SKILLS_CONVICTION_GATE"] == "off"

    def test_preserves_unrelated_keys(self, monkeypatch):
        run_mod = _load_run_mod("bp_child_env_keep")
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", "/tmp/leaky/thresholds.json")
        monkeypatch.setenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", "/tmp/leaky/pipeline-out")

        child_env = run_mod._engine_child_env()

        # env vars unrelated to the leak must survive (PATH is the sentinel).
        assert child_env["PATH"] == os.environ["PATH"]
        assert child_env["HOME"] == os.environ["HOME"]

    def test_unset_leak_vars_still_marks_gate(self, monkeypatch):
        run_mod = _load_run_mod("bp_child_env_unset")
        monkeypatch.delenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", raising=False)
        monkeypatch.delenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", raising=False)

        child_env = run_mod._engine_child_env()

        assert "MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH" not in child_env
        assert "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR" not in child_env
        assert child_env["MARKET_SKILLS_CONVICTION_GATE"] == "off"


class TestRunPairPassesSanitisedEnv:
    """``_run_pair`` must pass the sanitised env to ``subprocess.run`` so the
    engine child can never see the thresholds file it is about to overwrite."""

    def test_captured_env_lacks_leak_vars(self, monkeypatch):
        run_mod = _load_run_mod("bp_run_pair_env")
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH", "/tmp/leaky/thresholds.json")
        monkeypatch.setenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", "/tmp/leaky/pipeline-out")

        captured_env = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})

            class R:
                returncode = 1  # engine "failure" → _run_pair returns None; env already captured
                stdout = ""

            return R()

        monkeypatch.setattr(run_mod.subprocess, "run", fake_run)
        res = run_mod._run_pair("strategy-trend-follow", "BTCUSD", "kraken:BTCUSD", interval="1d")
        assert res is None
        assert "MARKET_SKILLS_CONVICTION_THRESHOLDS_PATH" not in captured_env
        assert "MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR" not in captured_env
        assert captured_env["MARKET_SKILLS_CONVICTION_GATE"] == "off"
        assert captured_env["PATH"] == os.environ["PATH"]


class TestGateLockInIsolation:
    """Regression for the nightly lock-in loop (bead market-skills-0rk).

    Pre-fix failure: with a floor-99 ``conviction_thresholds_private.json``
    in ``MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR``, the engine child
    inherited the var (``subprocess.run`` had no ``env=``) and the gate
    module loaded the floor at import — ``lookup_min_conviction`` returned
    99 and every idea was dropped. Post-fix the sanitised child env (both
    vars popped + ``MARKET_SKILLS_CONVICTION_GATE=off``) makes a fresh
    engine import resolve the shipped default floor 1 with an empty table.
    """

    @staticmethod
    def _write_thresholds(out_dir, table: dict) -> None:
        out_dir.joinpath("conviction_thresholds_private.json").write_text(
            json.dumps(
                {
                    "GLOBAL_MIN_CONVICTION_TO_EMIT": 1,
                    "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": table,
                }
            )
        )

    def test_floor99_file_inert_with_gate_off(self, monkeypatch, tmp_path):
        self._write_thresholds(tmp_path, {"strategy-trend-follow": {"kraken:BTCUSD": {"1d": 99, "4h": 99}}})
        monkeypatch.setenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", str(tmp_path))
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_GATE", "off")

        fresh = _load_fresh_ct("bp_ct_floor99")

        assert fresh.MIN_CONVICTION_TO_EMIT_BY_STRATEGY == {}
        assert fresh.lookup_min_conviction("strategy-trend-follow", "kraken:BTCUSD", "1d") == 1
        assert fresh.lookup_min_conviction("strategy-trend-follow", "kraken:BTCUSD", "4h") == 1

    def test_floor_1_4_table_also_inert_no_gate_applied(self, monkeypatch, tmp_path):
        """Even a benign floor-1/4 overrides file must not reach the engine
        child once the gate is off — the backtest measures raw strategy
        behaviour, unmodulated by any conviction floor."""
        self._write_thresholds(
            tmp_path,
            {"strategy-trend-follow": {"kraken:PENDLE": {"4h": 1}, "hl:SOMETHING": {"1d": 4}}},
        )
        monkeypatch.setenv("MARKET_SKILLS_BACKTEST_PIPELINE_OUT_DIR", str(tmp_path))
        monkeypatch.setenv("MARKET_SKILLS_CONVICTION_GATE", "off")

        fresh = _load_fresh_ct("bp_ct_floor14")

        assert fresh.MIN_CONVICTION_TO_EMIT_BY_STRATEGY == {}
        assert fresh.lookup_min_conviction("strategy-trend-follow", "kraken:PENDLE", "4h") == 1
        assert fresh.lookup_min_conviction("strategy-trend-follow", "hl:SOMETHING", "1d") == 1


class TestWriteSwingScanSkipPartition:
    """Zero-trade tickers must land in ``no_trade_tickers``, not be folded
    into the negative-Sharpe skip bucket with a factually wrong reason."""

    @staticmethod
    def _combo(strategy, ticker, sharpe, trades, insufficient=False):
        return {
            "strategy": strategy,
            "ticker": ticker,
            "strategy_sharpe": sharpe,
            "trades": trades,
            "insufficient_data": insufficient,
        }

    def test_partition_buckets_and_reason(self, tmp_path):
        run_mod = _load_run_mod("bp_skip_partition")
        current = {
            "1d\u00d7strategy-a\u00d7NEG": self._combo("strategy-a", "kraken:NEG", -0.5, 3),
            "4h\u00d7strategy-a\u00d7NEG": self._combo("strategy-a", "kraken:NEG", -1.0, 2),
            "1d\u00d7strategy-a\u00d7BLIND": self._combo("strategy-a", "kraken:BLIND", 0.0, 0),
            "4h\u00d7strategy-a\u00d7BLIND": self._combo("strategy-a", "kraken:BLIND", 0.0, 0),
            "1d\u00d7strategy-a\u00d7GOOD": self._combo("strategy-a", "kraken:GOOD", 1.0, 5),
            "1d\u00d7strategy-a\u00d7MIXED": self._combo("strategy-a", "kraken:MIXED", -0.3, 2),
            "4h\u00d7strategy-a\u00d7MIXED": self._combo("strategy-a", "kraken:MIXED", -0.8, 1),
            # insufficient-data entries must be excluded from the partition.
            "1d\u00d7strategy-a\u00d7SHORT": self._combo("strategy-a", "kraken:SHORT", 0.0, 0, insufficient=True),
        }
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_swing_scan_skip(current, {}, out_dir)

        payload = json.loads((out_dir / "swing_scan_skip_list.json").read_text())
        assert payload["skip_tickers"] == ["kraken:MIXED", "kraken:NEG"]
        assert payload["no_trade_tickers"] == ["kraken:BLIND"]
        assert payload["keep_tickers"] == ["kraken:GOOD"]
        assert payload["reason"] == (
            "2 ticker(s): all strategies negative Sharpe; "
            "1 ticker(s): no trade signals generated on any strategy/interval"
        )

    def test_blind_tickers_not_in_skip_tickers(self, tmp_path):
        """The BTCUSD/ETHUSD bug shape: all-zero-trade, all-0.0-Sharpe tickers
        must NOT be listed as skip_tickers (the old ``all(s <= 0 ...)`` test
        put them there with a 'negative Sharpe' reason)."""
        run_mod = _load_run_mod("bp_skip_blind_only")
        current = {
            "1d\u00d7strategy-a\u00d7BTCUSD": self._combo("strategy-a", "kraken:BTCUSD", 0.0, 0),
            "4h\u00d7strategy-a\u00d7BTCUSD": self._combo("strategy-a", "kraken:BTCUSD", 0.0, 0),
        }
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_swing_scan_skip(current, {}, out_dir)

        payload = json.loads((out_dir / "swing_scan_skip_list.json").read_text())
        assert payload["skip_tickers"] == []
        assert payload["no_trade_tickers"] == ["kraken:BTCUSD"]
        assert payload["keep_tickers"] == []
        assert payload["reason"] == "1 ticker(s): no trade signals generated on any strategy/interval"

    def test_empty_current_reason(self, tmp_path):
        run_mod = _load_run_mod("bp_skip_empty")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_swing_scan_skip({}, {}, out_dir)

        payload = json.loads((out_dir / "swing_scan_skip_list.json").read_text())
        assert payload["skip_tickers"] == []
        assert payload["no_trade_tickers"] == []
        assert payload["keep_tickers"] == []
        assert payload["reason"] == "no tickers skipped"


class TestRegimeHealthBriefPartition:
    """The brief must use the same three-way partition: negative tickers under
    the skipped heading, zero-trade tickers on their own no-trade heading."""

    def test_brief_lists_both_buckets(self, tmp_path, capsys):
        run_mod = _load_run_mod("bp_brief_partition")
        current = {
            "1d\u00d7strategy-a\u00d7NEG": {
                "strategy": "strategy-a",
                "ticker": "kraken:NEG",
                "strategy_sharpe": -0.5,
                "trades": 3,
                "insufficient_data": False,
            },
            "1d\u00d7strategy-a\u00d7BLIND": {
                "strategy": "strategy-a",
                "ticker": "kraken:BLIND",
                "strategy_sharpe": 0.0,
                "trades": 0,
                "insufficient_data": False,
            },
        }
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        run_mod._write_regime_health_brief(current, {}, out_dir)

        text = (out_dir / "regime_health_brief.md").read_text()
        assert text.startswith("## ")
        assert "1 tickers skipped (all strategies negative)" in text
        assert "kraken:NEG" in text
        assert "1 tickers produce no trade signals" in text
        assert "kraken:BLIND" in text


# ── partial-degradation guard (bead market-skills-ofs) ----------


_DEGRADED_CURRENT = {
    "1d\u00d7strategy-a\u00d7kraken:BTCUSD": {
        "strategy": "strategy-a",
        "ticker": "kraken:BTCUSD",
        "strategy_sharpe": None,
        "trades": 0,
        "insufficient_data": True,
    },
}


def _seeded_artifact(name: str) -> str:
    """Realistic populated content for each artifact file name."""
    if name == "conviction_thresholds_private.json":
        return json.dumps(
            {
                "GLOBAL_MIN_CONVICTION_TO_EMIT": 1,
                "MIN_CONVICTION_TO_EMIT_BY_STRATEGY": {"strategy-a": {"kraken:BTCUSD": {"1d": 1}}},
            },
            indent=2,
        )
    if name == "fitness_matrix.json":
        return json.dumps(_VALID_FITNESS_MATRIX, indent=2)
    if name == "swing_scan_skip_list.json":
        return json.dumps(_VALID_SWING_SCAN, indent=2)
    if name == "regime_health_brief.md":
        return (
            "\n".join(
                [
                    "## \U0001f52c Backtest Regime Health (nightly)",
                    "",
                    "### Strategy Health (avg Sharpe across all tickers)",
                    "| Strategy | Avg Sharpe | # Tickers | Verdict |",
                    "|----------|-----------|-----------|---------|",
                    "| strategy-a | +0.50 | 1 | \U0001f7e2 healthy |",
                    "",
                    "### \U0001f7e2 Top 5 by Sharpe",
                    "| Pair | Sharpe | Trades |",
                    "|------|--------|--------|",
                    "| 1d\u00d7strategy-a\u00d7kraken:BTCUSD | +1.00 | 5 |",
                    "",
                    "### \U0001f534 Bottom 5 by Sharpe",
                    "",
                ]
            )
            + "\n"
        )
    raise ValueError(f"unknown artifact name: {name}")


# artifact file name -> the module-level writer that produces it
_GRID_WRITERS = {
    "conviction_thresholds_private.json": "_write_conviction_thresholds",
    "fitness_matrix.json": "_write_fitness_matrix",
    "swing_scan_skip_list.json": "_write_swing_scan_skip",
    "regime_health_brief.md": "_write_regime_health_brief",
}


class TestPartialDegradationGuard:
    """A non-empty pair grid whose entries all degrade to insufficient_data
    must never replace a non-empty on-disk artifact with an empty payload.

    Pre-fix (bead market-skills-ofs): each grid-derived writer did an
    unconditional ``path.write_text(...)``, so a partially-degraded run
    still destroyed the previous good file. Tests drive the real writer
    functions (not the guard helpers) so a pre-fix regression surfaces as
    the actual overwrite."""

    @staticmethod
    def _call_writer(run_mod, name, current, out_dir):
        if name == "conviction_thresholds_private.json":
            run_mod._write_conviction_thresholds(current, {"baseline": {}}, out_dir)
        elif name == "fitness_matrix.json":
            run_mod._write_fitness_matrix(current, {"baseline": {}}, out_dir)
        elif name == "swing_scan_skip_list.json":
            run_mod._write_swing_scan_skip(current, {"baseline": {}}, out_dir)
        elif name == "regime_health_brief.md":
            run_mod._write_regime_health_brief(current, {"baseline": {}}, out_dir)
        else:
            raise ValueError(f"unknown artifact name: {name}")

    @pytest.mark.parametrize("artifact_name", sorted(_GRID_WRITERS))
    def test_degraded_payload_keeps_previous_file(self, monkeypatch, tmp_path, capsys, artifact_name):
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_degraded_keeps")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        seeded = _seeded_artifact(artifact_name)
        path = out_dir / artifact_name
        path.write_text(seeded)

        capsys.readouterr()
        self._call_writer(run_mod, artifact_name, _DEGRADED_CURRENT, out_dir)
        captured = capsys.readouterr()

        assert path.read_text() == seeded
        assert "[WARN]" in captured.out
        assert artifact_name in captured.out
        assert "empty" in captured.out
        assert "written" not in captured.out

    @pytest.mark.parametrize("artifact_name", sorted(_GRID_WRITERS))
    def test_degraded_payload_with_no_previous_file_is_written(self, monkeypatch, tmp_path, capsys, artifact_name):
        """Only what is actually on disk is preserved: with no previous file,
        the empty payload IS written and no [WARN] is printed."""
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_degraded_fresh")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        capsys.readouterr()
        self._call_writer(run_mod, artifact_name, _DEGRADED_CURRENT, out_dir)
        captured = capsys.readouterr()

        assert (out_dir / artifact_name).exists()
        assert "[WARN]" not in captured.out
        assert "written" in captured.out

    def test_legitimately_empty_skip_list_is_written(self, monkeypatch, tmp_path, capsys):
        """An empty skip list with populated keep_tickers is the legitimate
        steady state — the emptiness predicate must NOT treat it as degraded,
        so the file is replaced and no [WARN] is printed."""
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_skip_legit_empty")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        seeded = _seeded_artifact("swing_scan_skip_list.json")
        path = out_dir / "swing_scan_skip_list.json"
        path.write_text(seeded)

        current = {
            "1d\u00d7strategy-a\u00d7kraken:BTCUSD": {
                "strategy": "strategy-a",
                "ticker": "kraken:BTCUSD",
                "strategy_sharpe": 1.0,
                "trades": 5,
                "insufficient_data": False,
            },
        }

        capsys.readouterr()
        run_mod._write_swing_scan_skip(current, {"baseline": {}}, out_dir)
        captured = capsys.readouterr()

        payload = json.loads(path.read_text())
        assert payload["skip_tickers"] == []
        assert payload["no_trade_tickers"] == []
        assert payload["keep_tickers"] == ["kraken:BTCUSD"]
        assert "[WARN]" not in captured.out

    def test_empty_pair_grid_preserves_seeded_artifacts(self, monkeypatch, tmp_path, capsys):
        """The kwu empty-grid path, strengthened: a pre-existing populated set
        of artifacts must survive byte-identical when the grid resolves empty,
        and no runs.jsonl may be appended."""
        run_mod = _load_run_mod("bp_empty_grid_preserves")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        state_file = out_dir / "backtest-pipeline-state.json"
        state_before = json.dumps({"first_run": False, "baseline": {}, "last_run_ts": None})
        state_file.write_text(state_before)

        artifact_names = [*sorted(_GRID_WRITERS), "watchdog_regime_state.json"]
        seeded_bytes = {}
        for name in artifact_names:
            content = _seeded_artifact(name) if name in _GRID_WRITERS else json.dumps(_VALID_WATCHDOG, indent=2)
            (out_dir / name).write_text(content)
            seeded_bytes[name] = content

        TestEmptyPairGridFailLoud._setup(monkeypatch, run_mod, out_dir, state_file)
        monkeypatch.setattr(run_mod, "measured_strategies", lambda: ["strategy-trend-follow"])
        monkeypatch.setattr(run_mod, "_read_active_tickers", lambda baskets=None: [])

        rc = run_mod.main()
        captured = capsys.readouterr()

        assert rc != 0
        assert "FATAL" in captured.err
        for name in artifact_names:
            assert (out_dir / name).read_text() == seeded_bytes[name], name
        assert not (out_dir / "runs.jsonl").exists()
        assert state_file.read_text() == state_before


# ── watchdog regime key resolution (bead market-skills-jqx) ───────


class TestWatchdogRegimeKeyResolution:
    """_write_watchdog_regime must resolve each watch's monitor_provider onto
    the watchlist notation the measurement state is keyed by, and WARN when
    it cannot (bead market-skills-jqx).

    Pre-fix the lookup ticker came from `provider.split(":")[-1]`, which
    misses in two distinct ways: a quote mismatch (watch `kraken:ETHEUR` vs
    state key `ETHUSD`) and a dropped provider prefix (watch `hl:LIT` vs
    state key `hl:LIT`). Both lookups failed silently, so every entry was
    written with null Sharpes and regime_status "unknown" — the watchdog's
    regime-negative alert suppression never fired, for any watch, ever.
    """

    @staticmethod
    def _watch(name: str, monitor_provider: str, strategies: list[str]) -> dict:
        return {
            "name": name,
            "enabled": True,
            "monitor_provider": monitor_provider,
            "signals": [{"strategies": strategies}],
        }

    @staticmethod
    def _measured(strategy: str, provider_ticker: str, sharpe: float) -> dict:
        return {
            "strategy": strategy,
            "ticker": provider_ticker,
            "strategy_sharpe": sharpe,
            "trades": 12,
            "insufficient_data": False,
        }

    @staticmethod
    def _baseline_slot(avg: float) -> dict:
        return {"history": [], "avg_sharpe_7n": avg, "n_samples": 1}

    @staticmethod
    def _write_regime(run_mod, monkeypatch, tmp_path, watches, current, baseline) -> dict:
        """Drive the REAL _write_watchdog_regime against tmp fixtures and
        return the parsed watchdog_regime_state.json payload."""
        positions_path = tmp_path / "open-positions.json"
        positions_path.write_text(json.dumps({"watches": watches}))
        monkeypatch.setenv(_lib.ENV_OPEN_POSITIONS_PATH, str(positions_path))
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        run_mod._write_watchdog_regime(current, {"baseline": baseline}, out_dir)
        return json.loads((out_dir / "watchdog_regime_state.json").read_text())

    def test_regime_resolves_eur_quoted_monitor_provider(self, monkeypatch, tmp_path):
        """Watch `kraken:ETHEUR` must resolve onto the state's `ETHUSD` key
        through the watchlist's alias/quote resolution. Pre-fix the lookup
        used the bare tail `ETHEUR` — a key the state never contains — so
        both Sharpes stayed null and the status stayed "unknown"."""
        run_mod = _load_run_mod("bp_regime_eur_quote")
        data = self._write_regime(
            run_mod,
            monkeypatch,
            tmp_path,
            [self._watch("ETH", "kraken:ETHEUR", ["trend-follow"])],
            {
                "1d\u00d7strategy-trend-follow\u00d7ETHUSD": self._measured(
                    "strategy-trend-follow", "kraken:ETHUSD", 1.2
                )
            },
            {"1d\u00d7strategy-trend-follow\u00d7ETHUSD": self._baseline_slot(0.6)},
        )
        entry = data["positions"]["ETH"]["trend-follow"]
        assert entry["sharpe_now"] == 1.2
        assert entry["sharpe_7n"] == 0.6
        assert entry["regime_status"] != "unknown"
        assert entry["ticker"] == "ETHUSD"

    def test_regime_resolves_prefixed_provider(self, monkeypatch, tmp_path):
        """Watch `hl:LIT` must resolve onto the state's raw watchlist key
        `hl:LIT`. Pre-fix the lookup used the bare tail `LIT` — a key the
        state never contains — so the entry stayed null/unknown."""
        run_mod = _load_run_mod("bp_regime_prefixed")
        watchlist_path = tmp_path / "watchlist.json"
        watchlist_path.write_text(
            json.dumps(
                {
                    "baskets": {
                        "smoke": {
                            "hl:LIT": {"source": "hyperliquid", "tier": 2},
                        }
                    }
                }
            )
        )
        monkeypatch.setenv("MARKET_SKILLS_WATCHLIST_PATH", str(watchlist_path))
        data = self._write_regime(
            run_mod,
            monkeypatch,
            tmp_path,
            [self._watch("LIT", "hl:LIT", ["trend-follow"])],
            {"1d\u00d7strategy-trend-follow\u00d7hl:LIT": self._measured("strategy-trend-follow", "hl:LIT", 0.9)},
            {"1d\u00d7strategy-trend-follow\u00d7hl:LIT": self._baseline_slot(0.4)},
        )
        entry = data["positions"]["LIT"]["trend-follow"]
        assert entry["sharpe_now"] == 0.9
        assert entry["sharpe_7n"] == 0.4
        assert entry["regime_status"] != "unknown"
        assert entry["ticker"] == "hl:LIT"

    def test_regime_unknown_is_warned_not_silent(self, monkeypatch, tmp_path, capsys):
        """A watch whose monitor_provider matches nothing in the watchlist is
        a configuration bug: the entry is still written as "unknown" (the
        reader tolerates it) but the miss must be LOUD — a [WARN] naming the
        watch, the strategy, the monitor_provider and the candidates tried."""
        run_mod = _load_run_mod("bp_regime_warn")
        watches = [self._watch("NOPE", "kraken:NOPEUSD", ["trend-follow"])]

        capsys.readouterr()
        data = self._write_regime(run_mod, monkeypatch, tmp_path, watches, {}, {})
        captured = capsys.readouterr()

        entry = data["positions"]["NOPE"]["trend-follow"]
        assert entry["regime_status"] == "unknown"
        assert entry["sharpe_now"] is None
        assert entry["ticker"] == "kraken:NOPEUSD"

        warns = [ln for ln in captured.out.splitlines() if "[WARN]" in ln and "watchdog regime" in ln]
        assert len(warns) == 1
        assert "'NOPE'" in warns[0]
        assert "'trend-follow'" in warns[0]
        assert "'kraken:NOPEUSD'" in warns[0]
        assert "nopeusd" in warns[0]

    def test_regime_status_derives_from_baseline_sign(self, monkeypatch, tmp_path):
        """A negative 7-night baseline Sharpe must surface as regime_status
        "negative" with a skip-adds recommendation; a positive one as
        "positive" — reached through the resolved key, not the bare tail."""
        run_mod = _load_run_mod("bp_regime_baseline_sign")
        watches = [
            self._watch("ETH", "kraken:ETHEUR", ["trend-follow"]),
            self._watch("BTC", "kraken:BTCUSD", ["trend-follow"]),
        ]
        current = {
            "1d\u00d7strategy-trend-follow\u00d7ETHUSD": self._measured("strategy-trend-follow", "kraken:ETHUSD", -0.2),
            "1d\u00d7strategy-trend-follow\u00d7BTCUSD": self._measured("strategy-trend-follow", "kraken:BTCUSD", 1.1),
        }
        baseline = {
            "1d\u00d7strategy-trend-follow\u00d7ETHUSD": self._baseline_slot(-0.7),
            "1d\u00d7strategy-trend-follow\u00d7BTCUSD": self._baseline_slot(0.5),
        }
        data = self._write_regime(run_mod, monkeypatch, tmp_path, watches, current, baseline)

        neg = data["positions"]["ETH"]["trend-follow"]
        assert neg["regime_status"] == "negative"
        assert "skip adds" in neg["recommendation"]
        assert neg["sharpe_now"] == -0.2
        assert neg["ticker"] == "ETHUSD"

        pos = data["positions"]["BTC"]["trend-follow"]
        assert pos["regime_status"] == "positive"
        assert pos["sharpe_7n"] == 0.5
        assert pos["ticker"] == "BTCUSD"


# ── hold_regime.json (bead market-skills-xtp) ──────────────────────


_VALID_HOLD_REGIME = {
    "generated_at": "2026-01-01T00:00:00+00:00",
    "hold_regime": [
        {
            "ticker": "kraken:AAAUSD",
            "interval": "1d",
            "benchmark_sharpe": 1.177,
            "benchmark_total_return": 0.67,
            "strategies_measured": 3,
            "min_gap": 1.0,
            "max_gap": 2.5,
            "trades_total": 42,
        }
    ],
}


def _hold_flag(**overrides):
    """A valid HoldRegimeFlag payload, per-case field overrides."""
    flag = {
        "ticker": "kraken:AAAUSD",
        "interval": "1d",
        "benchmark_sharpe": 1.177,
        "benchmark_total_return": 0.67,
        "strategies_measured": 3,
        "min_gap": 1.0,
        "max_gap": 2.5,
        "trades_total": 42,
    }
    flag.update(overrides)
    return flag


def _hold_payload(flag_overrides=None, **payload_overrides):
    """A valid hold_regime.json payload, per-case overrides."""
    payload = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "hold_regime": [_hold_flag()],
    }
    if flag_overrides is not None:
        payload["hold_regime"] = [_hold_flag(**f) for f in flag_overrides]
    payload.update(payload_overrides)
    return payload


class TestValidateHoldRegime:
    def test_valid(self):
        data, err = validate_hold_regime(_VALID_HOLD_REGIME)
        assert data is not None, err
        assert err is None

    def test_valid_empty_list_is_steady_state(self):
        data, err = validate_hold_regime({"generated_at": "...", "hold_regime": []})
        assert data is not None, err
        assert err is None

    def test_not_a_dict(self):
        data, err = validate_hold_regime([])
        assert data is None
        assert "expected a JSON object" in err

    def test_missing_hold_regime(self):
        data, err = validate_hold_regime({"generated_at": "..."})
        assert data is None
        assert "hold_regime" in err

    def test_hold_regime_not_a_list(self):
        data, err = validate_hold_regime({"generated_at": "...", "hold_regime": {}})
        assert data is None
        assert "expected list" in err

    def test_missing_generated_at(self):
        data, err = validate_hold_regime({"hold_regime": []})
        assert data is None
        assert "generated_at" in err

    def test_entry_not_a_dict(self):
        payload = {"generated_at": "...", "hold_regime": ["nope"]}
        data, err = validate_hold_regime(payload)
        assert data is None
        assert "must be an object" in err

    def test_ticker_not_a_string(self):
        data, err = validate_hold_regime(_hold_payload([{"ticker": 5}]))
        assert data is None
        assert "ticker" in err

    def test_interval_not_a_string(self):
        data, err = validate_hold_regime(_hold_payload([{"interval": None}]))
        assert data is None
        assert "interval" in err

    def test_benchmark_sharpe_not_numeric(self):
        data, err = validate_hold_regime(_hold_payload([{"benchmark_sharpe": "1.2"}]))
        assert data is None
        assert "benchmark_sharpe" in err

    def test_benchmark_sharpe_bool_rejected(self):
        data, err = validate_hold_regime(_hold_payload([{"benchmark_sharpe": True}]))
        assert data is None
        assert "benchmark_sharpe" in err

    def test_benchmark_total_return_not_numeric(self):
        data, err = validate_hold_regime(_hold_payload([{"benchmark_total_return": None}]))
        assert data is None
        assert "benchmark_total_return" in err

    def test_min_gap_not_numeric(self):
        data, err = validate_hold_regime(_hold_payload([{"min_gap": "1.0"}]))
        assert data is None
        assert "min_gap" in err

    def test_max_gap_not_numeric(self):
        data, err = validate_hold_regime(_hold_payload([{"max_gap": []}]))
        assert data is None
        assert "max_gap" in err

    def test_strategies_measured_not_an_int(self):
        data, err = validate_hold_regime(_hold_payload([{"strategies_measured": 3.5}]))
        assert data is None
        assert "strategies_measured" in err

    def test_strategies_measured_bool_rejected(self):
        data, err = validate_hold_regime(_hold_payload([{"strategies_measured": True}]))
        assert data is None
        assert "strategies_measured" in err

    def test_trades_total_not_an_int(self):
        data, err = validate_hold_regime(_hold_payload([{"trades_total": "42"}]))
        assert data is None
        assert "trades_total" in err


class TestHoldRegimeFlags:
    """_hold_regime_flags is the single source of truth for the hold-regime
    opportunity flag (bead market-skills-xtp): a (ticker, interval) pair
    where buy-and-hold beats EVERY measured strategy by >= 1.0 Sharpe."""

    @staticmethod
    def _combo(
        strategy,
        ticker,
        strat_sharpe,
        *,
        bench_sharpe=1.177,
        bench_return=0.67,
        trades=10,
        insufficient=False,
        bankrupted=False,
    ):
        return {
            "strategy": strategy,
            "ticker": ticker,
            "strategy_sharpe": None if bankrupted else strat_sharpe,
            "benchmark_sharpe": bench_sharpe,
            "benchmark_total_return": bench_return,
            "trades": trades,
            "insufficient_data": insufficient,
            "bankrupted": bankrupted,
        }

    @staticmethod
    def _flagged_current(interval="1d"):
        """kraken:AAAUSD on one interval: three negative-Sharpe strategies,
        buy-and-hold beats each by >= 1.0 Sharpe. Gaps: 1.68 / 2.18 / 2.48;
        trades 12 + 20 + 10 = 42."""
        return {
            f"{interval}\u00d7strategy-a\u00d7kraken:AAAUSD": TestHoldRegimeFlags._combo(
                "strategy-a", "kraken:AAAUSD", -0.5, trades=12
            ),
            f"{interval}\u00d7strategy-b\u00d7kraken:AAAUSD": TestHoldRegimeFlags._combo(
                "strategy-b", "kraken:AAAUSD", -1.0, trades=20
            ),
            f"{interval}\u00d7strategy-c\u00d7kraken:AAAUSD": TestHoldRegimeFlags._combo(
                "strategy-c", "kraken:AAAUSD", -1.3, trades=10
            ),
        }

    def test_flag_constants_match_documented_rule(self):
        run_mod = _load_run_mod("bp_hold_constants")
        assert run_mod.HOLD_REGIME_MIN_STRATEGIES == 3
        assert run_mod.HOLD_REGIME_MIN_GAP == 1.0

    def test_benchmark_outperforming_pair_flags_with_expected_fields(self):
        run_mod = _load_run_mod("bp_hold_flag_basic")
        flags = run_mod._hold_regime_flags(self._flagged_current())
        assert len(flags) == 1
        flag = flags[0]
        # exact field set and order
        assert list(flag) == [
            "ticker",
            "interval",
            "benchmark_sharpe",
            "benchmark_total_return",
            "strategies_measured",
            "min_gap",
            "max_gap",
            "trades_total",
        ]
        assert flag["ticker"] == "kraken:AAAUSD"
        assert flag["interval"] == "1d"
        assert flag["benchmark_sharpe"] == pytest.approx(1.177)
        assert flag["benchmark_total_return"] == pytest.approx(0.67)
        assert flag["strategies_measured"] == 3
        assert flag["min_gap"] == pytest.approx(1.68)
        assert flag["max_gap"] == pytest.approx(2.48)
        assert flag["trades_total"] == 42

    def test_gap_exactly_min_gap_flags_boundary_inclusive(self):
        run_mod = _load_run_mod("bp_hold_flag_boundary")
        current = {
            "1d\u00d7strategy-a\u00d7kraken:AAAUSD": self._combo("strategy-a", "kraken:AAAUSD", 1.0, bench_sharpe=2.0),
            "1d\u00d7strategy-b\u00d7kraken:AAAUSD": self._combo("strategy-b", "kraken:AAAUSD", 1.0, bench_sharpe=2.0),
            "1d\u00d7strategy-c\u00d7kraken:AAAUSD": self._combo("strategy-c", "kraken:AAAUSD", 1.0, bench_sharpe=2.0),
        }
        # every gap is exactly HOLD_REGIME_MIN_GAP (2.0 - 1.0 = 1.0)
        # kraken:BBBUSD carries a below-gap strategy (2.0 - 1.5 = 0.5), so a
        # deleted gap check would ALSO flag BBBUSD (len == 2), while a
        # >= / > mutation drops the exactly-1.0 pair altogether (len == 0)
        current["1d\u00d7strategy-a\u00d7kraken:BBBUSD"] = self._combo("strategy-a", "kraken:BBBUSD", 1.5, trades=10)
        current["1d\u00d7strategy-b\u00d7kraken:BBBUSD"] = self._combo("strategy-b", "kraken:BBBUSD", 1.0, trades=10)
        current["1d\u00d7strategy-c\u00d7kraken:BBBUSD"] = self._combo("strategy-c", "kraken:BBBUSD", 1.0, trades=10)
        flags = run_mod._hold_regime_flags(current)
        assert [(f["ticker"], f["min_gap"], f["max_gap"]) for f in flags] == [("kraken:AAAUSD", 1.0, 1.0)]

    def test_one_strategy_beating_benchmark_blocks_flag(self):
        run_mod = _load_run_mod("bp_hold_flag_outperform")
        current = self._flagged_current()
        # ONE strategy literally beats the benchmark → negative gap
        current["1d\u00d7strategy-c\u00d7kraken:AAAUSD"]["strategy_sharpe"] = 2.5
        assert run_mod._hold_regime_flags(current) == []

    def test_gap_below_min_gap_blocks_flag(self):
        run_mod = _load_run_mod("bp_hold_flag_gap_below")
        current = self._flagged_current()
        # gap 1.177 - 0.5 = 0.677 < HOLD_REGIME_MIN_GAP
        current["1d\u00d7strategy-c\u00d7kraken:AAAUSD"]["strategy_sharpe"] = 0.5
        assert run_mod._hold_regime_flags(current) == []

    def test_too_few_measured_strategies_does_not_flag(self):
        run_mod = _load_run_mod("bp_hold_flag_few")
        current = {
            "1d\u00d7strategy-a\u00d7kraken:AAAUSD": self._combo("strategy-a", "kraken:AAAUSD", -3.0, trades=10),
            "1d\u00d7strategy-b\u00d7kraken:AAAUSD": self._combo("strategy-b", "kraken:AAAUSD", -3.5, trades=10),
        }
        # both gaps large (4.177 / 4.677) but only 2 measured strategies
        assert run_mod._hold_regime_flags(current) == []

    @staticmethod
    def _deep_neg_current(bench_sharpe):
        """kraken:AAAUSD on 1d with deeply negative strategies
        (-1.5 / -2.0 / -2.5): every gap (1.3-2.5 for bench -0.2,
        1.5-2.5 for bench 0.0) already clears HOLD_REGIME_MIN_GAP, so
        ONLY the ``benchmark_sharpe > 0`` conjunct can block the flag —
        deleting that sign check makes this fixture flag."""
        current = {}
        for strat, sharpe, trades in (
            ("strategy-a", -1.5, 12),
            ("strategy-b", -2.0, 20),
            ("strategy-c", -2.5, 10),
        ):
            current[f"1d\u00d7{strat}\u00d7kraken:AAAUSD"] = TestHoldRegimeFlags._combo(
                strat, "kraken:AAAUSD", sharpe, bench_sharpe=bench_sharpe, trades=trades
            )
        return current

    @pytest.mark.parametrize(
        ("spec_name", "bench_sharpe"),
        [("bp_hold_flag_neg_bench", -0.2), ("bp_hold_flag_zero_bench", 0.0)],
    )
    def test_nonpositive_benchmark_sharpe_does_not_flag(self, spec_name, bench_sharpe):
        run_mod = _load_run_mod(spec_name)
        current = self._deep_neg_current(bench_sharpe)
        assert run_mod._hold_regime_flags(current) == []

    @pytest.mark.parametrize(
        ("spec_name", "bench_return"),
        [("bp_hold_flag_neg_return", -0.05), ("bp_hold_flag_zero_return", 0.0)],
    )
    def test_nonpositive_benchmark_return_does_not_flag(self, spec_name, bench_return):
        run_mod = _load_run_mod(spec_name)

        current = self._flagged_current()
        for info in current.values():
            info["benchmark_total_return"] = bench_return
        assert run_mod._hold_regime_flags(current) == []

    def test_insufficient_data_records_neither_count_nor_block(self):
        run_mod = _load_run_mod("bp_hold_flag_insuff")
        current = self._flagged_current()
        # a same-group insufficient record: excluded entirely (if the skip
        # were missing, its 9.9 Sharpe would create a negative gap that
        # blocks the flag, and its 500 trades would inflate the totals)
        current["1d\u00d7strategy-d\u00d7kraken:AAAUSD"] = self._combo(
            "strategy-d", "kraken:AAAUSD", 9.9, trades=500, insufficient=True
        )
        flags = run_mod._hold_regime_flags(current)
        assert [(f["ticker"], f["interval"]) for f in flags] == [("kraken:AAAUSD", "1d")]
        assert flags[0]["strategies_measured"] == 3
        assert flags[0]["trades_total"] == 42

    def test_bankrupted_record_skipped_and_does_not_block(self):
        run_mod = _load_run_mod("bp_hold_flag_bankrupt")
        current = self._flagged_current()
        # the engine's bankrupted case: numeric benchmark, strategy_sharpe
        # None (destroyed curve), insufficient_data False — skipped entirely
        current["1d\u00d7strategy-d\u00d7kraken:AAAUSD"] = self._combo(
            "strategy-d", "kraken:AAAUSD", 9.9, trades=999, bankrupted=True
        )
        flags = run_mod._hold_regime_flags(current)
        assert len(flags) == 1
        assert flags[0]["strategies_measured"] == 3
        assert flags[0]["trades_total"] == 42

    def test_intervals_are_separate_groups(self):
        run_mod = _load_run_mod("bp_hold_flag_intervals")
        current = self._flagged_current(interval="1d")
        # the 4h group has 3 measured strategies but a sub-threshold gap
        current.update(
            {
                "4h\u00d7strategy-a\u00d7kraken:AAAUSD": self._combo("strategy-a", "kraken:AAAUSD", 0.0, trades=10),
                "4h\u00d7strategy-b\u00d7kraken:AAAUSD": self._combo("strategy-b", "kraken:AAAUSD", 0.0, trades=10),
                "4h\u00d7strategy-c\u00d7kraken:AAAUSD": self._combo(
                    "strategy-c",
                    "kraken:AAAUSD",
                    0.5,
                    trades=10,  # gap 0.677 < 1.0
                ),
            }
        )
        flags = run_mod._hold_regime_flags(current)
        assert [(f["ticker"], f["interval"]) for f in flags] == [("kraken:AAAUSD", "1d")]

    def test_sorted_by_max_gap_desc(self):
        run_mod = _load_run_mod("bp_hold_flag_sorted")
        current = self._flagged_current()  # kraken:AAAUSD 1d, max_gap 2.48
        # hl:ZZZ 4h also flags, with a smaller max_gap
        current.update(
            {
                "4h\u00d7strategy-a\u00d7hl:ZZZ": self._combo(
                    "strategy-a", "hl:ZZZ", 0.177, bench_sharpe=1.177, bench_return=0.2, trades=5
                ),
                "4h\u00d7strategy-b\u00d7hl:ZZZ": self._combo(
                    "strategy-b", "hl:ZZZ", 0.177, bench_sharpe=1.177, bench_return=0.2, trades=5
                ),
                "4h\u00d7strategy-c\u00d7hl:ZZZ": self._combo(
                    "strategy-c", "hl:ZZZ", 0.177, bench_sharpe=1.177, bench_return=0.2, trades=5
                ),
            }
        )
        flags = run_mod._hold_regime_flags(current)
        assert [(f["ticker"], f["interval"], f["max_gap"]) for f in flags] == [
            ("kraken:AAAUSD", "1d", 2.48),
            ("hl:ZZZ", "4h", 1.0),
        ]

    def test_ties_break_deterministically_by_ticker_interval(self):
        run_mod = _load_run_mod("bp_hold_flag_tie")
        current = {}
        # two tickers with identical gaps (exactly 1.0) → tie on max_gap
        for ticker_key in ("kraken:BBBUSD", "kraken:AAAUSD"):
            for strat in ("strategy-a", "strategy-b", "strategy-c"):
                current[f"1d\u00d7{strat}\u00d7{ticker_key}"] = self._combo(
                    strat, ticker_key, 1.0, bench_sharpe=2.0, trades=10
                )
        flags = run_mod._hold_regime_flags(current)
        assert [(f["ticker"], f["max_gap"]) for f in flags] == [
            ("kraken:AAAUSD", 1.0),
            ("kraken:BBBUSD", 1.0),
        ]


class TestWriteHoldRegime:
    def test_writer_creates_valid_file_with_expected_entry(self, tmp_path, capsys):
        run_mod = _load_run_mod("bp_hold_write")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        current = TestHoldRegimeFlags._flagged_current()
        run_mod._write_hold_regime(current, {"baseline": {}}, out_dir)

        path = out_dir / "hold_regime.json"
        payload = json.loads(path.read_text())
        assert isinstance(payload["generated_at"], str) and payload["generated_at"]
        assert [f["ticker"] for f in payload["hold_regime"]] == ["kraken:AAAUSD"]
        assert payload["hold_regime"][0]["strategies_measured"] == 3
        assert payload["hold_regime"][0]["trades_total"] == 42
        # round-trips through the contract validator
        data, err = validate_hold_regime(json.loads(path.read_text()))
        assert data is not None, err
        captured = capsys.readouterr()
        assert "hold regime written (1 pair(s))" in captured.out
        assert "[WARN]" not in captured.out

    def test_no_flag_run_overwrites_stale_file(self, tmp_path, capsys):
        """Tonight's empty steady state must overwrite last night's flags:
        the hold_regime.json emptiness guard must never keep a stale flag
        list on disk as if it were current."""
        run_mod = _load_run_mod("bp_hold_write_stale")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        stale = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "hold_regime": [_hold_flag(ticker="hl:ZZZ", interval="4h")],
        }
        (out_dir / "hold_regime.json").write_text(json.dumps(stale, indent=2))

        run_mod._write_hold_regime({}, {"baseline": {}}, out_dir)

        payload = json.loads((out_dir / "hold_regime.json").read_text())
        assert payload["hold_regime"] == []
        assert payload["generated_at"] != "2026-01-01T00:00:00+00:00"
        data, err = validate_hold_regime(payload)
        assert data is not None, err
        captured = capsys.readouterr()
        assert "hold regime written (0 pair(s))" in captured.out
        assert "[WARN]" not in captured.out


class TestHoldRegimeDoesNotChangeConvictionThresholds:
    """Hard constraint (bead market-skills-xtp): the hold-regime signal is
    additive — a pair flagging as hold-regime must not change a single
    conviction floor. A hold-regime ticker's strategies legitimately have
    negative Sharpe and stay floor-99-suppressed."""

    @staticmethod
    def _current():
        """Negative-Sharpe strategies next to a healthy ticker, with
        benchmark fields below the flag rule so nothing qualifies yet."""
        current = {}
        for strat, sharpe, trades in (("strategy-a", -0.5, 20), ("strategy-b", -0.8, 30), ("strategy-c", -1.1, 40)):
            current[f"1d\u00d7{strat}\u00d7AAAUSD"] = {
                "strategy": strat,
                "ticker": "kraken:AAAUSD",
                "strategy_sharpe": sharpe,
                "benchmark_sharpe": -0.2,
                "benchmark_total_return": -0.1,
                "trades": trades,
                "insufficient_data": False,
            }
        current["1d\u00d7strategy-a\u00d7BBBUSD"] = {
            "strategy": "strategy-a",
            "ticker": "kraken:BBBUSD",
            "strategy_sharpe": 1.0,
            "benchmark_sharpe": 0.5,
            "benchmark_total_return": 0.2,
            "trades": 30,
            "insufficient_data": False,
        }
        return current

    def test_thresholds_byte_identical_when_hold_regime_flags(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_lib.ENV_MIN_TRADES, raising=False)
        run_mod = _load_run_mod("bp_hold_invariance")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        thresholds_path = out_dir / "conviction_thresholds_private.json"

        current = self._current()
        assert run_mod._hold_regime_flags(current) == []  # nothing flags yet
        run_mod._write_conviction_thresholds(current, {"baseline": {}}, out_dir)
        before = thresholds_path.read_bytes()

        # Mutate ONLY the benchmark fields so AAAUSD becomes hold-regime.
        for info in current.values():
            if info["ticker"] == "kraken:AAAUSD":
                info["benchmark_sharpe"] = 1.177
                info["benchmark_total_return"] = 0.67
        flags = run_mod._hold_regime_flags(current)
        assert [f["ticker"] for f in flags] == ["kraken:AAAUSD"]  # the mutation flags
        run_mod._write_hold_regime(current, {"baseline": {}}, out_dir)
        run_mod._write_conviction_thresholds(current, {"baseline": {}}, out_dir)

        after = thresholds_path.read_bytes()
        assert after == before  # byte-identical: no floor moved, none un-suppressed
        table = json.loads(after)["MIN_CONVICTION_TO_EMIT_BY_STRATEGY"]
        for strat in ("strategy-a", "strategy-b", "strategy-c"):
            assert table[strat]["kraken:AAAUSD"]["1d"] == 99
        assert table["strategy-a"]["kraken:BBBUSD"]["1d"] == 1
        hold_payload = json.loads((out_dir / "hold_regime.json").read_text())
        assert [f["ticker"] for f in hold_payload["hold_regime"]] == ["kraken:AAAUSD"]


class TestHoldRegimeBriefSection:
    def test_brief_contains_hold_regime_section_when_flag(self, tmp_path):
        run_mod = _load_run_mod("bp_hold_brief_flag")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        current = TestHoldRegimeFlags._flagged_current()
        run_mod._write_regime_health_brief(current, {"baseline": {}}, out_dir)

        text = (out_dir / "regime_health_brief.md").read_text()
        heading = "### 🎯 Hold-regime assets (edge is exposure, not timing)"
        assert heading in text
        assert text.index(heading) < text.index("### Strategy Health")
        assert "- kraken:AAAUSD 1d: buy-and-hold Sharpe +1.18 / +67% return" in text
        assert "underperforms it by 1.68-2.48 Sharpe (3 strategies, 42 trades)" in text
        _, err = validate_regime_brief(text)
        assert err is None

    def test_brief_omits_hold_regime_section_when_no_flag(self, tmp_path):
        run_mod = _load_run_mod("bp_hold_brief_noflag")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        # one measured strategy only: below HOLD_REGIME_MIN_STRATEGIES
        current = {
            "1d\u00d7strategy-a\u00d7kraken:AAAUSD": TestHoldRegimeFlags._combo(
                "strategy-a", "kraken:AAAUSD", -0.5, trades=12
            ),
        }
        run_mod._write_regime_health_brief(current, {"baseline": {}}, out_dir)

        text = (out_dir / "regime_health_brief.md").read_text()
        assert "Hold-regime" not in text
        _, err = validate_regime_brief(text)
        assert err is None


class TestHoldRegimeLines:
    def test_no_flags_render_nothing(self):
        run_mod = _load_run_mod("bp_hold_lines_empty")
        assert run_mod._hold_regime_lines([]) == []

    def test_lines_contain_heading_and_per_flag_details(self):
        run_mod = _load_run_mod("bp_hold_lines_flagged")
        flags = [
            _hold_flag(),
            _hold_flag(
                ticker="hl:ZZZ",
                interval="4h",
                benchmark_sharpe=0.9,
                benchmark_total_return=0.25,
                min_gap=1.0,
                max_gap=1.5,
                trades_total=30,
            ),
        ]
        lines = run_mod._hold_regime_lines(flags)
        assert lines[0] == "🎯 Hold-regime assets (edge is exposure, not timing):"
        assert len(lines) == 3
        assert lines[1] == (
            "  kraken:AAAUSD 1d: buy-and-hold Sharpe +1.18 / +67% return; "
            "every strategy underperforms it by 1.00-2.50 Sharpe"
        )
        assert "hl:ZZZ 4h:" in lines[2]
        assert "+0.90 / +25% return" in lines[2]
        assert "1.00-1.50 Sharpe" in lines[2]
