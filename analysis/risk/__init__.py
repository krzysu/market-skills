"""Risk layer — advisory, not a hard gate.

The LLM is the agent brain (ARCHITECTURE.md). Risk.vet is a deterministic
function the LLM calls BEFORE asking the user to confirm execution. The
verdict is *advisory* — the user can still explicitly approve a REJECT
through the execution skill confirm (and the LLM narrates the
override).

Design
------

A verdict is built by composing many small policies. Each policy takes an
Intent plus a RiskContext (portfolio state + watchlist metadata) and
returns a RiskVerdictFragment. ``vet()`` aggregates the fragments into a
RiskVerdict that the LLM can narrate.

Policies are independent. Adding/removing a policy means changing the
per-policy ``check()`` implementation and the corresponding ``*_POLICIES``
list — no changes to ``vet()`` or the contract. This is the same
composition pattern L1/L2/L3 skills use.

Layout
------

- :mod:`analysis.risk.spot` — six spot policies (position size, drawdown,
  tier exposure, daily budget, insufficient funds, per-pair cooldown).
- :mod:`analysis.risk.perps` — five perps policies (leverage cap,
  liquidation distance, stop distance, funding drag, duplicate position).
  Each short-circuits on spot intents.
- :mod:`analysis.risk._common` — ``RiskContext`` dataclass + the
  ``_worst`` / ``_empty_fragment`` helpers used by both submodules.
- :mod:`analysis.risk` (this file) — orchestration: ``vet``,
  ``select_policies``, ``is_perps_intent``, ``Policy`` type alias,
  policy-override YAML loader, and the public re-exports.

Why advisory not gate
---------------------

A hard gate (REJECT -> never execute) is incompatible with the LLM-first
design from the 2026-06-22 pivot. The LLM needs to be able to recommend an
override when context the policies didn't see justifies it (e.g. the user
explained they're closing a hedge, the policy only sees the long). The
*actual* safety layer is the execution skill confirm — that's
the line that never gets crossed without an explicit "y" from the human.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import fields
from pathlib import Path
from typing import Any

from analysis.contracts import RiskVerdict, RiskVerdictFragment
from analysis.providers.execution.base import Intent, validate_intent

from ._common import RiskContext, _worst
from .perps import (
    PERPS_POLICIES,
    duplicate_perps_position_policy,
    funding_drag_policy,
    leverage_cap_policy,
    liquidation_distance_policy,
    stop_distance_policy,
)
from .spot import (
    SPOT_POLICIES,
    daily_budget_policy,
    insufficient_funds_policy,
    per_pair_cooldown_policy,
    per_tier_exposure_policy,
    portfolio_drawdown_policy,
    position_size_policy,
    regime_consistency_policy,
)

# Policy signature: (intent, ctx) -> RiskVerdictFragment
Policy = Callable[[Intent, RiskContext], RiskVerdictFragment]

# Public aliases preserving the original analysis.risk public API.
DEFAULT_POLICIES: list[Policy] = list(SPOT_POLICIES)
"""Spot-only policy set. Re-exported as ``DEFAULT_POLICIES`` for
backward compatibility — older callers that imported this name keep
working."""


def is_perps_intent(intent: dict | Intent) -> bool:
    """True when ``intent`` targets a perps venue (any provider).

    Centralised so callers and :func:`vet` agree on the same definition.
    """
    if not isinstance(intent, dict):
        return False
    venue = str(intent.get("venue") or "")
    return venue.endswith("-perps") or intent.get("leverage") is not None


def select_policies(intent: dict | Intent, policies: Iterable[Policy] | None = None) -> list[Policy]:
    """Pick the policy list for ``intent``.

    Explicit ``policies`` argument wins. Otherwise: ``SPOT_POLICIES +
    PERPS_POLICIES`` for perps intents; ``SPOT_POLICIES`` for spot.
    """
    if policies is not None:
        return list(policies)
    if is_perps_intent(intent):
        return [*SPOT_POLICIES, *PERPS_POLICIES]
    return list(SPOT_POLICIES)


def vet(
    intent: dict | Intent,
    ctx: RiskContext,
    *,
    policies: Iterable[Policy] | None = None,
) -> RiskVerdict:
    """Aggregate N policies into one RiskVerdict. Pure function.

    Args:
        intent:     An Intent (dict or TypedDict). Validated before policy
                    evaluation; raises ``ValueError`` for malformed input.
        ctx:        Portfolio + watchlist + market snapshot (RiskContext).
        policies:   Optional override of ``select_policies``. Tests use this
                    to isolate a single policy.

    Returns:
        RiskVerdict with the worst-case status, all fragments, and a
        human-readable ``narrative_hint`` for the LLM to use as a starting
        point for its response.

    This function does NOT touch the network, the SQLite DB, or any
    venue. Pure function over its inputs — easy to test.
    """
    validated = validate_intent(intent)
    chosen = select_policies(validated, policies)
    fragments: list[RiskVerdictFragment] = [p(validated, ctx) for p in chosen]

    status = _worst(*(f["status"] for f in fragments))
    concerns = [f"{f['policy']}: {f['reason']}" for f in fragments if f["status"] == "CONCERN"]

    # Aggregate SCALE suggestions — take the smallest suggested volume
    # across SCALE fragments (most conservative).
    scale_vols = [f.get("suggested_volume") for f in fragments if f["status"] == "SCALE"]
    scale_vols = [v for v in scale_vols if v is not None]
    suggested_volume = min(scale_vols) if scale_vols else None

    # Build a one-sentence hint for the LLM.
    rejected = [f for f in fragments if f["status"] == "REJECT"]
    if rejected:
        narrative_hint = (
            f"Risk layer recommends against this {validated['side']} of "
            f"{validated['volume']} {validated['pair']}: " + "; ".join(f["reason"] for f in rejected)
        )
    elif status == "SCALE":
        scale_reasons = "; ".join(f["reason"] for f in fragments if f["status"] == "SCALE")
        if suggested_volume is not None:
            narrative_hint = (
                f"Risk layer suggests reducing volume to {suggested_volume:.6f} {validated['pair']}: {scale_reasons}"
            )
        else:
            # Defensive: SCALE shouldn't reach here without a suggested_volume
            # after per_tier_exposure_policy + position_size_policy guards.
            # If a future policy regresses, fall back to a generic scale hint
            # rather than crashing the LLM's verdict read.
            narrative_hint = (
                f"Risk layer flags scale concern for this {validated['side']} "
                f"of {validated['volume']} {validated['pair']}: {scale_reasons}"
            )
    elif status == "CONCERN":
        narrative_hint = (
            f"Risk layer has informational concerns about this {validated['side']} "
            f"of {validated['volume']} {validated['pair']}: " + "; ".join(concerns)
        )
    else:
        narrative_hint = f"Risk layer approves this {validated['side']} of {validated['volume']} {validated['pair']}."

    verdict: RiskVerdict = {
        "intent_id": validated["intent_id"],
        "pair": validated["pair"],
        "side": validated["side"],
        "status": status,
        "fragments": fragments,
        "concerns": concerns,
        "narrative_hint": narrative_hint,
    }
    if suggested_volume is not None:
        verdict["suggested_volume"] = suggested_volume

    return verdict


# ───────────────────────────────────────────────────────────── Policy overrides


ENV_POLICIES_PATH = "MARKET_SKILLS_RISK_POLICIES_PATH"

# YAML keys for RiskContext scalar overrides are declared on the dataclass
# fields themselves via ``field(metadata={"yaml_key": ..., "yaml_coerce": ...})``
# — the dataclass is the single source of truth. Adding a new overridable
# field is a one-line change on the field, not two-line (field + map).
_NON_SCALAR_OVERRIDE_KEYS = frozenset({"tier_caps", "portfolios", "pairs", "perps"})

_POLICY_OVERRIDE_TOP_KEYS: frozenset[str] = _NON_SCALAR_OVERRIDE_KEYS | frozenset(
    f.metadata["yaml_key"] for f in fields(RiskContext) if "yaml_key" in f.metadata
)

# Maps YAML keys to (RiskContext field, value coercion). Derived from the
# dataclass field metadata so adding a new overridable scalar field doesn't
# require a parallel entry here.
_GLOBAL_FIELD_MAP: dict[str, tuple[str, Callable[[Any], Any]]] = {
    f.metadata["yaml_key"]: (f.name, f.metadata["yaml_coerce"]) for f in fields(RiskContext) if "yaml_key" in f.metadata
}


def default_policies_path() -> Path:
    """Default location of ``policies.yaml`` (under the skill data dir).

    Matches the convention used by ``analysis.watchlist.default_path`` and
    ``analysis.notes.default_path``: a repo-relative path that resolves to
    ``skills/risk-engine/data/policies.yaml``.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "skills" / "risk-engine" / "data" / "policies.yaml"


