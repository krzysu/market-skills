"""Tests for backtest-pipeline contract validation.

Defines the TypedDicts shapes and validators for all five cross-boundary
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
    BEFORE the run record is appended, any of the five output files is
    written, or the rolling-baseline state file is mutated.

    Pre-fix (bead market-skills-kwu): an empty watchlist resolved 0
    tickers, the pipeline computed 0 pairs, wrote empty artifacts,
    appended a ``results 0 / errors []`` record, and exited 0 — so the
    cron reported ``ok`` while every downstream consumer saw empty edge
    artifacts. Writers are deliberately NOT patched here: if the guard
    fails to fire, the real writers append ``runs.jsonl`` and write the
    five output files into ``out_dir``, and the no-artifacts assertion
    fails."""

    _OUTPUT_FILES = [
        "conviction_thresholds_private.json",
        "fitness_matrix.json",
        "watchdog_regime_state.json",
        "swing_scan_skip_list.json",
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
