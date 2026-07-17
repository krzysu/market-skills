"""Per-fix test fixture for backtest-engine CLI bt-5 (--from/--to, --json, --forensic-drill).

Covers the four acceptance areas from the bt-5 spec:

1. ``--from``/``--to`` ISO date filter slices candles by timestamp — tested
   directly against the factored-out ``_slice_candles_by_iso`` helper.
2. ``--json`` on a demo run emits a valid AXI envelope ``{data, count, errors,
   help}`` with the standard replay payload (plus fill-sim + metrics fields).
3. ``--forensic-drill <BAR>`` on a demo run prints the three blocks (decision,
   fill, risk verdict) and exits 0; in ``--json`` mode the same three blocks
   ride as the envelope ``data``.
4. ``--forensic-drill`` with an out-of-range or no-idea bar exits non-zero with
   a useful message.

The forensic data-extraction (``_forensic_decision`` / ``_risk_verdict_from_idea``
/ ``_forensic_fill_summary``) is also unit-tested directly with synthetic ideas +
candles so the verdict math is locked independently of the demo firing pattern.
All tests are deterministic and network-free.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_RUN_PATH = os.path.join(_REPO_ROOT, "skills", "backtest-engine", "scripts", "run.py")
_LIB_PATH = os.path.join(_REPO_ROOT, "skills", "backtest-engine", "lib.py")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RUN = _load_module("backtest_engine_run_forensic", _RUN_PATH)
_BT = _load_module("backtest_engine_lib_forensic", _LIB_PATH)

slice_candles_by_iso = _RUN._slice_candles_by_iso
parse_iso_to_epoch = _RUN._parse_iso_to_epoch
forensic_decision = _RUN._forensic_decision
risk_verdict_from_idea = _RUN._risk_verdict_from_idea
forensic_fill_summary = _RUN._forensic_fill_summary
FillSimulator = _BT.FillSimulator


def _candle(ts: int, o: float, h: float, lo: float, c: float, v: int = 1000) -> list:
    return [ts, o, h, lo, c, v]


def _idea(
    pair: str = "TEST",
    direction: str = "long",
    entry: float = 100.0,
    stop: float = 95.0,
    tps: tuple[float, ...] = (110.0, 115.0, 120.0),
    conviction: int = 3,
    entry_type: str = "market",
) -> dict:
    return {
        "pair": pair,
        "direction": direction,
        "conviction": conviction,
        "entry_type": entry_type,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": list(tps),
        "reasoning": "test idea",
        "source_skills": ["test"],
    }


def _run_cli(*argv: str):
    """Run the backtest-engine CLI in a subprocess; return (rc, stdout, stderr)."""
    proc = subprocess.run([sys.executable, _RUN_PATH, *argv], capture_output=True, text=True, cwd=_REPO_ROOT)
    return proc.returncode, proc.stdout, proc.stderr


_DEMO_ARGS = ["strategy-trend-follow", "DEMO", "1d", "--warmup", "100", "--bars", "200", "--demo"]


class TestSliceCandlesByIso:
    def test_date_only_inclusive_both_ends(self):
        # Daily candles at 86400s spacing; --from/--to as YYYY-MM-DD keep the
        # boundary days inclusive (1970-01-03 = i=2 .. 1970-01-07 = i=6 -> 5).
        candles = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
        out = slice_candles_by_iso(candles, "1970-01-03", "1970-01-07")
        assert len(out) == 5
        assert out[0][0] == 2 * 86400
        assert out[-1][0] == 6 * 86400

    def test_from_only_filter(self):
        candles = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
        out = slice_candles_by_iso(candles, "1970-01-05", None)
        assert len(out) == 6  # i=4..9
        assert out[0][0] == 4 * 86400

    def test_to_only_date_is_end_of_day_inclusive(self):
        # A date-only --to is read as end-of-day (23:59:59), so the candle at
        # midnight of that day is kept.
        candles = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
        out = slice_candles_by_iso(candles, None, "1970-01-04")
        assert len(out) == 4  # i=0..3
        assert out[-1][0] == 3 * 86400

    def test_full_iso_timestamp_start(self):
        # A full ISO 8601 --from at midday excludes the midnight candle before it.
        candles = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
        out = slice_candles_by_iso(candles, "1970-01-04T12:00:00", None)
        # i=3 candle (00:00) < 12:00 -> excluded; i=4 (next day 00:00) -> included.
        assert out[0][0] == 4 * 86400
        assert len(out) == 6

    def test_no_filter_returns_all(self):
        candles = [_candle(i * 86400, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
        assert slice_candles_by_iso(candles, None, None) == candles

    def test_parse_iso_date_only_end_of_day_vs_start(self):
        # --from date-only = start of day (00:00:00); --to date-only = end of day.
        assert parse_iso_to_epoch("1970-01-02", end_of_day=False) == 1 * 86400
        assert parse_iso_to_epoch("1970-01-02", end_of_day=True) == 1 * 86400 + 86399


class TestForensicBlocks:
    """Unit-test the forensic data extraction with synthetic ideas + candles."""

    def test_decision_extracts_l3idea_fields(self):
        idea = _idea(conviction=4)
        d = forensic_decision(idea)
        assert d["pair"] == "TEST"
        assert d["direction"] == "long"
        assert d["conviction"] == 4
        assert d["version"] == "v4"
        assert d["entry_price"] == 100.0
        assert d["stop_loss"] == 95.0
        assert d["take_profit"] == [110.0, 115.0, 120.0]
        assert d["entry_type"] == "market"
        assert d["reasoning"] == "test idea"

    def test_decision_version_falls_back_to_conviction(self):
        idea = _idea(conviction=2)
        idea.pop("version", None)
        assert forensic_decision(idea)["version"] == "v2"

    def test_risk_verdict_long_rr(self):
        idea = _idea(direction="long", entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        v = risk_verdict_from_idea(idea)
        assert v["direction"] == "long"
        assert v["entry"] == 100.0
        assert v["stop"] == 95.0
        assert v["target"] == 110.0
        assert v["stop_distance_pct"] == pytest.approx(5.0)
        assert v["target_distance_pct"] == pytest.approx(10.0)
        # (target - entry) / (entry - stop) = 10 / 5 = 2.0
        assert v["rr_to_tp1"] == pytest.approx(2.0)
        assert v["preliminary"] is True
        assert v["would_vet"] is True

    def test_risk_verdict_short_rr(self):
        idea = _idea(direction="short", entry=100.0, stop=105.0, tps=(90.0, 85.0, 80.0))
        v = risk_verdict_from_idea(idea)
        assert v["direction"] == "short"
        assert v["stop_distance_pct"] == pytest.approx(5.0)
        assert v["target_distance_pct"] == pytest.approx(10.0)
        # short: (entry - target) / (stop - entry) = 10 / 5 = 2.0
        assert v["rr_to_tp1"] == pytest.approx(2.0)
        assert v["preliminary"] is True

    def test_risk_verdict_missing_stop_yields_none_distances(self):
        # An idea with no stop_loss can't compute a stop distance or R:R.
        idea = _idea(entry=100.0, stop=None, tps=(110.0, 115.0, 120.0))
        v = risk_verdict_from_idea(idea)
        assert v["stop"] is None
        assert v["stop_distance_pct"] is None
        assert v["rr_to_tp1"] is None
        assert v["target_distance_pct"] == pytest.approx(10.0)

    def test_fill_summary_from_trade_record(self):
        sim = FillSimulator()
        idea = _idea(entry=100.0, stop=95.0, tps=(110.0, 115.0, 120.0))
        candles = [
            _candle(0, 100.0, 101.0, 99.0, 100.0),
            _candle(86400, 102.0, 112.0, 101.0, 111.0),  # target touched on entry bar
        ]
        rec = sim.simulate(idea, candles, entry_bar_index=0)
        f = forensic_fill_summary(rec)
        assert f["status"] == "filled"
        assert f["exit_reason"] == "target"
        assert f["exit_bar_index"] == 1
        assert f["entry"]["fill_price"] is not None
        assert f["entry"]["qty"] == 1.0
        assert f["entry"]["fee"] is not None
        assert f["entry"]["slippage_paid"] is not None
        assert f["exit"]["fill_price"] == pytest.approx(110.0)
        assert f["pnl_quote"] is not None


class TestJsonEnvelope:
    def test_demo_run_emits_axi_envelope(self):
        rc, stdout, _ = _run_cli(*_DEMO_ARGS, "--fill-sim", "--metrics", "--json")
        assert rc == 0
        env = json.loads(stdout)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["errors"] == []
        assert isinstance(env["help"], list) and len(env["help"]) >= 1
        data = env["data"]
        for key in ("strategy", "ticker", "interval", "warmup", "bars", "windows", "ideas"):
            assert key in data, f"missing {key} in replay payload"
        # fill-sim + metrics fields appear when those flags are on.
        assert "fill_sim" in data
        assert "metrics" in data
        assert env["count"] == data["ideas"]

    def test_demo_run_envelope_is_json_stable(self):
        # Two runs produce byte-identical stdout (deterministic demo + sort-free
        # envelope, but the payload itself is deterministic).
        rc1, out1, _ = _run_cli(*_DEMO_ARGS, "--fill-sim", "--json")
        rc2, out2, _ = _run_cli(*_DEMO_ARGS, "--fill-sim", "--json")
        assert rc1 == 0 and rc2 == 0
        assert out1 == out2


class TestForensicDrillCli:
    def test_bar_150_prints_three_blocks_and_exits_zero(self):
        rc, stdout, stderr = _run_cli(*_DEMO_ARGS, "--fill-sim", "--forensic-drill", "150")
        assert rc == 0, f"stderr={stderr}"
        assert "--- decision ---" in stdout
        assert "--- fill ---" in stdout
        assert "risk verdict" in stdout
        assert "preliminary" in stdout
        # The decision block carries the L3Idea fields.
        assert "pair:" in stdout
        assert "direction:" in stdout
        assert "take_profit:" in stdout

    def test_bar_150_json_returns_three_blocks_as_envelope_data(self):
        rc, stdout, _ = _run_cli(*_DEMO_ARGS, "--fill-sim", "--forensic-drill", "150", "--json")
        assert rc == 0
        env = json.loads(stdout)
        assert set(env.keys()) == {"data", "count", "errors", "help"}
        assert env["count"] == 1
        assert env["errors"] == []
        data = env["data"]
        assert set(data.keys()) == {"decision", "fill", "risk_verdict"}
        assert data["decision"]["pair"] == "DEMO"
        assert data["risk_verdict"]["preliminary"] is True
        assert data["risk_verdict"]["would_vet"] is True
        assert data["risk_verdict"]["rr_to_tp1"] is not None
        assert data["fill"]["status"] in ("filled", "open", "skipped")
        assert "entry" in data["fill"] and "exit" in data["fill"]

    def test_out_of_range_bar_exits_nonzero(self):
        rc, stdout, stderr = _run_cli(*_DEMO_ARGS, "--forensic-drill", "500")
        assert rc != 0
        assert "out of range" in stderr.lower()

    def test_no_idea_bar_exits_nonzero(self):
        # Bar 160 has no fired idea in the demo (the reversal fires at 149-151).
        rc, stdout, stderr = _run_cli(*_DEMO_ARGS, "--forensic-drill", "160")
        assert rc != 0
        assert "no idea" in stderr.lower()

    def test_below_warmup_bar_exits_nonzero(self):
        # Bar 50 is below warmup=100 -> out of range.
        rc, stdout, stderr = _run_cli(*_DEMO_ARGS, "--forensic-drill", "50")
        assert rc != 0
        assert "out of range" in stderr.lower()