def _resolve_policies_path(path: str | os.PathLike | None) -> Path | None:
    """explicit > ``$MARKET_SKILLS_RISK_POLICIES_PATH`` > ``default_policies_path``.

    Returns ``None`` when no path can be resolved (caller decides whether
    that's an error or just "use defaults"). Mirrors the resolution pattern
    in ``analysis.watchlist._resolve_path``.
    """
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get(ENV_POLICIES_PATH)
    if env:
        return Path(env).expanduser()
    return default_policies_path()


def load_policy_overrides(path: str | os.PathLike | None = None) -> dict:
    """Load and validate ``policies.yaml``.

    Returns the parsed mapping, or ``{}`` if the resolved path is missing or
    unreadable (matching ``analysis.watchlist.load_raw`` semantics — config is
    optional, missing means "use class defaults"). Raises ``ValueError`` on
    malformed YAML or unknown top-level keys so the caller can surface a clean
    error to the operator.

    Schema (top-level keys, all optional):

        max_position_pct:  float   # overrides ctx.max_position_pct
        max_drawdown_pct:  float   # overrides ctx.max_drawdown_pct
        daily_budget:      int     # overrides ctx.daily_trade_budget
        cooldown_hours:    float   # overrides ctx.pair_cooldown_hours
        tier_caps:         {tier: max_pct}     # merges into ctx.tier_limits
        portfolios:        {name: <overrides>} # per-portfolio block
        pairs:             {pair: <overrides>} # per-pair block
    """
    resolved = _resolve_policies_path(path)
    if resolved is None or not resolved.exists():
        return {}

    try:
        import yaml
    except ImportError as e:
        raise ValueError("PyYAML required to load policy overrides; install pyyaml") from e

    try:
        with open(resolved) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"failed to parse {resolved}: {e}") from e

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"policy file root must be a mapping, got {type(data).__name__}")

    unknown = set(data.keys()) - _POLICY_OVERRIDE_TOP_KEYS
    if unknown:
        raise ValueError(f"unknown top-level keys in policy file {resolved}: {sorted(unknown)}")
    return data


