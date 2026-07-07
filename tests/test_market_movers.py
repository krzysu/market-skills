"""Tests for skills/market-movers/.

The skill hits CoinGecko's public REST API. All transport is mocked so
the suite runs offline. The retry/backoff path is exercised by overriding
``_sleep`` so no test actually sleeps.

Per-fix fixtures:

  test_429_triggers_retry_then_degrades
      CoinGecko returns 429 on the gainers/losers endpoint; the skill
      retries 3 times with exponential backoff (1s/2s/4s), then
      escalates: ``gainers=[]``, ``losers=[]``, ``rate_limited=True``,
      and ``note`` carries the
      ``[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]``
      marker the morning-brief prompt reads. Trending stays populated
      because it's a separate path with a higher quota.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── import via the same loader the skill uses ────────────────────────────


def _load_movers_lib():
    """Load skills/market-movers/lib.py the same way ``analysis.skill_loader``
    does for skill composition. Tests should not assume ``skills.market_movers``
    is on sys.path — each skill is a standalone dir under skills/.
    """
    lib_path = Path(__file__).resolve().parent.parent / "skills" / "market-movers" / "lib.py"
    spec = importlib.util.spec_from_file_location("market_movers_lib_under_test", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── helpers ──────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


def _markets_payload(n: int = 10) -> list[dict]:
    """Build a synthetic /coins/markets response with ascending pct."""
    out = []
    for i in range(n):
        out.append(
            {
                "id": f"coin-{i}",
                "symbol": f"C{i}".lower(),
                "name": f"Coin {i}",
                "current_price": 100.0 + i,
                "price_change_percentage_24h": -50.0 + i * 10.0,
                "price_change_percentage_24h_in_currency": -50.0 + i * 10.0,
                "market_cap_rank": i + 1,
            }
        )
    return out


def _trending_payload(n: int = 5) -> dict:
    coins = []
    for i in range(n):
        coins.append(
            {
                "item": {
                    "id": f"trend-{i}",
                    "coin_id": i + 100,
                    "symbol": f"t{i}",
                    "name": f"Trend {i}",
                    "market_cap_rank": i + 100,
                }
            }
        )
    return {"coins": coins}


class SleepRecorder:
    """Records sleep calls without actually sleeping."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ── tests ────────────────────────────────────────────────────────────────


class TestGainersLosersFetch:
    def test_happy_path_returns_entries(self):
        movers_lib = _load_movers_lib()
        with patch.object(movers_lib.requests, "get") as mock_get:
            mock_get.return_value = FakeResponse(payload=_markets_payload(10))
            entries, attempts, rl = movers_lib.fetch_gainers_losers(sleeper=lambda s: None)

        assert attempts == 1
        assert rl is False
        assert len(entries) == 10

    def test_429_then_success_retries(self):
        movers_lib = _load_movers_lib()
        recorder = SleepRecorder()
        with patch.object(movers_lib.requests, "get") as mock_get:
            mock_get.side_effect = [
                FakeResponse(status_code=429),
                FakeResponse(payload=_markets_payload(3)),
            ]
            entries, attempts, rl = movers_lib.fetch_gainers_losers(sleeper=recorder)

        assert attempts == 2
        assert rl is False
        assert len(entries) == 3
        assert recorder.calls == [1.0]

    def test_sustained_429_marks_rate_limited(self):
        movers_lib = _load_movers_lib()
        recorder = SleepRecorder()
        with patch.object(movers_lib.requests, "get") as mock_get:
            mock_get.return_value = FakeResponse(status_code=429)
            entries, attempts, rl = movers_lib.fetch_gainers_losers(sleeper=recorder)

        assert attempts == 3
        assert rl is True
        assert entries == []
        # 1s, 2s backoffs between the 3 attempts (no sleep after the last).
        assert recorder.calls == [1.0, 2.0]


