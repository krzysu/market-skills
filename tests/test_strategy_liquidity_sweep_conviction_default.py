"""Per-fix fixture pinning the liq-sweep conviction formula default (bead 7eq).

History: an earlier commit flipped the default from ``"current"`` to
``"max_plus_one"`` based on aggregated journal data that mixed strategies.
The aggregated numbers were not liq-sweep specific; with a
``--strategy=strategy-liquidity-sweep`` filter the journal reports zero
closed liq-sweep trades across all bands, so a flip cannot be defended
today. This test pins the reverted default (``"current"``) so any future
flip must come with strategy-filtered evidence.

These tests pin the post-revert contract:
  1. The default mode is ``"current"`` (legacy expression).
  2. For a (sweep, accum) pair that the two modes differ on, the default
     call must produce the ``"current"`` result, not the
     ``"max_plus_one"`` result.
  3. Explicit ``mode="max_plus_one"`` still computes that formula
     (back-compat for the previously-shipped constant shape).
"""

from __future__ import annotations

import importlib.util
import os


def _load_lib():
    lib_path = os.path.join(
        os.path.dirname(__file__), "..", "skills", "strategy-liquidity-sweep", "lib.py"
    )
    spec = importlib.util.spec_from_file_location("strategy_liquidity_sweep_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_mode_is_current():
    """The shipped default conviction mode is ``"current"`` (the legacy
    expression). A future flip must come with strategy-filtered journal data
    that justifies it (see bead 7eq notes)."""
    import inspect

    mod = _load_lib()
    sig = inspect.signature(mod.conviction_from_confidences)
    default = sig.parameters["mode"].default
    assert default == "current", (
        f"Default conviction mode must be 'current' (reverted after "
        f"strategy-conflated evidence); got {default!r}. To flip, supply "
        f"--strategy=strategy-liquidity-sweep to conviction_grid.py first."
    )


def test_default_uses_current_for_discriminating_pair():
    """For a (sweep, accum) pair where the two modes differ, the default-mode
    call must produce the ``"current"`` result.

    Pair (2, 3):
      * current      = 2 + 3 // 2 = 2 + 1 = 3
      * max_plus_one = max(2, 3) + 1 = 4
    The default call must yield 3, not 4.
    """
    mod = _load_lib()
    out = mod.conviction_from_confidences(2, 3)
    assert out == 3, (
        f"Default-mode call on (2,3) must yield 3 (current formula), got {out}. "
        f"This proves the default is 'current', not 'max_plus_one'."
    )


def test_explicit_max_plus_one_still_computes_that_formula():
    """Callers can still pin ``mode="max_plus_one"`` explicitly. This guards
    the previously-shipped constant shape from accidentally being dropped
    when the default mode is reverted."""
    mod = _load_lib()
    out = mod.conviction_from_confidences(2, 3, mode="max_plus_one")
    assert out == 4, (
        f"Explicit mode='max_plus_one' must yield max(2,3)+1=4; got {out}"
    )
