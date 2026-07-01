"""market-movers — CoinGecko gainers/losers/trending with retry/backoff.

Pure function over the HTTP path. The CLI wrapper handles rate-limit
log writing and stdout rendering. Single responsibility: fetch the
panels the morning brief consumes (gainers + losers + trending +
rotation categories) and degrade gracefully when CoinGecko throttles
the caller's IP.

The gainers/losers endpoint is ``/coins/markets?price_change_percentage=24h``
ordered by 24h % change — CoinGecko rate-limits this on the public tier
at ~30 req/min. The trending endpoint is a separate path with a more
generous quota, so on final 429 we keep the trending panel and surface
an explicit ``[MOVERS API RATE-LIMITED — gainers/losers unavailable]``
note for the morning-brief consumer.

The categories panel hits ``/coins/categories`` for category-level 24h
market-cap change — same public tier as ``/coins/markets`` so it shares
the rate-limit bucket. On final 429 it degrades the same way.

An optional Kraken tradability cross-reference wraps each gainers /
losers / trending entry with a ``tradable_on`` field sourced from
``kraken pairs -o json`` (cached in-process with a TTL). When the
``kraken`` CLI is unavailable (e.g. CI), the field degrades to
``None`` on every entry and a ``[MOVERS KRAKEN CLI UNAVAILABLE ...]``
marker is written into ``note``. Disable the cross-ref entirely via
``tradable_filter=False`` (the CLI flag ``--no-tradable-filter``).

Retry policy: exponential backoff (1s, 2s, 4s, ...) up to ``retries``
attempts (default 3 → two backoff sleeps of 1s and 2s). Sleeps use real
``time.sleep``; tests override via the ``_sleep`` argument to keep them
fast.

This skill is intentionally NOT registered in ``analysis.registry`` —
registry is L2/L3-only (see ARCHITECTURE.md "Extensibility"). Movers
is a utility endpoint, not a pattern detector. The skill_loader picks
it up by name via ``load_skill("market-movers")``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_BASE_HEADERS = {
    "User-Agent": "market-skills/0.1 (https://github.com/krzysu/market-skills)",
    "Accept": "application/json",
}
_DEFAULT_TIMEOUT_S = 10
_DEFAULT_RETRIES = 3
_BACKOFF_BASE_S = 1.0  # first retry sleeps 1s, then 2s, then 4s

_KRAKEN_PAIRS_CMD = ["kraken", "pairs", "-o", "json"]
_KRAKEN_PAIRS_TIMEOUT_S = 30.0
_KRAKEN_PAIRS_TTL_S_DEFAULT = 600.0

# CoinGecko id → Kraken base symbol, used to avoid symbol-collision lookups
# for the small set of coins whose short symbol is ambiguous on CoinGecko
# (e.g. "BTC" is ambiguous because many chains publish a token literally
# named BTC). New entries land here when market-movers surfaces a CoinGecko
# id that maps cleanly to a known Kraken pair but the symbol-only fallback
# can't pick the right one.
_COINGECKO_ID_TO_KRAKEN_BASE: dict[str, str] = {
    "bitcoin": "XXBT",
    "ethereum": "XETH",
    "litecoin": "XLTC",
    "dogecoin": "XDG",
    "ripple": "XXRP",
    "monero": "XMR",
    "zcash": "XZEC",
    "stellar": "XXLM",
    "dash": "DASH",
    "eos": "EOS",
    "ethereum-classic": "ETC",
    "tezos": "XTZ",
    "cardano": "ADA",
    "solana": "SOL",
    "polkadot": "DOT",
    "polygon": "MATIC",
    "avalanche-2": "AVAX",
    "chainlink": "LINK",
    "uniswap": "UNI",
    "aave": "AAVE",
    "maker": "MKR",
    "yearn-finance": "YFI",
    "compound-governance-token": "COMP",
    "sushi": "SUSHI",
    "curve-dao-token": "CRV",
    "synthetix-network-token": "SNX",
    "1inch": "1INCH",
    "balancer": "BAL",
    "the-graph": "GRT",
    "algorand": "ALGO",
    "tron": "TRX",
    "filecoin": "FIL",
    "cosmos": "ATOM",
    "near": "NEAR",
    "aptos": "APT",
    "sui": "SUI",
    "arbitrum": "ARB",
    "optimism": "OP",
    "hyperliquid": "HYPE",
    "ondo-finance": "ONDO",
    "injective-protocol": "INJ",
}

# Quote currencies Kraken's public AssetPairs call sorts actively online
# pairs across. Order is preference: USDT first so a USDT/USD pair is the
# canonical "tradeable on Kraken" answer for coins paired against USDT.
_KRAKEN_PREFERRED_QUOTES: tuple[str, ...] = (
    "ZUSD",
    "USDT",
    "USDC",
    "ZEUR",
    "ZGBP",
    "XXBT",
    "XETH",
)


def _sleep(seconds: float) -> None:
    """Sleep wrapper, overridable in tests."""
    time.sleep(seconds)


def _history_path() -> str | None:
    """Path to the rate-limit log; raises if XDG_DATA_HOME unset.

    Returns None when ``MARKET_SKILLS_NO_RATE_LIMIT_LOG=1`` is set —
    tests disable the on-disk log to keep their tmp dirs clean.
    """
    if os.environ.get("MARKET_SKILLS_NO_RATE_LIMIT_LOG") == "1":
        return None
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        return None
    return os.path.join(base, "market-skills", "coingecko-rate-limit.log")


def _log_rate_limit(endpoint: str, attempts: int, final_status: int) -> None:
    """Append one line to the rate-limit log. Silent on any filesystem error."""
    path = _history_path()
    if path is None:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = _dt.datetime.now(_dt.UTC).isoformat()
        with open(path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": ts,
                        "endpoint": endpoint,
                        "attempts": attempts,
                        "final_status": final_status,
                    }
                )
                + "\n"
            )
    except OSError:
        return


# ── Kraken tradability cross-reference ───────────────────────────────────

# Process-wide cache for ``kraken pairs -o json`` output. The CLI call is
# cheap (sub-second) but the payload is ~1.5k pairs that change rarely —
# 10 minute TTL is generous enough that the morning brief cron makes at
# most one subprocess call per day.
_kraken_pairs_cache_lock = threading.Lock()
_kraken_pairs_cache: dict[str, dict[str, Any]] | None = None
_kraken_pairs_cache_at: float = 0.0
_kraken_pairs_cache_cli_available: bool | None = None


def _reset_kraken_pairs_cache_for_test() -> None:
    """Clear the in-process Kraken pairs cache. Test-only helper."""
    global _kraken_pairs_cache, _kraken_pairs_cache_at, _kraken_pairs_cache_cli_available
    with _kraken_pairs_cache_lock:
        _kraken_pairs_cache = None
        _kraken_pairs_cache_at = 0.0
        _kraken_pairs_cache_cli_available = None


def _kraken_pairs_runner(args: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    """subprocess runner override point for tests."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)


