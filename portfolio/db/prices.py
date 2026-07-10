"""Asset price cache — fetch, cache, retrieve current prices."""

import sys
from datetime import UTC, datetime

from portfolio.db.schema import get_db


def get_cached_prices(db_path: str) -> dict[str, float]:
    conn = get_db(db_path)
    rows = conn.execute("SELECT asset, price FROM price_cache").fetchall()
    conn.close()
    return {r["asset"]: r["price"] for r in rows}


def refresh_prices(db_path: str) -> dict[str, float]:
    from analysis.data import fetch_ohlc, fetch_spot_price

    conn = get_db(db_path)
    assets = [r[0] for r in conn.execute("SELECT DISTINCT asset FROM transactions").fetchall()]
    conn.close()

    now_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    stale_assets: list[str] = []

    for asset in assets:
        if ":" not in asset:
            continue

        spot = fetch_spot_price(asset)
        if spot:
            prices[asset] = spot["price"]
            sources[asset] = spot.get("source", "spot")
            continue

        candles = fetch_ohlc(asset)
        if candles:
            prices[asset] = candles[-1][4]
            sources[asset] = "ohlc:close"
            stale_assets.append(asset)

    if stale_assets:
        print(
            f"refresh_prices: {len(stale_assets)} asset(s) fell back to stale OHLC close "
            f"(no live spot available): {', '.join(sorted(stale_assets))}",
            file=sys.stderr,
        )

    conn = get_db(db_path)
    for asset, price in prices.items():
        conn.execute(
            "INSERT OR REPLACE INTO price_cache (asset, price, ts, source) VALUES (?, ?, ?, ?)",
            (asset, price, now_ts, sources.get(asset, "analysis.data")),
        )
    conn.commit()
    conn.close()

    return prices
