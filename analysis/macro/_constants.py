"""Shared constants for analysis.macro submodules."""

# Alternative.me Fear & Greed — free, no key.
_FNG_URL = "https://api.alternative.me/fng/"
_FNG_TIMEOUT_S = 5

# CoinGecko /global — free, no key, ~10-30 req/min on the public tier.
_COINGECKO_URL = "https://api.coingecko.com/api/v3/global"
_COINGECKO_TIMEOUT_S = 5
_COINGECKO_UA = "market-skills/0.1 (https://github.com/krzysu/market-skills)"

# yfinance tickers
_YF_VIX = "^VIX"
_YF_DXY = "DX-Y.NYB"
_YF_US10Y = "^TNX"
_YF_BTC_MCAP = "BTC-USD"

# Source labels used in errors[] / history.
_LABEL_FNG = "fng"
_LABEL_VIX = "vix"
_LABEL_DXY = "dxy"
_LABEL_US10Y = "us10y"
_LABEL_BTC_MCAP = "btc_mcap"
_LABEL_COINGECKO = "coingecko"

# Default TTL on the in-process cache.
DEFAULT_TTL_SECONDS = 300

# History ring-buffer cap (mirrors analysis.chop).
_HISTORY_CAP = 200
_HISTORY_FILENAME = "macro_history.json"