def _fetch_kraken_pairs(
    *,
    ttl_s: float = _KRAKEN_PAIRS_TTL_S_DEFAULT,
    sleeper: Callable[[float], None] | None = None,
    now_s: Callable[[], float] = time.time,
    runner: Callable[..., subprocess.CompletedProcess] = _kraken_pairs_runner,
) -> tuple[dict[str, dict[str, Any]] | None, bool]:
    """Fetch and parse ``kraken pairs -o json``, cached in-process.

    Returns ``(pairs_index, cli_available)`` where:

      - ``pairs_index`` is ``{base: {altname, quote, status, wsname, ...}}``
        keyed by Kraken's ``base`` currency symbol (e.g. ``XXBT``, ``SOL``).
        ``None`` when the CLI is missing, the call times out, the JSON is
        unparseable, or the payload is empty/malformed.
      - ``cli_available`` is ``True`` if the ``kraken`` binary returned a
        parseable payload (even when empty — that's a legitimate "Kraken
        currently lists no pairs" answer, though unlikely). ``False`` if
        the binary wasn't on PATH (``FileNotFoundError``). ``None`` when
        the call wasn't made (cache warm + TTL not expired).

    Cache lookup is guarded by ``_kraken_pairs_cache_lock``; the cache is
    keyed by ``time.monotonic()-like`` ``now_s``. Tests pass a fake
    ``now_s`` to force expiry without sleeping.
    """
    global _kraken_pairs_cache, _kraken_pairs_cache_at, _kraken_pairs_cache_cli_available

    with _kraken_pairs_cache_lock:
        if _kraken_pairs_cache is not None and (now_s() - _kraken_pairs_cache_at) < ttl_s:
            return _kraken_pairs_cache, bool(_kraken_pairs_cache_cli_available)

        cli_available: bool | None = None
        pairs_dict: dict[str, dict[str, Any]] = {}

        try:
            result = runner(_KRAKEN_PAIRS_CMD, _KRAKEN_PAIRS_TIMEOUT_S)
        except FileNotFoundError:
            cli_available = False
        except subprocess.TimeoutExpired:
            cli_available = True
        else:
            cli_available = True
            if result.returncode != 0:
                pairs_dict = {}
            else:
                try:
                    raw = json.loads(result.stdout)
                except json.JSONDecodeError:
                    pairs_dict = {}
                else:
                    pairs_dict = _coerce_kraken_pairs_dict(raw)

        _kraken_pairs_cache = pairs_dict
        _kraken_pairs_cache_at = now_s()
        _kraken_pairs_cache_cli_available = cli_available

        if cli_available is False:
            # Fall through to the warm-cache short-circuit by returning
            # the empty dict paired with the False flag.
            return _kraken_pairs_cache, False
        return _kraken_pairs_cache, True


