"""market-watchlist — asset registry: named baskets of tickers + metadata.

Pure re-export of `analysis.watchlist` so other skills can load via
`analysis.skill_loader.load_skill("market-watchlist")` without path gymnastics.

Storage layout: `skills/market-watchlist/data/watchlist.json` (gitignored).
File format: `{baskets: {<name>: {<ticker>: {metadata}}}}`.

Library entry points (all in `analysis.watchlist`):
    from analysis.watchlist import all_tickers, by_category, basket, categories
    from analysis.watchlist import metadata_for, provider_for, resolve, expand_tickers
"""

from analysis.watchlist import (
    all_tickers,
    basket,
    by_category,
    categories,
    default_path,
    expand_tickers,
    get_baskets,
    load_raw,
    metadata_for,
    provider_for,
    resolve,
    save_raw,
    validate_storage,
)

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
