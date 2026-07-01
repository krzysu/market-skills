"""analysis/watchlist — typed accessor for the market-watchlist data file.

Centralises on-disk path resolution and I/O so other skills don't have to
know about the file layout. skills/market-watchlist re-exports this surface
for skill convention compatibility.

Default location: `skills/market-watchlist/data/watchlist.json`, overridable via:
    - MARKET_SKILLS_WATCHLIST_PATH env var
    - explicit `path=` argument to every function
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from analysis.watchlist_format import (
    all_tickers as _all_tickers,
)
from analysis.watchlist_format import (
    basket as _basket,
)
from analysis.watchlist_format import (
    by_category as _by_category,
)
from analysis.watchlist_format import (
    categories as _categories,
)
from analysis.watchlist_format import (
    get_baskets,
    validate_storage,
)
from analysis.watchlist_format import (
    metadata_for as _metadata_for,
)
from analysis.watchlist_format import (
    provider_for as _provider_for,
)
from analysis.watchlist_format import (
    resolve as _resolve,
)


def default_path() -> Path:
    """Resolve the default watchlist file path.

    Skill lives at `skills/market-watchlist/`, data at `skills/market-watchlist/data/`.
    """
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    return repo_root / "skills" / "market-watchlist" / "data" / "watchlist.json"


def _resolve_path(path: str | os.PathLike | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("MARKET_SKILLS_WATCHLIST_PATH")
    if env:
        return Path(env).expanduser()
    return default_path()


def load_raw(path: str | os.PathLike | None = None) -> dict:
    """Read the raw `{baskets: {...}}` dict. Returns {} if file missing."""
    p = _resolve_path(path)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save_raw(data: dict, path: str | os.PathLike | None = None) -> None:
    """Atomic write: tmp + rename. Creates parent dirs as needed."""
    p = _resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


def all_tickers(path: str | os.PathLike | None = None) -> list[str]:
    return _all_tickers(load_raw(path))


def categories(path: str | os.PathLike | None = None) -> list[str]:
    return _categories(load_raw(path))


def by_category(name: str, path: str | os.PathLike | None = None) -> list[str]:
    return _by_category(load_raw(path), name)


def basket(name: str, path: str | os.PathLike | None = None) -> dict:
    return _basket(load_raw(path), name)


def metadata_for(ticker: str, path: str | os.PathLike | None = None) -> dict:
    return _metadata_for(load_raw(path), ticker)


def provider_for(ticker: str, path: str | os.PathLike | None = None) -> str | None:
    return _provider_for(load_raw(path), ticker)


def resolve(alias: str, path: str | os.PathLike | None = None) -> str | None:
    return _resolve(load_raw(path), alias)


def expand_tickers(items: list[str], path: str | os.PathLike | None = None) -> list[str]:
    """Resolve each item: bare symbols → canonical tickers, passthrough on miss.

    Used by `run-watchlist` to accept `--tickers btc eth xle` and turn it
    into `["BTCUSD", "ETHUSD", "XLExUSD"]`. Unknown symbols pass through
    unchanged so callers can use mixed `provider:ticker` notation.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        try:
            resolved = resolve(item, path)
        except ValueError:
            resolved = item
        canonical = resolved if resolved is not None else item
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


__all__ = [
    "all_tickers",
    "basket",
    "by_category",
    "categories",
    "default_path",
    "expand_tickers",
    "get_baskets",
    "load_raw",
    "metadata_for",
    "provider_for",
    "resolve",
    "save_raw",
    "validate_storage",
]