def _coerce_kraken_pairs_dict(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalise Kraken's ``pairs -o json`` payload to ``{altname: pair_dict}``.

    The CLI returns a dict keyed by altname (e.g. ``XBTUSD``) whose value
    contains ``altname``, ``base``, ``quote``, ``status``, ``wsname`` etc.
    Some historical versions of the CLI wrap the dict under a top-level
    ``{"result": {...}}`` envelope — same structure as the REST response.
    This helper collapses both shapes and filters rows whose value isn't
    a dict (defensive against schema drift).
    """
    if isinstance(raw, dict):
        if "result" in raw and isinstance(raw["result"], dict):
            raw = raw["result"]
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def _index_pairs_by_base(pairs: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Index pairs by their ``base`` currency, retaining only online rows.

    Kraken can publish pre-listing / delisted rows with ``status`` in
    ``online``, ``only_post``, ``cancel_only``, ``post_only`` etc. The
    tradable-on-Kraken answer is "yes iff at least one online pair exists",
    so we filter to ``online`` here. Cancelled/withdrawn-only rows are
    ignored.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs.values():
        if pair.get("status") != "online":
            continue
        base = pair.get("base")
        if not base:
            continue
        out.setdefault(base, []).append(pair)
    return out


def _candidate_bases_from_symbol(symbol: str) -> list[str]:
    """Generate plausible Kraken base candidates from a CoinGecko symbol.

    Kraken uses two naming schemes for base currencies:

      - Modern (post-2020): the symbol itself, e.g. ``SOL``, ``GRT``.
      - Legacy (pre-2020): prefixed with ``X`` / ``XX``, e.g. ``XXBT``,
        ``XDG``, ``XLTC``.

    For an input symbol ``SOL`` we try ``SOL`` first, then ``XSOL`` and
    ``XXSOL`` (legacy fallbacks). For legacy-shaped inputs (e.g. someone
    pipes a Kraken altname through) we try the unprefixed forms. The
    pair index treats unknown bases as a miss — false positives are
    harmless because we only return a match when the base actually
    appears in the AssetPairs response.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return []
    candidates: list[str] = [sym]
    if sym.startswith("XX") and len(sym) > 2:
        candidates.extend([sym[1:], sym[2:]])
    elif sym.startswith("X") and len(sym) > 1:
        candidates.append(sym[1:])
    else:
        candidates.append(f"X{sym}")
        candidates.append(f"XX{sym}")
    seen: set[str] = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def _pick_tradable_pair(
    pairs_for_base: list[dict[str, Any]],
    *,
    altname_hint: str | None = None,
) -> dict[str, Any] | None:
    """Pick the most useful pair for a given base out of the active list.

    Preference order matches ``_KRAKEN_PREFERRED_QUOTES`` (stablequote
    > stablecoin > XBT quote > XETH quote). Returns a single pair dict
    suitable for surfacing to the LLM under ``tradable_on``. ``None``
    when ``pairs_for_base`` is empty.
    """
    if not pairs_for_base:
        return None
    if altname_hint:
        for pair in pairs_for_base:
            if (pair.get("altname") or "").upper() == altname_hint.upper():
                return pair
    by_quote: dict[str, dict[str, Any]] = {pair.get("quote"): pair for pair in pairs_for_base if pair.get("quote")}
    for quote in _KRAKEN_PREFERRED_QUOTES:
        if quote in by_quote:
            return by_quote[quote]
    return pairs_for_base[0]


def _lookup_tradable(
    entry_id: str | None,
    symbol: str | None,
    pairs_index: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Resolve a CoinGecko entry to a Kraken pair, or ``None``.

    Strategy:

      1. If ``entry_id`` is in ``_COINGECKO_ID_TO_KRAKEN_BASE``, use that
         authoritative mapping. This bypasses symbol collision (a low-cap
         token literally named ``BTC`` won't match Bitcoin's ``XXBT``
         because its CoinGecko ``id`` is something like ``btc-2``).
      2. Otherwise, generate candidate bases from the symbol and try
         each against the index. A symbol-collision false positive is
         possible for unknown tokens whose short symbol matches a known
         coin, but documented in SKILL.md.

    Returns ``{"kraken": True, "altname", "base", "quote"}`` on hit,
    ``{"kraken": False}`` when the index is non-empty but the lookup
    missed, ``None`` when ``pairs_index`` is empty (CLI unavailable,
    which the caller distinguishes via the top-level flag).
    """
    if not pairs_index:
        return None
    sym = (symbol or "").upper().strip()
    candidates: list[str] = []
    if entry_id and entry_id in _COINGECKO_ID_TO_KRAKEN_BASE:
        candidates.append(_COINGECKO_ID_TO_KRAKEN_BASE[entry_id])
    candidates.extend(_candidate_bases_from_symbol(sym))

    seen: set[str] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        pairs = pairs_index.get(base)
        if pairs:
            chosen = _pick_tradable_pair(pairs)
            if chosen:
                return {
                    "kraken": True,
                    "altname": chosen.get("altname"),
                    "base": chosen.get("base"),
                    "quote": chosen.get("quote"),
                }
    return {"kraken": False}


# ── CoinGecko categories panel ──────────────────────────────────────────


def fetch_categories(
    *,
    retries: int = _DEFAULT_RETRIES,
    sleeper: Callable[[float], None] = _sleep,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Fetch the ``/coins/categories`` panel ordered by 24h market-cap change.

    Categories aggregate coins into themes (``AI``, ``DeFi``, ``Layer 1``,
    ``Meme``, etc.) — surfacing the category rotation is the missing
    "where should I dig" signal next to the per-coin gainers/losers
    panels. Shares the CoinGecko public tier with ``/coins/markets`` so
    a 429 here tends to fire alongside a gainers/losers 429 — same
    degrade path applies.

    Returns ``(entries, attempts, rate_limited)``. ``entries`` is the
    raw payload list — caller reduces via :func:`_category_summary`.
    Empty list on rate-limit, network failure, or HTTP error.
    """
    url = f"{_COINGECKO_BASE}/coins/categories"
    params = {"order": "market_cap_change_24h_desc"}
    data, attempts, rate_limited = _fetch_with_retry(url, params=params, retries=retries, sleeper=sleeper)
    if rate_limited:
        _log_rate_limit(url, attempts, 429)
    if not isinstance(data, list):
        return [], attempts, rate_limited
    return data, attempts, rate_limited


def _category_summary(entry: dict[str, Any]) -> dict[str, Any]:
    """Reduce a CoinGecko categories entry to the fields the brief consumes.

    Keeps ``top_3_coins_id`` (a list of CoinGecko ids in the category)
    so the LLM agent brain can decide which L3 strategies to run when
    a category is hot. Other keys (volume, market_cap_change_24h, etc.)
    vary across revs and are read defensively.
    """
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "market_cap_usd": entry.get("market_cap"),
        "pct_24h": entry.get("market_cap_change_24h"),
        "top_3_coins_id": entry.get("top_3_coins_id") or [],
    }


def _fetch_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retries: int = _DEFAULT_RETRIES,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    sleeper: Callable[[float], None] = _sleep,
) -> tuple[dict[str, Any] | list[Any] | None, int, bool]:
    """GET with exponential backoff on 429.

    Returns ``(data, attempts_used, rate_limited)``. ``rate_limited``
    is True when the final response was 429 — caller's signal that the
    "degrade" path applies.
    """
    attempts = 0
    for i in range(retries):
        attempts += 1
        try:
            r = requests.get(url, params=params or {}, headers=_BASE_HEADERS, timeout=timeout_s)
        except requests.RequestException:
            # network error: back off and retry, but don't mark rate_limited
            if i + 1 < retries:
                sleeper(_BACKOFF_BASE_S * (2**i))
                continue
            return None, attempts, False
        if r.status_code == 200:
            try:
                return r.json(), attempts, False
            except ValueError:
                return None, attempts, False
        if r.status_code == 429:
            if i + 1 < retries:
                sleeper(_BACKOFF_BASE_S * (2**i))
                continue
            return None, attempts, True
        # any other 4xx/5xx — no retry, just bail
        return None, attempts, False