def _coerce_numeric(key: str, value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number, got {type(value).__name__}")
    return value


def _apply_dict_overrides(ctx: RiskContext, block: dict, *, source: str) -> None:
    """Apply a flat override dict to ``ctx`` in place. Shared by all three apply_*."""
    if not isinstance(block, dict):
        raise ValueError(f"{source}: overrides must be a mapping, got {type(block).__name__}")
    for yaml_key, (ctx_field, coerce) in _GLOBAL_FIELD_MAP.items():
        if yaml_key in block:
            raw = _coerce_numeric(f"{source}.{yaml_key}", block[yaml_key])
            setattr(ctx, ctx_field, coerce(raw))
    if "tier_caps" in block:
        caps = block["tier_caps"]
        if not isinstance(caps, dict):
            raise ValueError(f"{source}.tier_caps must be a mapping, got {type(caps).__name__}")
        for tier, cap in caps.items():
            if not isinstance(cap, (int, float)) or isinstance(cap, bool):
                raise ValueError(f"{source}.tier_caps.{tier} must be a number")
            ctx.tier_limits[str(tier)] = {"max_pct": float(cap)}
    if "perps" in block:
        apply_perps_overrides(ctx, block["perps"], source=f"{source}.perps")


def apply_perps_overrides(ctx: RiskContext, block: dict | None, *, source: str = "perps") -> RiskContext:
    """Apply the ``perps:`` block from ``policies.yaml`` to ``ctx``.

    Block shape::

        perps:
          leverage_caps:        # win over code defaults in LEVERAGE_CAPS
            SOLUSD: 3
            BTCUSD: 5
          default_leverage_cap: 10   # win over DEFAULT_LEVERAGE_CAP (5)
          mm_rates:             # win over code defaults in MM_RATES
            SOLUSD: 0.015
            HYPEUSD: 0.02

    All three sub-keys are optional. Pairs not in ``leverage_caps`` /
    ``mm_rates`` fall through to the code-defined defaults in
    ``analysis.providers.execution.kraken_perps``. ``default_leverage_cap``
    is the fallback for pairs not in the per-pair dict.

    The block is additive: merging top-level + per-portfolio blocks
    progressively overrides more keys. The same pair can appear in both
    blocks; the later (per-portfolio) wins.
    """
    if not block:
        return ctx
    if not isinstance(block, dict):
        raise ValueError(f"{source}: perps block must be a mapping, got {type(block).__name__}")

    if "leverage_caps" in block:
        caps = block["leverage_caps"]
        if not isinstance(caps, dict):
            raise ValueError(f"{source}.leverage_caps must be a mapping, got {type(caps).__name__}")
        for pair, cap in caps.items():
            if isinstance(cap, bool) or not isinstance(cap, int):
                raise ValueError(f"{source}.leverage_caps.{pair} must be an integer, got {type(cap).__name__}")
            if cap < 1:
                raise ValueError(f"{source}.leverage_caps.{pair} must be >= 1, got {cap}")
            ctx.leverage_caps[str(pair).upper()] = int(cap)

    if "mm_rates" in block:
        rates = block["mm_rates"]
        if not isinstance(rates, dict):
            raise ValueError(f"{source}.mm_rates must be a mapping, got {type(rates).__name__}")
        for pair, rate in rates.items():
            if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                raise ValueError(f"{source}.mm_rates.{pair} must be a number, got {type(rate).__name__}")
            if not 0 < float(rate) < 1:
                raise ValueError(f"{source}.mm_rates.{pair} must be in (0, 1), got {rate}")
            ctx.mm_rates[str(pair).upper()] = float(rate)

    if "default_leverage_cap" in block:
        dlc = block["default_leverage_cap"]
        if isinstance(dlc, bool) or not isinstance(dlc, int):
            raise ValueError(f"{source}.default_leverage_cap must be an integer, got {type(dlc).__name__}")
        if dlc < 1:
            raise ValueError(f"{source}.default_leverage_cap must be >= 1, got {dlc}")
        ctx.default_leverage_cap = int(dlc)

    return ctx


def apply_global_overrides(ctx: RiskContext, overrides: dict | None) -> RiskContext:
    """Apply top-level scalar overrides (and top-level ``tier_caps``) to ctx.

    Mutates ``ctx`` and returns it for chaining. Idempotent — reapplying
    the same overrides has the same effect as applying once.
    """
    if not overrides:
        return ctx
    _apply_dict_overrides(ctx, overrides, source="global")
    return ctx


def apply_portfolio_overrides(ctx: RiskContext, overrides: dict | None, portfolio_name: str | None) -> RiskContext:
    """Apply a per-portfolio block from ``overrides`` to ``ctx`` if it matches.

    Match is case-insensitive on the portfolio name. Returns ``ctx``.
    """
    if not overrides or not portfolio_name:
        return ctx
    portfolios = overrides.get("portfolios")
    if not isinstance(portfolios, dict):
        return ctx
    pf_block = None
    for name, block in portfolios.items():
        if isinstance(name, str) and name.lower() == portfolio_name.lower():
            pf_block = block
            break
    if not pf_block:
        return ctx
    _apply_dict_overrides(ctx, pf_block, source=f"portfolios.{portfolio_name}")
    return ctx


def apply_pair_overrides(ctx: RiskContext, overrides: dict | None, pair: str | None) -> RiskContext:
    """Apply a per-pair block from ``overrides`` to ``ctx`` if it matches.

    Match is case-insensitive on the bare ticker (``HYPEUSD`` matches both
    ``HYPEUSD`` and ``HYPE-USD`` in the YAML). Returns ``ctx``.
    """
    if not overrides or not pair:
        return ctx
    pairs = overrides.get("pairs")
    if not isinstance(pairs, dict):
        return ctx
    bare = pair.replace("-", "").replace("/", "").upper()
    pair_block = None
    for name, block in pairs.items():
        if not isinstance(name, str):
            continue
        n_bare = name.replace("-", "").replace("/", "").upper()
        if n_bare == bare or n_bare == pair.upper():
            pair_block = block
            break
    if not pair_block:
        return ctx
    _apply_dict_overrides(ctx, pair_block, source=f"pairs.{pair}")
    return ctx


__all__ = [
    "DEFAULT_POLICIES",
    "PERPS_POLICIES",
    "Policy",
    "RiskContext",
    "SPOT_POLICIES",
    "apply_global_overrides",
    "apply_pair_overrides",
    "apply_perps_overrides",
    "apply_portfolio_overrides",
    "daily_budget_policy",
    "default_policies_path",
    "duplicate_perps_position_policy",
    "funding_drag_policy",
    "insufficient_funds_policy",
    "is_perps_intent",
    "leverage_cap_policy",
    "liquidation_distance_policy",
    "load_policy_overrides",
    "per_pair_cooldown_policy",
    "per_tier_exposure_policy",
    "regime_consistency_policy",
    "portfolio_drawdown_policy",
    "position_size_policy",
    "select_policies",
    "stop_distance_policy",
    "vet",
]
