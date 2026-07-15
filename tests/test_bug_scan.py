"""Tests for the bug-scan skill — L2 Pattern B shapes, weight drift, L3 skew, cross-TF."""

from __future__ import annotations

import importlib.util
import os

import pytest

# -- lib loading --------------------------------------------------------------


def _load_lib():
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", "bug-scan", "lib.py")
    spec = importlib.util.spec_from_file_location("bug_scan_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- shape #1: absent-with-subs ----------------------------------------------


class TestPatternB1AbsentWithSubs:
    def test_fires_on_3_subs_wsum_above_threshold(self):
        lib = _load_lib()
        result = {
            "pattern": {"present": False, "classification": None, "confidence": 1},
            "signals": {
                "ema_alignment": {"present": True, "weight": 0.25},
                "pullback_depth": {"present": True, "weight": 0.20},
                "volume_confirmation": {"present": True, "weight": 0.15},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="HYPEUSD", tf="1h", skill="market-trend-quality")
        shape_b1 = [f for f in findings if f["shape"] == lib.SHAPE_PATTERN_B_1]
        assert len(shape_b1) == 1
        assert shape_b1[0]["tag"] == "[BUG]"
        assert shape_b1[0]["wsum"] == pytest.approx(0.60)
        assert sorted(shape_b1[0]["present_sub_signals"]) == sorted(
            ["ema_alignment", "pullback_depth", "volume_confirmation"]
        )

    def test_does_not_fire_on_2_subs_below_wsum_threshold(self):
        """The recurring 0.35-wsum shape (liquidity-sweep) is below the
        0.30 floor only when wsum is exactly 0.30 — 0.35 should still
        fire. 2 subs at 0.20 each (wsum 0.40) fires.
        """
        lib = _load_lib()
        result = {
            "pattern": {"present": False, "classification": None, "confidence": 1},
            "signals": {
                "a": {"present": True, "weight": 0.20},
                "b": {"present": True, "weight": 0.20},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1h", skill="market-test")
        assert any(f["shape"] == lib.SHAPE_PATTERN_B_1 for f in findings)

    def test_does_not_fire_on_1_sub(self):
        lib = _load_lib()
        result = {
            "pattern": {"present": False, "classification": None, "confidence": 1},
            "signals": {"only_one": {"present": True, "weight": 0.50}},
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1h", skill="market-test")
        assert not any(f["shape"] == lib.SHAPE_PATTERN_B_1 for f in findings)

    def test_wsum_only_counts_present_subs(self):
        """Live data has 3-of-5 present at 0.60; must NOT report 1.00.

        Regression for the bug where _present_subs_and_wsum accumulated
        weight across ALL configured signals instead of only the
        present: true ones, causing Pattern B Shape #1 to report
        misleading wsum values (and misclassify severity).
        """
        lib = _load_lib()
        result = {
            "pattern": {"present": False, "classification": None, "confidence": 1},
            "signals": {
                "ema_alignment": {"present": True, "weight": 0.25},
                "hh_hl_integrity": {"present": False, "weight": 0.25},
                "pullback_depth": {"present": True, "weight": 0.20},
                "impulse_vs_retrace": {"present": False, "weight": 0.15},
                "volume_confirmation": {"present": True, "weight": 0.15},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="HYPEUSD", tf="1h", skill="market-trend-quality")
        shape_b1 = [f for f in findings if f["shape"] == lib.SHAPE_PATTERN_B_1]
        assert len(shape_b1) == 1
        assert shape_b1[0]["wsum"] == pytest.approx(0.60)
        assert shape_b1[0]["severity"] == "medium"
        # And the configured total should NOT also fire a weight_drift
        # finding — 1.0 is exactly the target.
        assert not any(f["shape"] == lib.SHAPE_WEIGHT_DRIFT for f in findings)


# -- shape #2: silent --------------------------------------------------------


class TestPatternB2Silent:
    def test_fires_when_present_true_classification_none(self):
        lib = _load_lib()
        result = {
            "pattern": {"present": True, "classification": None, "confidence": 1},
            "signals": {
                "ema_alignment": {"present": True, "weight": 0.25},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="HYPEUSD", tf="1h", skill="market-test")
        shape_b2 = [f for f in findings if f["shape"] == lib.SHAPE_PATTERN_B_2]
        assert len(shape_b2) == 1
        assert shape_b2[0]["tag"] == "[BUG]"
        assert shape_b2[0]["severity"] == "high"

    def test_does_not_fire_when_no_sub_signals(self):
        lib = _load_lib()
        result = {
            "pattern": {"present": True, "classification": None, "confidence": 1},
            "signals": {},
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1h", skill="market-test")
        assert not any(f["shape"] == lib.SHAPE_PATTERN_B_2 for f in findings)


# -- shape #3: ghost ---------------------------------------------------------


class TestPatternB3Ghost:
    def test_fires_when_present_false_classification_populated(self):
        lib = _load_lib()
        result = {
            "pattern": {
                "present": False,
                "classification": "HEALTHY_UPTREND",
                "confidence": 4,
            },
            "signals": {},
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1h", skill="market-test")
        shape_b3 = [f for f in findings if f["shape"] == lib.SHAPE_PATTERN_B_3]
        assert len(shape_b3) == 1
        assert shape_b3[0]["tag"] == "[BUG]"
        assert "HEALTHY_UPTREND" in shape_b3[0]["summary"]


# -- weight drift -------------------------------------------------------------


class TestWeightDrift:
    def test_fires_on_0_900_drift(self):
        """market-exhaustion 2026-06-21 regression: wsum=0.900 must fire."""
        lib = _load_lib()
        result = {
            "pattern": {"present": True, "classification": "BLOWOFF", "confidence": 4},
            "signals": {
                "a": {"present": True, "weight": 0.30},
                "b": {"present": False, "weight": 0.30},
                "c": {"present": False, "weight": 0.30},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1d", skill="market-exhaustion")
        wd = [f for f in findings if f["shape"] == lib.SHAPE_WEIGHT_DRIFT]
        assert len(wd) == 1
        assert wd[0]["tag"] == "[DRIFT]"
        assert wd[0]["wsum"] == pytest.approx(0.90)

    def test_does_not_fire_at_exactly_1_0(self):
        lib = _load_lib()
        result = {
            "pattern": {"present": True, "classification": "X", "confidence": 3},
            "signals": {
                "a": {"present": True, "weight": 0.40},
                "b": {"present": True, "weight": 0.30},
                "c": {"present": True, "weight": 0.30},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1d", skill="market-test")
        assert not any(f["shape"] == lib.SHAPE_WEIGHT_DRIFT for f in findings)

    def test_does_not_fire_within_tolerance(self):
        """0.96 → 0.04 deviation, within 0.05 tolerance."""
        lib = _load_lib()
        result = {
            "pattern": {"present": True, "classification": "X", "confidence": 3},
            "signals": {
                "a": {"present": True, "weight": 0.32},
                "b": {"present": True, "weight": 0.32},
                "c": {"present": True, "weight": 0.32},
            },
        }
        findings = lib._scan_l2_skill(result, ticker="X", tf="1d", skill="market-test")
        assert not any(f["shape"] == lib.SHAPE_WEIGHT_DRIFT for f in findings)


# -- L3 calibration skew -----------------------------------------------------


class TestL3CalibrationSkew:
    def test_fires_on_14_ideas_no_high_conv(self):
        lib = _load_lib()
        ideas = [{"conviction": c} for c in [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3]]
        result = {"ideas": ideas, "narrative": "x"}
        findings = lib._scan_l3_strategy(result, ticker="X", tf="1d", strategy="strategy-trend-follow")
        assert len(findings) == 1
        assert findings[0]["shape"] == lib.SHAPE_L3_CALIBRATION_SKEW
        assert findings[0]["ideas_count"] == 14
        assert findings[0]["high_conviction_count"] == 0

    def test_does_not_fire_below_min_ideas(self):
        lib = _load_lib()
        ideas = [{"conviction": 1} for _ in range(5)]
        result = {"ideas": ideas, "narrative": "x"}
        findings = lib._scan_l3_strategy(result, ticker="X", tf="1d", strategy="strategy-trend-follow")
        assert findings == []

    def test_does_not_fire_with_one_high_conv_idea(self):
        lib = _load_lib()
        ideas = [{"conviction": 1} for _ in range(5)] + [{"conviction": 4}]
        result = {"ideas": ideas, "narrative": "x"}
        findings = lib._scan_l3_strategy(result, ticker="X", tf="1d", strategy="strategy-trend-follow")
        assert findings == []


# -- cross-TF contradiction --------------------------------------------------


class TestCrossTfContradiction:
    def test_fires_on_aero_style_4h_vs_1d_split(self):
        lib = _load_lib()
        tickers = {
            "AEROUSD": {
                "tfs": {
                    "4h": {
                        "skills": {
                            "market-trend-quality": {
                                "pattern": {
                                    "present": True,
                                    "classification": "HEALTHY_UPTREND",
                                    "confidence": 4,
                                },
                                "signals": {},
                            }
                        }
                    },
                    "1d": {
                        "skills": {
                            "market-trend-quality": {
                                "pattern": {
                                    "present": True,
                                    "classification": "WEAKENING",
                                    "confidence": 2,
                                },
                                "signals": {},
                            }
                        }
                    },
                }
            }
        }
        findings = lib._scan_cross_tf_contradictions({"tickers": tickers}, {"tickers": {}})
        assert len(findings) == 1
        assert findings[0]["shape"] == lib.SHAPE_CROSS_TF_CONTRADICTION
        assert findings[0]["ticker"] == "AEROUSD"
        assert findings[0]["classification_a"] in ("HEALTHY_UPTREND", "WEAKENING")
        assert findings[0]["classification_b"] in ("HEALTHY_UPTREND", "WEAKENING")

    def test_does_not_fire_on_matching_tfs(self):
        lib = _load_lib()
        tickers = {
            "XUSD": {
                "tfs": {
                    "4h": {
                        "skills": {
                            "market-trend-quality": {
                                "pattern": {
                                    "present": True,
                                    "classification": "HEALTHY_UPTREND",
                                    "confidence": 4,
                                },
                                "signals": {},
                            }
                        }
                    },
                    "1d": {
                        "skills": {
                            "market-trend-quality": {
                                "pattern": {
                                    "present": True,
                                    "classification": "HEALTHY_UPTREND",
                                    "confidence": 4,
                                },
                                "signals": {},
                            }
                        }
                    },
                }
            }
        }
        findings = lib._scan_cross_tf_contradictions({"tickers": tickers}, {"tickers": {}})
        assert findings == []

    def test_vvv_1d_4h_split_works(self):
        lib = _load_lib()
        tickers = {
            "VVVUSD": {
                "tfs": {
                    "1d": {
                        "skills": {
                            "market-trend-quality": {
                                "pattern": {
                                    "present": True,
                                    "classification": "HEALTHY_UPTREND",
                                    "confidence": 4,
                                },
                                "signals": {},
                            }
                        }
                    },
                    "4h": {
                        "skills": {
                            "market-trend-quality": {
                                "pattern": {
                                    "present": True,
                                    "classification": "WEAKENING",
                                    "confidence": 2,
                                },
                                "signals": {},
                            }
                        }
                    },
                }
            }
        }
        findings = lib._scan_cross_tf_contradictions({"tickers": tickers}, {"tickers": {}})
        assert len(findings) == 1


# -- cross-TF direction conflict (L3-only --from-json) ----------------------


class TestCrossTfFromJsonL3Only:
    """The from-json bug: an L3-only envelope produced zero findings
    because the cross-TF detector only walked the L2 axis (and the call
    lived inside scan_l2, which saw an empty tickers dict). These lock in
    that scan() now runs the cross-TF detector over both tiers.
    """

    def test_from_json_l3_only_runs_cross_tf_direction_conflict(self):
        lib = _load_lib()
        envelope = {
            "tickers": {
                "HYPEUSD": {
                    "tfs": {
                        "1h": {
                            "strategies": {"strategy-trend-follow": {"ideas": [{"direction": "long", "conviction": 4}]}}
                        },
                        "4h": {
                            "strategies": {
                                "strategy-trend-follow": {"ideas": [{"direction": "short", "conviction": 3}]}
                            }
                        },
                    }
                }
            }
        }
        result = lib.scan(envelope)
        assert result["ok"] is True
        conflicts = [f for f in result["findings"] if f["shape"] == lib.SHAPE_CROSS_TF_DIRECTION_CONFLICT]
        assert len(conflicts) >= 1
        f = conflicts[0]
        assert f["tag"] == "[INFO]"
        assert f["ticker"] == "HYPEUSD"
        assert f["strategy"] == "strategy-trend-follow"
        # Both TFs reflected in the tf field.
        assert "1h" in f["tf"] and "4h" in f["tf"]

    def test_from_json_l2_only_still_runs_cross_tf_classification(self):
        """Regression guard: the L2 healthy-vs-weakening path is not broken
        by the signature change to _scan_cross_tf_contradictions.
        """
        lib = _load_lib()
        envelope = {
            "tickers": {
                "AEROUSD": {
                    "tfs": {
                        "4h": {
                            "skills": {
                                "market-trend-quality": {
                                    "pattern": {
                                        "present": True,
                                        "classification": "HEALTHY_UPTREND",
                                        "confidence": 4,
                                    },
                                    "signals": {},
                                }
                            }
                        },
                        "1d": {
                            "skills": {
                                "market-trend-quality": {
                                    "pattern": {
                                        "present": True,
                                        "classification": "WEAKENING",
                                        "confidence": 2,
                                    },
                                    "signals": {},
                                }
                            }
                        },
                    }
                }
            }
        }
        result = lib.scan(envelope)
        assert result["ok"] is True
        contra = [f for f in result["findings"] if f["shape"] == lib.SHAPE_CROSS_TF_CONTRADICTION]
        assert len(contra) == 1
        assert contra[0]["ticker"] == "AEROUSD"

    def test_from_json_l3_only_direction_no_conflict_when_same_side(self):
        """Two TFs with the same dominant direction must NOT emit a
        direction conflict — the conflict is only meaningful when the
        directions disagree.
        """
        lib = _load_lib()
        envelope = {
            "tickers": {
                "XUSD": {
                    "tfs": {
                        "1h": {
                            "strategies": {"strategy-trend-follow": {"ideas": [{"direction": "long", "conviction": 4}]}}
                        },
                        "4h": {
                            "strategies": {"strategy-trend-follow": {"ideas": [{"direction": "long", "conviction": 3}]}}
                        },
                    }
                }
            }
        }
        result = lib.scan(envelope)
        assert result["ok"] is True
        conflicts = [f for f in result["findings"] if f["shape"] == lib.SHAPE_CROSS_TF_DIRECTION_CONFLICT]
        assert conflicts == []

    def test_scan_calls_cross_tf_for_both_l2_and_l3(self):
        """A merged envelope with contradicting L2 classifications AND
        contradicting L3 directions must surface BOTH finding shapes —
        the scan does not raise and does not silently drop one tier.
        """
        lib = _load_lib()
        envelope = {
            "tickers": {
                "MERGED": {
                    "tfs": {
                        "1h": {
                            "skills": {
                                "market-trend-quality": {
                                    "pattern": {
                                        "present": True,
                                        "classification": "HEALTHY_UPTREND",
                                        "confidence": 4,
                                    },
                                    "signals": {},
                                }
                            },
                            "strategies": {
                                "strategy-trend-follow": {"ideas": [{"direction": "long", "conviction": 4}]}
                            },
                        },
                        "4h": {
                            "skills": {
                                "market-trend-quality": {
                                    "pattern": {
                                        "present": True,
                                        "classification": "WEAKENING",
                                        "confidence": 2,
                                    },
                                    "signals": {},
                                }
                            },
                            "strategies": {
                                "strategy-trend-follow": {"ideas": [{"direction": "short", "conviction": 3}]}
                            },
                        },
                    }
                }
            }
        }
        result = lib.scan(envelope)
        assert result["ok"] is True
        shapes = {f["shape"] for f in result["findings"]}
        assert lib.SHAPE_CROSS_TF_CONTRADICTION in shapes
        assert lib.SHAPE_CROSS_TF_DIRECTION_CONFLICT in shapes

    def test_l3_direction_conflict_filters_low_conviction(self):
        """A direction conflict where one TF only carries ideas with
        conviction < 2 must NOT emit a finding — the noise floor filters
        weak ideas out of the cross-TF direction comparison.
        """
        lib = _load_lib()
        envelope = {
            "tickers": {
                "XUSD": {
                    "tfs": {
                        "1h": {
                            "strategies": {"strategy-trend-follow": {"ideas": [{"direction": "long", "conviction": 4}]}}
                        },
                        "4h": {
                            "strategies": {
                                "strategy-trend-follow": {"ideas": [{"direction": "short", "conviction": 1}]}
                            }
                        },
                    }
                }
            }
        }
        result = lib.scan(envelope)
        assert result["ok"] is True
        conflicts = [f for f in result["findings"] if f["shape"] == lib.SHAPE_CROSS_TF_DIRECTION_CONFLICT]
        assert conflicts == []


# -- top-level scan dispatch -------------------------------------------------


class TestScanDispatch:
    def test_state_tracker_passthrough(self):
        lib = _load_lib()
        state = {
            "_comment": "test",
            "open_findings": [
                {
                    "key": "test_key",
                    "tag": "[BUG]",
                    "summary": "HYPEUSD 1h market-trend-quality: 3 subs w=0.60",
                    "ticks_seen": 2,
                }
            ],
        }
        envelope = lib.scan(state)
        assert envelope["ok"] is True
        assert len(envelope["findings"]) == 1
        assert envelope["findings"][0]["shape"] == "state_tracker"
        assert envelope["findings"][0]["tag"] == "[BUG]"

    def test_prebaked_findings_passthrough(self):
        lib = _load_lib()
        payload = {"findings": [{"tag": "[INFO]", "shape": "x", "summary": "y"}]}
        envelope = lib.scan(payload)
        assert envelope["ok"] is True
        assert envelope["findings"] == payload["findings"]

    def test_empty_input_returns_ok(self):
        lib = _load_lib()
        assert lib.scan({}) == {"ok": True, "findings": []}

    def test_envelope_with_skills_runs_l2_detectors(self):
        lib = _load_lib()
        envelope = {
            "interval": "1h",
            "tickers": {
                "X": {
                    "skills": {
                        "market-test": {
                            "pattern": {
                                "present": False,
                                "classification": None,
                                "confidence": 1,
                            },
                            "signals": {
                                "a": {"present": True, "weight": 0.30},
                                "b": {"present": True, "weight": 0.30},
                            },
                        }
                    }
                }
            },
        }
        result = lib.scan(envelope)
        assert result["ok"] is True
        assert any(f["shape"] == lib.SHAPE_PATTERN_B_1 for f in result["findings"])


# -- format_for_terminal ------------------------------------------------------


class TestFormatForTerminal:
    def test_no_findings_message(self):
        lib = _load_lib()
        out = lib.format_for_terminal({"ok": True, "findings": []})
        assert "no findings" in out

    def test_sorted_bug_first(self):
        lib = _load_lib()
        envelope = {
            "ok": True,
            "findings": [
                {"tag": "[INFO]", "shape": "x", "summary": "info", "severity": "low"},
                {"tag": "[BUG]", "shape": "y", "summary": "bug", "severity": "high"},
            ],
        }
        out = lib.format_for_terminal(envelope)
        lines = out.split("\n")
        # Bug should appear before info.
        bug_idx = next(idx for idx, line in enumerate(lines) if "[BUG]" in line)
        info_idx = next(idx for idx, line in enumerate(lines) if "[INFO]" in line)
        assert bug_idx < info_idx