def fetch_gainers_losers(
    *,
    vs_currency: str = "usd",
    limit: int = 50,
    retries: int = _DEFAULT_RETRIES,
    sleeper: Callable[[float], None] = _sleep,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Fetch the ``/coins/markets`` panel ordered by 24h % change.

    Returns ``(entries, attempts, rate_limited)``. ``entries`` is the
    raw CoinGecko payload — gainers are sorted descending, losers are
    sorted ascending; the caller splits them via ``_split_pct``. Empty
    list on rate-limit or transport failure. ``rate_limited`` is True
    when the final response was 429 — same flag the trending path
    consults to decide whether to log an incident.
    """
    url = f"{_COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": vs_currency,
        "order": "percent_change_24h_desc",
        "per_page": limit,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    data, attempts, rate_limited = _fetch_with_retry(url, params=params, retries=retries, sleeper=sleeper)
    if rate_limited:
        _log_rate_limit(url, attempts, 429)
    if not isinstance(data, list):
        return [], attempts, rate_limited
    return data, attempts, rate_limited


def fetch_trending(
    *,
    retries: int = _DEFAULT_RETRIES,
    sleeper: Callable[[float], None] = _sleep,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Fetch the ``/search/trending`` panel.

    Trending has a more generous quota than ``/coins/markets`` on the
    public tier, so we keep the panel populated even when gainers/losers
    rate-limits. The return shape mirrors ``fetch_gainers_losers``.
    """
    url = f"{_COINGECKO_BASE}/search/trending"
    data, attempts, rate_limited = _fetch_with_retry(url, retries=retries, sleeper=sleeper)
    if rate_limited:
        _log_rate_limit(url, attempts, 429)
    if not isinstance(data, dict):
        return [], attempts, rate_limited
    coins = data.get("coins") or []
    # The trending endpoint nests the coin payload under ``item``; flatten.
    entries = []
    for row in coins:
        item = row.get("item") or row
        entries.append(item)
    return entries, attempts, rate_limited


def _split_pct(entries: list[dict[str, Any]], *, top_n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the markets panel into (gainers, losers) ordered by 24h % change.

    CoinGecko's ``/coins/markets`` with ``order=percent_change_24h_desc``
    returns gainers first; we collect the top ``top_n`` as gainers and
    the bottom ``top_n`` as losers. The middle is dropped — the morning
    brief only needs the tails.
    """

    def _pct(c):
        raw = c.get("price_change_percentage_24h_in_currency") or c.get("price_change_percentage_24h") or 0
        return float(raw or 0)

    ranked = sorted(entries, key=_pct)
    losers = ranked[:top_n]
    gainers = list(reversed(ranked[-top_n:]))
    return gainers, losers


# Sentinel used by ``_entry_summary`` to distinguish "kwarg omitted —
# caller didn't opt in" from "kwarg passed as None — force the field to
# null". Module-private because the distinction only matters inside
# this file's fetch path.
_SKIP_TRADABLE_MARKER = object()


def _entry_summary(
    entry: dict[str, Any],
    *,
    tradable_on: dict[str, Any] | None | object = _SKIP_TRADABLE_MARKER,
) -> dict[str, Any]:
    """Reduce a CoinGecko entry to the fields the morning brief consumes.

    Keeps the dict small (symbol + pct + price for gainers/losers;
    name + market_cap_rank for trending) and stable across CoinGecko
    payload revs that add new keys without renaming existing ones.

    The ``tradable_on`` keyword opts into the Kraken cross-reference
    field. Default sentinel (``_SKIP_TRADABLE_MARKER``) preserves the
    pre-extension behaviour so existing callers that don't pass the
    kwarg see no field at all. Pass ``None`` to force the field to
    ``null`` (the "CLI unavailable" case) or a dict to surface a hit
    / miss result.
    """
    summary: dict[str, Any] = {
        "id": entry.get("id") or entry.get("coin_id"),
        "symbol": (entry.get("symbol") or "").upper(),
        "name": entry.get("name"),
        "pct_24h": entry.get("price_change_percentage_24h_in_currency") or entry.get("price_change_percentage_24h"),
        "price_usd": entry.get("current_price"),
        "market_cap_rank": entry.get("market_cap_rank") or entry.get("market_cap"),
    }
    if tradable_on is not _SKIP_TRADABLE_MARKER:
        summary["tradable_on"] = tradable_on
    return summary


def fetch_movers(
    *,
    top_n: int = 7,
    retries: int = _DEFAULT_RETRIES,
    sleeper: Callable[[float], None] = _sleep,
    tradable_filter: bool = True,
    categories_top_n: int = 10,
    kraken_pairs_ttl_s: float = _KRAKEN_PAIRS_TTL_S_DEFAULT,
    kraken_runner: Callable[..., subprocess.CompletedProcess] = _kraken_pairs_runner,
    now_s: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Fetch gainers, losers, trending, and rotation categories; degrade on rate-limit.

    Returns a MoversPayload::

        {
            "fetched_at": "<iso8601-utc>",
            "gainers": [ <entry> x top_n ],          # entry shape: + "tradable_on" when enabled
            "losers":  [ <entry> x top_n ],
            "trending": [ <entry> x top_n ],
            "categories": [ <category> x categories_top_n ],
            "rate_limited": bool,
            "attempts": { "gainers_losers", "trending", "categories": int },
            "note": "",                              # populated on degradation markers
            "kraken_cli_available": bool | None,     # None when tradable_filter disabled
            "tradable_filter": bool,
        }

    Degradation markers in ``note``:

      - ``[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]``
        when the markets endpoint exhausts retries (gainers/losers empty,
        trending kept).
      - ``[MOVERS API RATE-LIMITED — categories unavailable this run]``
        when the categories endpoint exhausts retries (categories empty,
        gainers/losers/trending unaffected).
      - ``[MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]``
        when ``tradable_filter`` is enabled and the ``kraken`` CLI is
        absent or times out; ``tradable_on`` is ``null`` on every entry.

    Symbol-collision caveat: a CoinGecko entry whose ``id`` is not in
    the small built-in ``_COINGECKO_ID_TO_KRAKEN_BASE`` map falls back to
    symbol-only lookup, which can match a Kraken pair for a different
    coin sharing the symbol. The id-based map covers the top-50 coins
    by market cap to prevent the most likely collisions; consumers that
    need a precise answer for an unmapped coin should cross-reference
    the CoinGecko ``id`` against Kraken's own listing pages.
    """
    fetched_at = _dt.datetime.now(_dt.UTC).isoformat()

    markets_panel, markets_attempts, markets_rl = fetch_gainers_losers(retries=retries, sleeper=sleeper)
    trending_panel, trending_attempts, trending_rl = fetch_trending(retries=retries, sleeper=sleeper)
    categories_panel, categories_attempts, categories_rl = fetch_categories(retries=retries, sleeper=sleeper)

    # Kraken tradability cross-reference. Runs once per ``fetch_movers``
    # call (the in-process cache skips on repeat calls within ``ttl_s``).
    # ``kraken_cli_available`` is None when the filter is disabled —
    # we never probed. When enabled, it's True/False reflecting the
    # last probe's outcome (warms the cache so the cron run's second
    # call uses the same data).
    if tradable_filter:
        pairs_dict, cli_available = _fetch_kraken_pairs(
            ttl_s=kraken_pairs_ttl_s,
            runner=kraken_runner,
            now_s=now_s,
        )
        pairs_index = _index_pairs_by_base(pairs_dict or {})
        kraken_cli_available: bool | None = cli_available
    else:
        pairs_index = {}
        kraken_cli_available = None

    def _tradable_for(entry: dict[str, Any]) -> dict[str, Any] | None | type(_SKIP_TRADABLE_MARKER):
        if not tradable_filter:
            return _SKIP_TRADABLE_MARKER
        return _lookup_tradable(
            entry.get("id"),
            entry.get("symbol"),
            pairs_index,
        )

    gainers: list[dict[str, Any]] = []
    losers: list[dict[str, Any]] = []
    if markets_panel and not markets_rl:
        gainers_raw, losers_raw = _split_pct(markets_panel, top_n=top_n)
        gainers = [_entry_summary(e, tradable_on=_tradable_for(e)) for e in gainers_raw]
        losers = [_entry_summary(e, tradable_on=_tradable_for(e)) for e in losers_raw]

    trending = [_entry_summary(e, tradable_on=_tradable_for(e)) for e in trending_panel[:top_n]]

    categories = [_category_summary(e) for e in categories_panel[: max(categories_top_n, 0)]]

    rate_limited = bool(markets_rl) or bool(trending_rl) or bool(categories_rl)
    note = ""
    if markets_rl:
        note = "[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]"
    elif categories_rl:
        note = "[MOVERS API RATE-LIMITED — categories unavailable this run]"
    if tradable_filter and kraken_cli_available is False and note:
        note = f"{note} [MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]"
    elif tradable_filter and kraken_cli_available is False:
        note = "[MOVERS KRAKEN CLI UNAVAILABLE — tradable_on field empty this run]"

    return {
        "fetched_at": fetched_at,
        "gainers": gainers,
        "losers": losers,
        "trending": trending,
        "categories": categories,
        "rate_limited": rate_limited,
        "attempts": {
            "gainers_losers": markets_attempts,
            "trending": trending_attempts,
            "categories": categories_attempts,
        },
        "note": note,
        "kraken_cli_available": kraken_cli_available,
        "tradable_filter": tradable_filter,
    }
