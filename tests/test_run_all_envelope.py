"""Envelope shape tests for run-all-l2 / run-all-l3.

Guards the envelope shape so a parser that hardcodes the wrong field name
fails loud in CI instead of silently returning None for every classification
across the batch.

Key invariants:
- L2 envelope: ``{"ticker": ..., "skills": {<name>: {"pattern": {"classification": ...}}}}``
- L3 envelope: ``{"ticker": ..., "strategies": {<name>: {"ideas": [...]}}}``
- L2 skills list: exactly 5
- L3 strategies list: exactly 6
"""

import importlib.util
import os
import random


def _load(name):
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", name, "lib.py")
    spec = importlib.util.spec_from_file_location(f"{name.replace('-', '_')}_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_candles(n=200, seed=42):
    rng = random.Random(seed)
    cs = []
    p = 100.0
    for i in range(n):
        p *= 1.0 + rng.uniform(-0.005, 0.012)
        cs.append([i * 86400, p, p + 0.5, p - 0.5, p, 100000])
    return cs


class TestRunAllL2Envelope:
    def test_envelope_keys(self):
        """L2 envelope must have ``ticker`` and ``skills`` keys, NOT ``data``/``strategies``."""
        ral2 = _load("run-all-l2")
        out = ral2.analyze("TEST", _make_candles(), interval="1d", period="1y")
        assert "ticker" in out
        assert "skills" in out
        assert "strategies" not in out, "L2 must use 'skills' key, not 'strategies'"
        assert "data" not in out, "L2 envelope is flat — top-level 'data' key would double-nest"

    def test_skill_count_is_five(self):
        """The L2 registry has exactly 5 skills — market-trend-analysis was removed in v0.7.0.

        Adding back the 6th skill without updating ``analysis.registry``
        would silently reintroduce the ghost-classification shape
        (present=False with a classification populated).
        """
        from analysis.registry import l2_skills

        skills = l2_skills()
        assert len(skills) == 5
        assert "market-trend-analysis" not in skills
        for required in (
            "market-accumulation",
            "market-breakout",
            "market-exhaustion",
            "market-liquidity-sweep",
            "market-trend-quality",
        ):
            assert required in skills

    def test_run_all_l2_iterates_registry(self):
        """``run-all-l2`` must iterate exactly the registered L2 skills —
        guards against a runner that hard-codes its own list and drifts
        from the registry."""
        from analysis.registry import l2_skills

        ral2 = _load("run-all-l2")
        out = ral2.analyze("TEST", _make_candles(), interval="1d", period="1y")
        assert set(out["skills"].keys()) == set(l2_skills())

    def test_pattern_classification_field_present(self):
        """Every L2 skill's pattern must expose ``classification``.

        Guards against parsers reading ``pattern.name`` (None across all L2
        skills) and treating every trend-quality instance as broken.
        """
        ral2 = _load("run-all-l2")
        out = ral2.analyze("TEST", _make_candles(), interval="1d", period="1y")
        for skill_name, result in out["skills"].items():
            if "error" in result:
                continue
            assert "pattern" in result, f"{skill_name}: missing 'pattern' key"
            pat = result["pattern"]
            assert "classification" in pat, (
                f"{skill_name}: missing 'classification' field — the field that Pattern B detection reads"
            )

    def test_pattern_present_and_classification_coherent(self):
        """If ``present=True``, ``classification`` must not be None (Pattern B silent)."""
        ral2 = _load("run-all-l2")
        out = ral2.analyze("TEST", _make_candles(), interval="1d", period="1y")
        for skill_name, result in out["skills"].items():
            if "error" in result:
                continue
            pat = result["pattern"]
            if pat.get("present") is True:
                assert pat.get("classification") is not None, (
                    f"{skill_name}: present=True but classification=None — Pattern B silent shape"
                )

    def test_signals_inspectable_when_pattern_absent(self):
        """Pattern B detection reads sub-signals even when present=False."""
        ral2 = _load("run-all-l2")
        out = ral2.analyze("TEST", _make_candles(), interval="1d", period="1y")
        for skill_name, result in out["skills"].items():
            if "error" in result:
                continue
            # The "signals" key must be present even when pattern didn't fire
            assert "signals" in result, f"{skill_name}: missing 'signals' dict"
            for sub_name, sub in result["signals"].items():
                assert "present" in sub, f"{skill_name}.signals.{sub_name}: missing 'present' field"
                assert "weight" in sub, f"{skill_name}.signals.{sub_name}: missing 'weight' field"


class TestRunAllL3Envelope:
    def test_envelope_keys(self):
        """L3 envelope must have ``ticker`` and ``strategies`` keys."""
        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")
        assert "ticker" in out
        assert "strategies" in out
        assert "skills" not in out, "L3 must use 'strategies' key, not 'skills' (L2 envelope)"
        assert "data" not in out, "L3 envelope is flat — top-level 'data' would double-nest"

    def test_strategy_count_is_six(self):
        """The L3 registry has exactly 6 strategies."""
        from analysis.registry import l3_strategies

        strats = l3_strategies()
        assert len(strats) == 6
        for required in (
            "strategy-trend-follow",
            "strategy-mean-reversion",
            "strategy-breakout-confirm",
            "strategy-accumulation-swing",
            "strategy-exhaustion-fade",
            "strategy-liquidity-sweep",
        ):
            assert required in strats

    def test_run_all_l3_iterates_registry(self):
        """``run-all-l3`` must iterate exactly the registered L3 strategies."""
        from analysis.registry import l3_strategies

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")
        assert set(out["strategies"].keys()) == set(l3_strategies())

    def test_ideas_array_per_strategy(self):
        """Each L3 strategy result must expose ``ideas`` (possibly empty) and ``narrative``."""
        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")
        for strat_name, result in out["strategies"].items():
            if "error" in result:
                continue
            assert "ideas" in result, f"{strat_name}: missing 'ideas' key"
            assert isinstance(result["ideas"], list)
            assert "narrative" in result

    def test_no_legacy_track_ideas_flag(self):
        """``--track-ideas`` was removed. Strategy-trend-follow should not
        expose it. Guards against the run-all-l3 CLI parser accepting the flag
        silently and letting stale callers re-introduce persistent state I/O
        via a removed path.
        """
        # Check the run.py wrapper doesn't accept --track-ideas
        run_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "skills",
            "run-all-l3",
            "scripts",
            "run.py",
        )
        if os.path.exists(run_path):
            with open(run_path) as f:
                src = f.read()
            assert "track-ideas" not in src, (
                "run-all-l3/scripts/run.py still references --track-ideas (removed in v0.7.0)"
            )

    def test_all_l3_strategies_accept_asset_class_kwarg(self):
        """Every L3 strategy must accept ``asset_class`` without raising TypeError.

        Regression for commit e5646ba: ``run-all-l3`` was updated to thread
        ``asset_class=asset_class`` into every L3 strategy call, but only
        ``strategy-trend-follow`` had the parameter. The other 5 strategies
        raised ``TypeError: analyze() got an unexpected keyword argument
        'asset_class'`` which the ``except Exception`` in
        ``run-all-l3/lib.py`` silently swallowed into an empty envelope.

        Pre-fix: 5/6 strategies report ``error: ...unexpected keyword argument 'asset_class'``.
        Post-fix: all 6 strategies run cleanly with any asset_class string.
        """
        from analysis.registry import l3_strategies

        ral3 = _load("run-all-l3")
        cs = _make_candles()
        out = ral3.analyze("TEST", cs, interval="1d", period="1y", asset_class="perp_dex")
        for strat_name in l3_strategies():
            assert strat_name in out["strategies"], f"{strat_name}: missing from envelope"
            strat_result = out["strategies"][strat_name]
            narr = strat_result.get("narrative", "")
            assert "unexpected keyword argument" not in narr, f"{strat_name} rejected asset_class kwarg: {narr}"
            assert "error:" not in narr, f"{strat_name} returned an error narrative: {narr}"

    def test_all_l3_strategy_signatures_expose_asset_class(self):
        """Static check: each L3 strategy's ``analyze`` must declare ``asset_class=None``.

        Complements the runtime check above by catching the regression at
        import time, before any execution. A strategy that drops the
        parameter (deliberate rename, accidental revert) fails this test.
        """
        import inspect

        for strat_name in (
            "strategy-trend-follow",
            "strategy-mean-reversion",
            "strategy-breakout-confirm",
            "strategy-accumulation-swing",
            "strategy-exhaustion-fade",
            "strategy-liquidity-sweep",
        ):
            mod = _load(strat_name)
            params = inspect.signature(mod.analyze).parameters
            assert "asset_class" in params, (
                f"{strat_name}.analyze missing asset_class parameter — "
                "run-all-l3 will raise TypeError and silently empty the envelope"
            )
            assert params["asset_class"].default is None, (
                f"{strat_name}.analyze asset_class default must be None (forward-compat), "
                f"got {params['asset_class'].default!r}"
            )


