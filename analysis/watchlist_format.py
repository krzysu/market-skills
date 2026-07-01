"""analysis/watchlist_format — pure helpers for the asset watchlist.

No I/O. No env vars. All functions take a parsed watchlist dict (or path
arguments via analysis/watchlist.py).

Schema:
    {
      "baskets": {
        "<basket_name>": {
          "<ticker>": {
            "label": <str|None>,        # human-friendly name
            "tier": <int|None>,         # 1/2/3 priority for the agent brain
            "source": <str|None>,       # "kraken" | "yfinance" | "hyperliquid" | "ccxt"
            "yfinance_ticker": <str|None>,
            "hl_coin": <str|None>,
            "hl_proxy": <str|None>,
            "hl_proxy_weight": <float|None>,
            "tracking_only": <bool>,    # if true, skip strategy evaluation
            "sector": <str|None>,
            "asset_class": <str|None>,   # perp_dex / low_float / ai_infra — L3 strategies scale maturity thresholds
            "comment": <str|None>,
          }
        }
      }
    }
"""

from __future__ import annotations

_VALID_SOURCES = {"kraken", "yfinance", "hyperliquid", "ccxt"}


def get_baskets(data: dict) -> dict:
    """Return the baskets sub-dict, defaulting to {} if missing."""
    return data.get("baskets", {}) if isinstance(data, dict) else {}


def all_tickers(data: dict) -> list[str]:
    """Flat list of every ticker across every basket (order: basket order, then insertion)."""
    out: list[str] = []
    seen: set[str] = set()
    for _basket_name, members in get_baskets(data).items():
        if not isinstance(members, dict):
            continue
        for ticker in members:
            if ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def categories(data: dict) -> list[str]:
    """Basket names in insertion order."""
    return list(get_baskets(data).keys())


def by_category(data: dict, name: str) -> list[str]:
    """Tickers in a basket. Empty list if missing."""
    basket = get_baskets(data).get(name, {})
    if not isinstance(basket, dict):
        return []
    return list(basket.keys())


def basket(data: dict, name: str) -> dict:
    """Full basket dict (ticker -> metadata). Empty dict if missing."""
    b = get_baskets(data).get(name, {})
    return b if isinstance(b, dict) else {}


def metadata_for(data: dict, ticker: str) -> dict:
    """Metadata for a ticker across all baskets. Empty dict if not found.

    If the ticker appears in multiple baskets, the first match wins.
    """
    for _name, members in get_baskets(data).items():
        if isinstance(members, dict) and ticker in members:
            return members[ticker]
    return {}


def provider_for(data: dict, ticker: str) -> str | None:
    """Resolve provider for a ticker.

    Order:
      1. Explicit `provider:` prefix on the ticker (e.g. `hl:LIT` → `hyperliquid`)
      2. `source` field in metadata (e.g. `"yfinance"`, `"kraken"`)
      3. `None` if ambiguous/missing
    """
    if ":" in ticker:
        prefix = ticker.split(":", 1)[0].lower()
        prefix_map = {"hl": "hyperliquid", "kraken": "kraken", "yf": "yfinance", "yfinance": "yfinance"}
        if prefix in prefix_map:
            return prefix_map[prefix]
    meta = metadata_for(data, ticker)
    src = meta.get("source")
    if src in _VALID_SOURCES:
        return src
    return None


def _bare_aliases(ticker: str) -> set[str]:
    """Generate short alias tokens for a ticker so users can type `btc`, `eth`, `xle`.

    Examples:
      BTCUSD    -> {btcusd, btc}
      XLExUSD   -> {xlexusd, xle}
      URAUSD    -> {urausd, ura}
      hl:LIT    -> {hl:lit, lit}
      AERO      -> {aero}
    """
    p = ticker.lower()
    aliases: set[str] = {p}
    if ":" in p:
        # provider:ticker form — keep the full form AND the bare tail
        _, _, tail = p.partition(":")
        aliases.add(tail)
    else:
        for suffix in ("xusd", "usd", "eur"):
            if p.endswith(suffix) and len(p) > len(suffix):
                aliases.add(p[: -len(suffix)])
                break
    return aliases


def resolve(data: dict, alias: str) -> str | None:
    """Resolve a bare symbol (`btc`, `eth`) to its canonical ticker.

    Returns the ticker, or None if not found. Raises ValueError if ambiguous.
    """
    norm = alias.strip().lower()
    candidates: list[str] = []
    for ticker in all_tickers(data):
        if norm in _bare_aliases(ticker):
            candidates.append(ticker)
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError(f"ambiguous alias {alias!r}: matches {candidates}")
    return candidates[0]


def validate_storage(data: dict) -> list[str]:
    """Return list of validation errors (empty == valid). Pure."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be a dict"]
    if "baskets" not in data:
        errors.append("missing 'baskets' key")
        return errors
    baskets = data["baskets"]
    if not isinstance(baskets, dict):
        return ["'baskets' must be a dict"]
    for name, members in baskets.items():
        if not isinstance(members, dict):
            errors.append(f"basket {name!r}: must be a dict")
            continue
        for ticker, meta in members.items():
            if not isinstance(meta, dict):
                errors.append(f"{name}.{ticker}: metadata must be a dict")
                continue
            src = meta.get("source")
            if src is not None and src not in _VALID_SOURCES:
                errors.append(f"{name}.{ticker}: invalid source {src!r}")
            tier = meta.get("tier")
            if tier is not None and not isinstance(tier, int):
                errors.append(f"{name}.{ticker}: tier must be int, got {type(tier).__name__}")
            for k in ("yfinance_ticker", "hl_coin", "hl_proxy", "hl_proxy_note"):
                v = meta.get(k)
                if v is not None and not isinstance(v, str):
                    errors.append(f"{name}.{ticker}: {k} must be str")
            weight = meta.get("hl_proxy_weight")
            if weight is not None and not isinstance(weight, (int, float)):
                errors.append(f"{name}.{ticker}: hl_proxy_weight must be number")
            tracking = meta.get("tracking_only")
            if tracking is not None and not isinstance(tracking, bool):
                errors.append(f"{name}.{ticker}: tracking_only must be bool")
            asset_class = meta.get("asset_class")
            if asset_class is not None and not isinstance(asset_class, str):
                errors.append(f"{name}.{ticker}: asset_class must be str")
    return errors
