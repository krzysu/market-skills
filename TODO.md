# TODO

## Asset context resolver

**Status:** Deferred (was built and reverted in session — network plumbing was deemed too heavy for this layer)

**Goal:** Ground-truth token identity (chain, category, sector peers, market-cap tier) injected into LLM prompts at the agent layer, so callers don't hallucinate ticker context per skill.

**Source spec:** TradingAgents-inspired — `Resolve Instrument Context` is the first node in the agent graph, cached and threaded through every downstream step.

**Why deferred:** The repo is deterministic math (L1/L2/L3 indicators) with no LLM dependency. Adding CoinGecko + YFinance network calls into this package:
- Adds runtime dep on external APIs (rate limits, timeouts, schema drift) inside a math layer
- Breaks the "skills are pure functions of candles + ticker" invariant
- Complicates testing (network mocking, fixture invalidation)
- The actual consumer (Hermes/Horizon cron prompts) is the right home — it already has LLM calling infrastructure

**Re-attempt criteria:**
- A consumer (cron / agent) has a concrete prompt failure that token context would fix
- Or: this is split into a separate `asset-context` package with its own dependency boundary

**Discarded approaches:**
- Static map only (committed in `e5f40b6`, reverted) — too small surface area vs maintenance
- CoinGecko + YFinance inside `analysis/instrument_context.py` (uncommitted, reverted) — wrong layer