class TestL2L3EnvelopeDistinction:
    """Cross-envelope confusion: using the L2 envelope parser against L3 output
    (or vice versa). They look superficially similar but the inner key differs:
    ``skills`` vs ``strategies``, ``pattern`` vs ``ideas``.
    """

    def test_l2_has_skills_l3_has_strategies(self):
        ral2 = _load("run-all-l2")
        ral3 = _load("run-all-l3")
        cs = _make_candles()
        l2_out = ral2.analyze("TEST", cs, interval="1d", period="1y")
        l3_out = ral3.analyze("TEST", cs, interval="1d", period="1y")
        # L2: skills[].pattern.classification
        assert any("pattern" in v for v in l2_out["skills"].values())
        # L3: strategies[].ideas[]
        assert any("ideas" in v for v in l3_out["strategies"].values())


class TestRunAllL3DoesNotSwallowTypeErrors:
    """Defensive guard: ``run-all-l3`` and ``run-watchlist`` only pass
    ``asset_class`` to strategies that declare it. This prevents the
    half-shipped kwarg regression from recurring when a new strategy is
    added to the registry without the parameter.

    Implementation: ``inspect.signature(mod.analyze)`` filters kwargs
    per call. Strategies that don't declare ``asset_class`` still get
    called with the base kwargs (ticker/interval/period) and produce
    normal output.
    """

    def test_strategy_without_asset_class_still_runs(self, monkeypatch):
        """A legacy strategy without ``asset_class`` must not raise TypeError."""
        import analysis.skill_loader as sl

        # Pretend a strategy exists that doesn't accept asset_class.
        # If run-all-l3 blindly passed asset_class=, this would TypeError.
        def _legacy_analyze(c, *, ticker, interval="1d", period="1y"):
            return {"ideas": [{"direction": "long"}], "narrative": "legacy ok"}

        legacy_mod = type("Legacy", (), {"analyze": staticmethod(_legacy_analyze)})()
        canned = {"strategy-trend-follow": legacy_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y", asset_class="perp_dex")
        strat_result = out["strategies"]["strategy-trend-follow"]
        assert "error:" not in strat_result.get("narrative", ""), (
            f"legacy strategy was passed asset_class despite not declaring it: {strat_result}"
        )
        assert strat_result.get("ideas") == [{"direction": "long"}]

    def test_strategy_without_asset_class_runs_in_watchlist(self, monkeypatch):
        """Same guard applies to ``run-watchlist``."""
        import analysis.skill_loader as sl

        def _legacy_analyze(c, *, ticker, interval="1d", period="1y"):
            return {"ideas": [{"direction": "long"}], "narrative": "legacy ok"}

        legacy_mod = type("Legacy", (), {"analyze": staticmethod(_legacy_analyze)})()
        canned = {"strategy-trend-follow": legacy_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        rwl = _load("run-watchlist")
        out = rwl.analyze_ticker("TEST", _make_candles(), metadata={"asset_class": "perp_dex"})
        strat_result = out["l3"]["strategy-trend-follow"]
        assert "error:" not in strat_result.get("narrative", ""), (
            f"legacy strategy was passed asset_class despite not declaring it: {strat_result}"
        )
        assert strat_result.get("ideas") == [{"direction": "long"}]

    def test_non_typeerror_still_swallowed(self, monkeypatch):
        """RuntimeError (data error) keeps the soft narrative.

        The introspection guard must not change unrelated exception
        semantics — transient errors should still produce a per-strategy
        error envelope rather than tanking the whole batch.
        """
        import analysis.skill_loader as sl

        def _flaky_analyze(c, **_kw):
            raise RuntimeError("data feed down")

        flaky_mod = type("Flaky", (), {"analyze": staticmethod(_flaky_analyze)})()
        canned = {"strategy-trend-follow": flaky_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y", asset_class="perp_dex")
        strat_result = out["strategies"]["strategy-trend-follow"]
        assert "error: data feed down" in strat_result["narrative"], (
            f"RuntimeError should produce soft error narrative, got {strat_result}"
        )


class TestRunAllL3EnvelopeNormalization:
    """Every idea in the run-all-l3 envelope must expose both the canonical
    fields (``stop_loss`` / ``take_profit[]`` / ``rr_to_tp[]``) AND the flat
    fields consumers expect (``stop`` / ``tp1`` / ``tp2`` / ``tp3`` / ``rr_tp1``
    / ``tp1_pct``). Without the flat fields, a consumer reading
    ``run-all-l3 --json`` directly gets zeros/nulls for every bracket value
    and the bar evaluation silently fails.

    The normalization happens in ``run-all-l3/lib.py::_normalize_idea`` and is
    applied to every idea before the envelope is returned. Strategies that
    already emit the flat fields keep them; strategies that only emit the
    canonical fields get the flat ones derived.
    """

    def test_idea_with_only_canonical_fields_gets_flat_fields(self, monkeypatch):
        """An idea with ``stop_loss`` + ``take_profit[]`` + ``rr_to_tp[]`` only
        must have ``stop`` + ``tp1``/``tp2``/``tp3`` + ``rr_tp1`` derived."""
        import analysis.skill_loader as sl

        def _canonical_analyze(c, *, ticker, interval="1d", period="1y"):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 3,
                        "version": "v3",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "take_profit": [107.5, 112.5, 120.0],
                        "take_profit_ideal": [107.5, 112.5, 120.0],
                        "rr_to_tp": [1.5, 2.5, 4.0],
                        "reasoning": "test",
                        "source_skills": ["market-trend-quality"],
                    }
                ],
                "narrative": "ok",
            }

        canonical_mod = type("Canonical", (), {"analyze": staticmethod(_canonical_analyze)})()
        canned = {"strategy-trend-follow": canonical_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")
        idea = out["strategies"]["strategy-trend-follow"]["ideas"][0]

        # Canonical fields preserved
        assert idea["stop_loss"] == 95.0
        assert idea["take_profit"] == [107.5, 112.5, 120.0]
        assert idea["rr_to_tp"] == [1.5, 2.5, 4.0]

        # Flat fields derived
        assert idea["stop"] == 95.0
        assert idea["tp1"] == 107.5
        assert idea["tp2"] == 112.5
        assert idea["tp3"] == 120.0
        assert idea["rr_tp1"] == 1.5
        assert idea["rr_tp2"] == 2.5
        assert idea["rr_tp3"] == 4.0
        # tp1_pct = |107.5 - 100| / 100 * 100 = 7.5
        assert abs(idea["tp1_pct"] - 7.5) < 0.01

    def test_idea_with_flat_fields_already_present_unchanged(self, monkeypatch):
        """An idea that already has ``stop``/``tp1``/``rr_tp1`` must keep
        them unchanged. Consumer-supplied flat fields win over canonical
        derived ones (avoids stomping a manually-built envelope)."""
        import analysis.skill_loader as sl

        def _flat_analyze(c, *, ticker, interval="1d", period="1y"):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "short",
                        "conviction": 4,
                        "version": "v4",
                        "entry_price": 200.0,
                        "stop_loss": 210.0,
                        "take_profit": [185.0, 175.0, 160.0],
                        # Flat fields already present — must NOT be overwritten
                        "stop": 209.0,  # different from stop_loss
                        "tp1": 186.0,  # different from take_profit[0]
                        "rr_tp1": 1.43,
                    }
                ],
                "narrative": "ok",
            }

        flat_mod = type("Flat", (), {"analyze": staticmethod(_flat_analyze)})()
        canned = {"strategy-trend-follow": flat_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")
        idea = out["strategies"]["strategy-trend-follow"]["ideas"][0]

        # Flat fields preserved
        assert idea["stop"] == 209.0
        assert idea["tp1"] == 186.0
        assert idea["rr_tp1"] == 1.43
        # Canonical fields preserved too
        assert idea["stop_loss"] == 210.0
        assert idea["take_profit"] == [185.0, 175.0, 160.0]
        # Flat fields that the strategy didn't provide get derived from canonical
        assert idea["tp2"] == 175.0
        assert idea["tp3"] == 160.0
        # rr_tp2/rr_tp3 are NOT fabricated when the strategy provides no
        # rr_to_tp[] and no individual rr_tp*. Consistent with the stop
        # handler which also skips fabrication when stop_loss is None.
        assert "rr_tp2" not in idea
        assert "rr_tp3" not in idea
        # tp1_pct = |186 - 200| / 200 * 100 = 7.0
        assert abs(idea["tp1_pct"] - 7.0) < 0.01

    def test_mixed_forms_in_same_batch(self, monkeypatch):
        """One strategy emitting canonical-only, another emitting flat-only.
        Both forms must normalize correctly within the same batch."""
        import analysis.skill_loader as sl

        def _canonical_only(c, *, ticker, interval="1d", period="1y"):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "entry_price": 100.0,
                        "stop_loss": 95.0,
                        "take_profit": [110.0, 115.0, 125.0],
                        "rr_to_tp": [2.0, 3.0, 5.0],
                    }
                ],
                "narrative": "canonical",
            }

        def _flat_only(c, *, ticker, interval="1d", period="1y"):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "short",
                        "entry_price": 500.0,
                        "stop_loss": 520.0,
                        "take_profit": [480.0, 460.0, 430.0],
                        "rr_to_tp": [1.0, 2.0, 3.5],
                        "stop": 519.0,  # different from stop_loss (520)
                        "tp1": 481.0,  # different from take_profit[0] (480)
                    }
                ],
                "narrative": "flat",
            }

        canned = {
            "strategy-trend-follow": type("C", (), {"analyze": staticmethod(_canonical_only)})(),
            "strategy-mean-reversion": type("F", (), {"analyze": staticmethod(_flat_only)})(),
        }
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")

        canon_idea = out["strategies"]["strategy-trend-follow"]["ideas"][0]
        flat_idea = out["strategies"]["strategy-mean-reversion"]["ideas"][0]

        # Canonical-only strategy: flat fields derived from canonical
        assert canon_idea["stop"] == 95.0
        assert canon_idea["tp1"] == 110.0
        assert canon_idea["rr_tp1"] == 2.0
        assert abs(canon_idea["tp1_pct"] - 10.0) < 0.01

        # Flat-only strategy: flat fields preserved, missing ones derived
        assert flat_idea["stop"] == 519.0  # preserved (different from stop_loss=520)
        assert flat_idea["tp1"] == 481.0  # preserved (different from take_profit[0]=480)
        assert flat_idea["stop_loss"] == 520.0  # canonical preserved
        assert flat_idea["tp2"] == 460.0  # derived
        assert flat_idea["tp3"] == 430.0  # derived
        assert abs(flat_idea["tp1_pct"] - 3.8) < 0.01  # |481-500|/500*100

    def test_idea_with_no_bracket_data_passes_through(self, monkeypatch):
        """An idea with neither stop nor TPs (e.g. a structure-only
        pattern) must not be fabricated with zeros — passes through."""
        import analysis.skill_loader as sl

        def _bare_analyze(c, *, ticker, interval="1d", period="1y"):
            return {
                "ideas": [
                    {
                        "ticker": ticker,
                        "direction": "long",
                        "conviction": 2,
                        "reasoning": "structure-only, no bracket",
                    }
                ],
                "narrative": "bare",
            }

        bare_mod = type("Bare", (), {"analyze": staticmethod(_bare_analyze)})()
        canned = {"strategy-trend-follow": bare_mod}
        monkeypatch.setattr(sl, "load_skill", lambda name: canned.get(name))

        ral3 = _load("run-all-l3")
        out = ral3.analyze("TEST", _make_candles(), interval="1d", period="1y")
        idea = out["strategies"]["strategy-trend-follow"]["ideas"][0]

        # No bracket fields fabricated
        assert "stop" not in idea
        assert "tp1" not in idea
        assert "rr_tp1" not in idea
        assert "tp1_pct" not in idea
        # Original idea fields preserved
        assert idea["direction"] == "long"
        assert idea["reasoning"] == "structure-only, no bracket"
