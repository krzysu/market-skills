"""Disk ring-buffer history store for macro regime signals.

Mirrors the ``analysis.chop`` ring-buffer pattern: XDG_DATA_HOME-based
default path, load/append API, cap at ``_HISTORY_CAP`` entries.
"""

import datetime as _dt
import json
import os
from typing import Any

from analysis.macro._constants import _HISTORY_CAP, _HISTORY_FILENAME


def default_history_path() -> str:
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        raise OSError(
            "XDG_DATA_HOME is not set; cannot resolve the macro history "
            "path. Set XDG_DATA_HOME or pass path= explicitly to "
            "load_history / append_tick."
        )
    return os.path.join(base, "market-skills", _HISTORY_FILENAME)


def _resolve_path(path: str | os.PathLike | None) -> str:
    if path is None:
        return default_history_path()
    return os.fspath(path)


def load_history(path: str | os.PathLike | None = None) -> list[dict]:
    resolved = _resolve_path(path)
    if not os.path.exists(resolved):
        return []
    try:
        with open(resolved) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return data


def append_macro_tick(
    signal: dict[str, Any],
    *,
    path: str | os.PathLike | None = None,
) -> int:
    if not isinstance(signal, dict):
        return 0
    try:
        resolved = _resolve_path(path)
        history = load_history(resolved)
    except OSError:
        return 0

    inputs = signal.get("inputs") or {}
    entry = {
        "ts": signal.get("timestamp") or _dt.datetime.now(_dt.UTC).isoformat(),
        "risk_appetite": (signal.get("regime") or {}).get("risk_appetite"),
        "liquidity": (signal.get("regime") or {}).get("liquidity"),
        "sentiment": (signal.get("regime") or {}).get("sentiment"),
        "vix": inputs.get("vix"),
        "dxy": inputs.get("dxy"),
        "us10y": inputs.get("us10y"),
        "fng": inputs.get("fng"),
        "btc_dominance": inputs.get("btc_dominance"),
        "btc_dominance_source": inputs.get("btc_dominance_source"),
        "regime_note": signal.get("regime_note"),
    }
    history.append(entry)
    if len(history) > _HISTORY_CAP:
        history = history[-_HISTORY_CAP:]
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w") as f:
            json.dump(history, f, indent=2)
    except OSError:
        return 0
    return 1


def _safe_append_tick(signal: dict[str, Any]) -> None:
    try:
        append_macro_tick(signal)
    except Exception:  # noqa: BLE001 — history write must not crash fetcher
        return