class TestFetchMovers:
    def test_clean_run(self):
        movers_lib = _load_movers_lib()
        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(_markets_payload(10), 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=([], 1, False),
            ),
            patch.object(
                movers_lib,
                "_fetch_kraken_pairs",
                return_value=({}, False),  # CLI absent — degrades gracefully
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None)

        assert payload["rate_limited"] is False
        # Kraken CLI absent (mocked) → "KRAKEN CLI UNAVAILABLE" note surfaces,
        # but no "API RATE-LIMITED" marker — those CoinGecko panels succeeded.
        assert "API RATE-LIMITED" not in payload["note"]
        assert "[MOVERS KRAKEN CLI UNAVAILABLE" in payload["note"]
        assert len(payload["gainers"]) == 7
        assert len(payload["losers"]) == 7
        assert len(payload["trending"]) == 5
        assert payload["categories"] == []
        assert payload["attempts"] == {"gainers_losers": 1, "trending": 1, "categories": 1}
        assert payload["kraken_cli_available"] is False
        assert payload["tradable_filter"] is True

    def test_429_triggers_retry_then_degrades(self):
        """Per-fix fixture: 429 on the gainers/losers endpoint.

        The morning-brief workflow called ``/coins/markets`` for
        gainers/losers/trending; on 429 the brief was delivered without
        the movers section — silently, no alert, no retry, no logging.
        With the fix: 3 retries (1s/2s/4s backoffs) → final 429 →
        ``gainers=[]``, ``losers=[]``, ``rate_limited=True``,
        ``note`` carries the explicit
        ``[MOVERS API RATE-LIMITED — gainers/losers unavailable this run]``
        marker the morning-brief prompt reads. Trending stays populated.
        """
        movers_lib = _load_movers_lib()
        recorder = SleepRecorder()
        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=([], 3, True),  # all retries exhausted
            ) as gl_mock,
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(7)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=([], 1, False),
            ),
            patch.object(
                movers_lib,
                "_fetch_kraken_pairs",
                return_value=({}, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=recorder)

        assert payload["rate_limited"] is True
        assert payload["gainers"] == []
        assert payload["losers"] == []
        assert "[MOVERS API RATE-LIMITED" in payload["note"]
        assert "gainers/losers unavailable" in payload["note"]
        assert len(payload["trending"]) == 7
        assert payload["attempts"]["gainers_losers"] == 3
        assert payload["attempts"]["trending"] == 1
        gl_mock.assert_called_once()

    def test_trending_429_only(self):
        movers_lib = _load_movers_lib()
        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(_markets_payload(10), 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=([], 3, True),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=([], 1, False),
            ),
            patch.object(
                movers_lib,
                "_fetch_kraken_pairs",
                return_value=({}, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None)

        assert payload["rate_limited"] is True
        assert len(payload["gainers"]) == 7
        assert len(payload["losers"]) == 7
        assert payload["trending"] == []
        # No "MOVERS RATE-LIMITED" note on gainers/losers — they came through.
        assert "gainers/losers unavailable" not in payload["note"]

    def test_categories_429_only(self):
        movers_lib = _load_movers_lib()
        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(_markets_payload(10), 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=([], 3, True),
            ),
            patch.object(
                movers_lib,
                "_fetch_kraken_pairs",
                return_value=({}, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None)

        assert payload["rate_limited"] is True
        assert payload["categories"] == []
        assert "[MOVERS API RATE-LIMITED — categories unavailable this run]" in payload["note"]
        assert "gainers/losers unavailable" not in payload["note"]
        assert len(payload["gainers"]) == 7
        assert payload["attempts"]["categories"] == 3


class TestRateLimitLog:
    def test_429_writes_to_rate_limit_log(self, monkeypatch, tmp_path):
        movers_lib = _load_movers_lib()
        # The path resolver puts the log at $XDG_DATA_HOME/market-skills/.
        log_path = tmp_path / "market-skills" / "coingecko-rate-limit.log"
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.delenv("MARKET_SKILLS_NO_RATE_LIMIT_LOG", raising=False)

        with patch.object(movers_lib.requests, "get", return_value=FakeResponse(status_code=429)):
            movers_lib.fetch_gainers_losers(retries=3, sleeper=lambda s: None)

        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert "/coins/markets" in record["endpoint"]
        assert record["attempts"] == 3
        assert record["final_status"] == 429

    def test_no_xdg_no_log(self, monkeypatch):
        """XDG_DATA_HOME unset → log silently skipped (no fallback path)."""
        movers_lib = _load_movers_lib()
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)

        with patch.object(movers_lib.requests, "get", return_value=FakeResponse(status_code=429)):
            movers_lib.fetch_gainers_losers(retries=2, sleeper=lambda s: None)

    def test_no_log_flag_disables_logging(self, monkeypatch, tmp_path):
        movers_lib = _load_movers_lib()
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("MARKET_SKILLS_NO_RATE_LIMIT_LOG", "1")

        with patch.object(movers_lib.requests, "get", return_value=FakeResponse(status_code=429)):
            movers_lib.fetch_gainers_losers(retries=2, sleeper=lambda s: None)

        log_path = tmp_path / "market-skills" / "coingecko-rate-limit.log"
        assert not log_path.exists()


class TestSplitAndSummary:
    def test_split_orders_gainers_desc_losers_asc(self):
        movers_lib = _load_movers_lib()
        markets = _markets_payload(10)  # pct ascends from -50 to +40
        gainers, losers = movers_lib._split_pct(markets, top_n=3)
        # Top-3 gainers = the 3 highest pct (ids 7,8,9 → +20, +30, +40).
        assert [g["id"] for g in gainers] == ["coin-9", "coin-8", "coin-7"]
        # Bottom-3 losers = the 3 lowest pct (ids 0,1,2 → -50, -40, -30).
        assert [item["id"] for item in losers] == ["coin-0", "coin-1", "coin-2"]

    def test_entry_summary_flattens_market_entry(self):
        movers_lib = _load_movers_lib()
        e = {
            "id": "bitcoin",
            "symbol": "btc",
            "name": "Bitcoin",
            "current_price": 62000.0,
            "price_change_percentage_24h_in_currency": 1.42,
            "market_cap_rank": 1,
        }
        s = movers_lib._entry_summary(e)
        assert s["id"] == "bitcoin"
        assert s["symbol"] == "BTC"
        assert s["pct_24h"] == 1.42
        assert s["price_usd"] == 62000.0
        assert s["market_cap_rank"] == 1

    def test_entry_summary_trending_variant(self):
        movers_lib = _load_movers_lib()
        e = {
            "id": "meme-1",
            "coin_id": 999,
            "symbol": "m1",
            "name": "Meme 1",
            "market_cap_rank": 50,
        }
        s = movers_lib._entry_summary(e)
        assert s["id"] == "meme-1"
        assert s["market_cap_rank"] == 50
        assert s["pct_24h"] is None  # trending entries don't carry pct

    def test_entry_summary_tradable_explicit_none_added(self):
        movers_lib = _load_movers_lib()
        e = {"id": "x", "symbol": "x", "name": "X"}
        # Explicit None → field surfaces as null in the output.
        s = movers_lib._entry_summary(e, tradable_on=None)
        assert "tradable_on" in s
        assert s["tradable_on"] is None

    def test_entry_summary_tradable_hit(self):
        movers_lib = _load_movers_lib()
        e = {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"}
        s = movers_lib._entry_summary(
            e,
            tradable_on={"kraken": True, "altname": "XBTUSD", "base": "XXBT", "quote": "ZUSD"},
        )
        assert s["tradable_on"]["kraken"] is True
        assert s["tradable_on"]["altname"] == "XBTUSD"


# ── Categories panel ─────────────────────────────────────────────────────


def _categories_payload(n: int = 5) -> list[dict]:
    """Build a synthetic /coins/categories response."""
    out = []
    for i in range(n):
        # Alternating + and - to exercise both directions.
        pct = (i + 1) * 2.0 if i % 2 == 0 else -((i + 1) * 2.0)
        out.append(
            {
                "id": f"cat-{i}",
                "name": f"Category {i}",
                "market_cap": 1_000_000_000 * (i + 1),
                "market_cap_change_24h": pct,
                "top_3_coins_id": [f"coin-{i}-a", f"coin-{i}-b", f"coin-{i}-c"],
            }
        )
    return out


class TestCategoriesPanel:
    def test_fetch_categories_happy(self):
        movers_lib = _load_movers_lib()
        payload = _categories_payload(5)
        with patch.object(movers_lib.requests, "get") as mock_get:
            mock_get.return_value = FakeResponse(payload=payload)
            entries, attempts, rl = movers_lib.fetch_categories(sleeper=lambda s: None)
        assert attempts == 1
        assert rl is False
        assert len(entries) == 5

    def test_fetch_categories_429(self):
        movers_lib = _load_movers_lib()
        recorder = SleepRecorder()
        with patch.object(movers_lib.requests, "get", return_value=FakeResponse(status_code=429)):
            entries, attempts, rl = movers_lib.fetch_categories(sleeper=recorder)
        assert attempts == 3
        assert rl is True
        assert entries == []

    def test_category_summary_trims_payload(self):
        movers_lib = _load_movers_lib()
        s = movers_lib._category_summary(
            {
                "id": "ai",
                "name": "AI",
                "market_cap": 12_000_000_000,
                "market_cap_change_24h": 12.34,
                "top_3_coins_id": ["a", "b", "c"],
                "irrelevant_field_we_drop": "noise",
            }
        )
        assert s["id"] == "ai"
        assert s["name"] == "AI"
        assert s["market_cap_usd"] == 12_000_000_000
        assert s["pct_24h"] == 12.34
        assert s["top_3_coins_id"] == ["a", "b", "c"]
        assert "irrelevant_field_we_drop" not in s

    def test_panel_integrated_into_fetch_movers(self):
        movers_lib = _load_movers_lib()
        cats = _categories_payload(3)
        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(_markets_payload(10), 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=(cats, 1, False),
            ),
            patch.object(
                movers_lib,
                "_fetch_kraken_pairs",
                return_value=({}, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, categories_top_n=3, sleeper=lambda s: None)

        assert len(payload["categories"]) == 3
        assert payload["categories"][0]["id"] == "cat-0"
        # All raw entries are summarised — only the surfaced keys are present.
        for cat in payload["categories"]:
            assert set(cat.keys()) == {"id", "name", "market_cap_usd", "pct_24h", "top_3_coins_id"}

    def test_categories_top_n_zero_skips_panel(self):
        movers_lib = _load_movers_lib()
        cats = _categories_payload(5)
        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(_markets_payload(10), 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=(cats, 1, False),
            ) as cat_mock,
            patch.object(
                movers_lib,
                "_fetch_kraken_pairs",
                return_value=({}, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, categories_top_n=0, sleeper=lambda s: None)

        # Even with top_n=0, the categories fetch must still happen so
        # rate-limit accounting is honest — the consumer trims after.
        cat_mock.assert_called_once()
        assert payload["categories"] == []


# ── Tradable filter (Kraken cross-reference) ─────────────────────────────


def _kraken_pairs_payload() -> dict:
    """Build a synthetic ``kraken pairs -o json`` response."""
    return {
        "XBTUSD": {
            "altname": "XBTUSD",
            "base": "XXBT",
            "quote": "ZUSD",
            "status": "online",
            "wsname": "XBT/USD",
        },
        "SOLUSD": {
            "altname": "SOLUSD",
            "base": "SOL",
            "quote": "ZUSD",
            "status": "online",
            "wsname": "SOL/USD",
        },
        "GRTUSD": {
            "altname": "GRTUSD",
            "base": "GRT",
            "quote": "ZUSD",
            "status": "online",
            "wsname": "GRT/USD",
        },
        "HYPEUSD": {
            "altname": "HYPEUSD",
            "base": "HYPE",
            "quote": "ZUSD",
            "status": "online",
            "wsname": "HYPE/USD",
        },
        "XBTUSDT": {
            "altname": "XBTUSDT",
            "base": "XXBT",
            "quote": "USDT",
            "status": "online",
            "wsname": "XBT/USDT",
        },
        "DELISTEDUSD": {
            "altname": "DELISTEDUSD",
            "base": "DELISTED",
            "quote": "ZUSD",
            "status": "delisted",  # not "online" — must be filtered out
            "wsname": "DELISTED/USD",
        },
    }


def _completed_pair_process(stdout: str, returncode: int = 0) -> MagicMock:
    """Build a stand-in for ``subprocess.CompletedProcess``."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


class TestKrakenPairsFetch:
    def test_happy_path_parses_pairs(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()
        payload = json.dumps(_kraken_pairs_payload())

        def runner(args, timeout_s):
            return _completed_pair_process(payload)

        pairs_dict, cli_ok = movers_lib._fetch_kraken_pairs(runner=runner)
        assert cli_ok is True
        assert "XBTUSD" in pairs_dict
        # The raw response keeps the delisted row (it's the indexer
        # step's job to filter). Both statuses present.
        statuses = {p.get("status") for p in pairs_dict.values()}
        assert statuses == {"online", "delisted"}

    def test_index_drops_delisted_pairs(self):
        movers_lib = _load_movers_lib()
        pairs = _kraken_pairs_payload()
        indexed = movers_lib._index_pairs_by_base(pairs)
        assert "DELISTED" not in indexed
        # All indexed bases are online only.
        for base, pair_list in indexed.items():
            assert all(p.get("status") == "online" for p in pair_list), base

    def test_cli_missing_marks_unavailable(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()

        def runner(args, timeout_s):
            raise FileNotFoundError("kraken not on PATH")

        pairs_dict, cli_ok = movers_lib._fetch_kraken_pairs(runner=runner)
        assert cli_ok is False
        assert pairs_dict == {}

    def test_cli_timeout_treated_as_unavailable_payload(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()

        def runner(args, timeout_s):
            raise subprocess.TimeoutExpired(cmd="kraken", timeout=timeout_s)

        pairs_dict, cli_ok = movers_lib._fetch_kraken_pairs(runner=runner)
        # CLI is "available" (the binary exists) but the call timed out —
        # we return an empty dict so the tradable field degrades to null.
        assert cli_ok is True
        assert pairs_dict == {}

    def test_unparseable_json_returns_empty(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()

        def runner(args, timeout_s):
            return _completed_pair_process("not json")

        pairs_dict, cli_ok = movers_lib._fetch_kraken_pairs(runner=runner)
        assert cli_ok is True
        assert pairs_dict == {}

    def test_response_envelope_unwrapped(self):
        """CLI shape: ``{'result': {'XBTUSD': {...}}}`` (REST envelope)."""
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()
        payload = json.dumps({"result": _kraken_pairs_payload()})

        def runner(args, timeout_s):
            return _completed_pair_process(payload)

        pairs_dict, cli_ok = movers_lib._fetch_kraken_pairs(runner=runner)
        assert cli_ok is True
        assert "XBTUSD" in pairs_dict

    def test_ttl_cache_skips_repeated_calls(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()
        payload = json.dumps(_kraken_pairs_payload())
        calls = []

        def runner(args, timeout_s):
            calls.append(args)
            return _completed_pair_process(payload)

        # first call → cache miss → runner invoked
        movers_lib._fetch_kraken_pairs(runner=runner, ttl_s=600, now_s=lambda: 1000.0)
        # second call within TTL → cache hit → runner NOT invoked
        movers_lib._fetch_kraken_pairs(runner=runner, ttl_s=600, now_s=lambda: 1010.0)
        # third call past TTL → cache miss again
        movers_lib._fetch_kraken_pairs(runner=runner, ttl_s=600, now_s=lambda: 1700.0)
        assert len(calls) == 2

    def test_ttl_cache_invalidation_via_reset(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()
        payload = json.dumps(_kraken_pairs_payload())
        calls = []

        def runner(args, timeout_s):
            calls.append(args)
            return _completed_pair_process(payload)

        movers_lib._fetch_kraken_pairs(runner=runner)
        movers_lib._reset_kraken_pairs_cache_for_test()
        movers_lib._fetch_kraken_pairs(runner=runner)
        assert len(calls) == 2


class TestTradableLookup:
    def test_id_based_resolution(self):
        movers_lib = _load_movers_lib()
        pairs = _kraken_pairs_payload()
        pairs_index = movers_lib._index_pairs_by_base(pairs)
        # Bitcoin: id-mapped to XXBT (not symbol-collide with another BTC).
        result = movers_lib._lookup_tradable("bitcoin", "BTC", pairs_index)
        assert result["kraken"] is True
        assert result["base"] == "XXBT"
        assert result["quote"] == "ZUSD"
        assert result["altname"] == "XBTUSD"

    def test_symbol_based_resolution(self):
        movers_lib = _load_movers_lib()
        pairs = _kraken_pairs_payload()
        pairs_index = movers_lib._index_pairs_by_base(pairs)
        # SOL — not in the id map, must resolve via symbol fallback.
        result = movers_lib._lookup_tradable("solana", "SOL", pairs_index)
        assert result["kraken"] is True
        assert result["base"] == "SOL"

    def test_symbol_collision_id_override(self):
        movers_lib = _load_movers_lib()
        pairs = _kraken_pairs_payload()
        pairs_index = movers_lib._index_pairs_by_base(pairs)
        # A low-cap coin literally named "BTC" but with id="btc-2" — must
        # NOT collide with bitcoin's XXBT mapping. The id is not in the
        # map, so the symbol fallback runs. The fallback tries "BTC" →
        # "XBTC" → "XXBTC" — none of those are bases in the index, so
        # we get a clean {"kraken": False} answer (no false-positive
        # binding to bitcoin's XXBT).
        result = movers_lib._lookup_tradable("btc-2", "BTC", pairs_index)
        assert result["kraken"] is False

    def test_unknown_symbol_returns_false(self):
        movers_lib = _load_movers_lib()
        pairs = _kraken_pairs_payload()
        pairs_index = movers_lib._index_pairs_by_base(pairs)
        result = movers_lib._lookup_tradable("nope-coin", "ZZZZ", pairs_index)
        assert result is not None
        assert result["kraken"] is False

    def test_empty_index_returns_none(self):
        """Empty index (CLI unavailable) → tradable lookup returns None
        so the entry surface as ``tradable_on: null``."""
        movers_lib = _load_movers_lib()
        result = movers_lib._lookup_tradable("bitcoin", "BTC", {})
        assert result is None

    def test_quote_preference(self):
        movers_lib = _load_movers_lib()
        # SOL pairs with USD, USDT, EUR — preference order should pick USD first.
        pairs = {
            "SOLEUR": {"altname": "SOLEUR", "base": "SOL", "quote": "ZEUR", "status": "online"},
            "SOLUSDT": {"altname": "SOLUSDT", "base": "SOL", "quote": "USDT", "status": "online"},
            "SOLUSD": {"altname": "SOLUSD", "base": "SOL", "quote": "ZUSD", "status": "online"},
        }
        pairs_index = movers_lib._index_pairs_by_base(pairs)
        result = movers_lib._lookup_tradable("solana", "SOL", pairs_index)
        assert result["quote"] == "ZUSD"


class TestTradableFilterIntegration:
    def test_happy_path_populates_tradable_on(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()
        # Build CoinGecko markets with bitcoin + solana + a non-Kraken coin.
        markets = [
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 62000.0,
                "price_change_percentage_24h": 1.0,
                "market_cap_rank": 1,
            },
            {
                "id": "solana",
                "symbol": "sol",
                "name": "Solana",
                "current_price": 150.0,
                "price_change_percentage_24h": 5.0,
                "market_cap_rank": 5,
            },
            {
                "id": "mystery-token",
                "symbol": "mtk",
                "name": "Mystery",
                "current_price": 0.01,
                "price_change_percentage_24h": 50.0,
                "market_cap_rank": 9999,
            },
        ]
        payload_json = json.dumps(_kraken_pairs_payload())

        def runner(args, timeout_s):
            return _completed_pair_process(payload_json)

        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(markets, 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(3)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=(_categories_payload(3), 1, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None, kraken_runner=runner)

        assert payload["kraken_cli_available"] is True
        # All three CoinGecko entries surfaced with tradable_on populated.
        flat_entries = payload["gainers"] + payload["losers"] + payload["trending"]
        with_tradable = [e for e in flat_entries if "tradable_on" in e]
        assert len(with_tradable) >= 3
        # At least one entry should be tradable on Kraken (BTC).
        kraken_tradable = [e for e in with_tradable if (e.get("tradable_on") or {}).get("kraken") is True]
        assert any(e["id"] == "bitcoin" for e in kraken_tradable)
        # The mystery token has no Kraken listing → {"kraken": False}.
        mystery = next((e for e in with_tradable if e["id"] == "mystery-token"), None)
        assert mystery is not None
        assert mystery["tradable_on"] == {"kraken": False}

    def test_cli_missing_all_entries_get_none(self):
        movers_lib = _load_movers_lib()
        movers_lib._reset_kraken_pairs_cache_for_test()

        def runner(args, timeout_s):
            raise FileNotFoundError("kraken not on PATH")

        markets = _markets_payload(10)

        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(markets, 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=(_categories_payload(3), 1, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None, kraken_runner=runner)

        assert payload["kraken_cli_available"] is False
        assert "[MOVERS KRAKEN CLI UNAVAILABLE" in payload["note"]
        # Every surfaced entry has tradable_on: null.
        for entry in payload["gainers"] + payload["losers"] + payload["trending"]:
            assert entry.get("tradable_on") is None

    def test_tradable_filter_disabled_omits_field(self):
        """--no-tradable-filter path → field omitted entirely from entries."""
        movers_lib = _load_movers_lib()
        markets = _markets_payload(10)

        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(markets, 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=(_categories_payload(3), 1, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None, tradable_filter=False)

        assert payload["tradable_filter"] is False
        assert payload["kraken_cli_available"] is None
        # No entry carries the field.
        for entry in payload["gainers"] + payload["losers"] + payload["trending"]:
            assert "tradable_on" not in entry

    def test_kraken_unavailable_does_not_block_other_panels(self):
        movers_lib = _load_movers_lib()
        markets = _markets_payload(10)

        def runner(args, timeout_s):
            raise FileNotFoundError("kraken not on PATH")

        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(markets, 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=(_categories_payload(3), 1, False),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None, kraken_runner=runner)

        # Gainers, losers, trending, categories all still populated.
        assert len(payload["gainers"]) == 7
        assert len(payload["losers"]) == 7
        assert len(payload["trending"]) == 5
        assert len(payload["categories"]) == 3
        # Only the tradable_on field is degraded.
        assert "[MOVERS KRAKEN CLI UNAVAILABLE" in payload["note"]
        assert "API RATE-LIMITED" not in payload["note"]


class TestCandidateBases:
    def test_modern_symbol(self):
        movers_lib = _load_movers_lib()
        cands = movers_lib._candidate_bases_from_symbol("SOL")
        assert cands[0] == "SOL"
        assert "XSOL" in cands
        assert "XXSOL" in cands

    def test_legacy_symbol_strip_prefixes(self):
        movers_lib = _load_movers_lib()
        cands = movers_lib._candidate_bases_from_symbol("BTC")
        # BTC → BTC, XBTC, XXBTC — all candidates.
        assert "BTC" in cands
        assert "XBTC" in cands
        assert "XXBTC" in cands

    def test_empty_symbol_returns_empty(self):
        movers_lib = _load_movers_lib()
        assert movers_lib._candidate_bases_from_symbol("") == []


class TestCategorAndTradableStacking:
    """Bugs to guard against: the order of elif branches in
    fetch_movers' note-builder can let a rate-limit marker overwrite
    a KRAKEN CLI UNAVAILABLE marker, or vice versa."""

    def test_categories_429_with_kraken_cli_missing_stacks_both(self):
        movers_lib = _load_movers_lib()

        def runner(args, timeout_s):
            raise FileNotFoundError("kraken not on PATH")

        with (
            patch.object(
                movers_lib,
                "fetch_gainers_losers",
                return_value=(_markets_payload(10), 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_trending",
                return_value=(_trending_payload(5)["coins"], 1, False),
            ),
            patch.object(
                movers_lib,
                "fetch_categories",
                return_value=([], 3, True),
            ),
        ):
            payload = movers_lib.fetch_movers(top_n=7, sleeper=lambda s: None, kraken_runner=runner)

        assert payload["rate_limited"] is True
        assert "[MOVERS API RATE-LIMITED — categories unavailable this run]" in payload["note"]
        assert "[MOVERS KRAKEN CLI UNAVAILABLE" in payload["note"]


# `subprocess` is referenced via the timeout test fixture; the import
# lives at the top of this file with the other stdlib imports.
