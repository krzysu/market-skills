# Multi-source discovery: design pattern for opportunity-finding crons

## When to use this pattern

Any analysis cron whose job is to surface actionable candidates from a defined universe should follow this pattern when:
- The user has expressed that the current scan is "too quiet" / "too restrictive" / "find more trades"
- The universe currently covers only the most-trusted assets (tier 1 / thesis universe)
- The journal shows the cron produces mostly SILENT outputs over consecutive days
- The cron has access to MCPs (surf-mcp, coingecko-mcp, nansen-mcp) it isn't using

Don't apply this pattern when the user wants a quiet scan (intentional filter, e.g. "only trade what I have notes on"). The principle is: when silence decays trust, expand the universe; when the user wants silence, don't override.

## The 6-source framework

| # | Source | Class | Conv gate | Sizing cap | Tool |
|---|--------|-------|-----------|------------|------|
| 1 | Thesis universe (tier 1+2) | established thesis, has notes | ≥3 | full budget (EUR 200) | market-skills run-all-l3 |
| 2 | Swing shortlist (tier 3, proven scanner-bait) | trial thesis | ≥4 (stricter) | half budget (EUR 100) | market-skills run-all-l3 |
| 3 | External movers (CoinGecko gainers) | discovery | ≥4 + structural signal | half budget (EUR 100) | market-skills `skills/market-movers/scripts/run.py --json --top-n=15 --retries=3` (returns `tradable_on.altname` ready for execution) |
| 4 | Smart money accumulation (paid) | discovery | ≥3 + L3 confirms | half budget (EUR 100); max 2/day | nansen-mcp smart-money labels |
| 5 | Venue-native narrative (Hyperliquid-only tokens) | discovery, perp-only | ≥3 + L3 confirms | small (EUR 50 notional) | `curl -s https://api.hyperliquid.xyz/info -d '{"type":"meta"}'` filtered by 24h volume |

Sources 1 and 2 use the existing market-skills pipeline. Sources 3-5 widen the universe. **No separate cross-check source** — market-skills `market-snapshot` (RSI + Supertrend + MA alignment) is the cross-check applied to every candidate, regardless of source.

**Source 3 — use the market-movers skill, not inline curl.** The market-movers skill already has: CoinGecko fetch with retry/backoff (1s/2s/4s), Kraken AssetPairs cross-reference, stablecoin/meme filters, top-N panel sizing (`--top-n=15` widens from default 7), categories panel for sector rotation. Its output has `tradable_on: {kraken: true, altname: "XBTUSD", base: "XXBT", quote: "ZUSD"}` per entry — ready to drop directly into `execution-kraken-spot submit`. Do NOT reinvent this with `curl https://api.coingecko.com/...` and a separate Kraken pair lookup. Wasted code, double rate-limit exposure, and the tradability field would have to be reimplemented anyway.

**Surf-MCP cross-check retired 2026-06-29.** Surf-MCP cross-check (RSI + mindshare decline) was replaced with market-skills `market-snapshot`. Same effect for the overbought-RSI check (skip if RSI > 80). No mindshare check — that's a surf-mcp signal not in market-skills, and the user directive 2026-06-29 was "use what market-skills provides; MCPs only where market-skills doesn't have coverage." RSI is covered; mindshare is dropped.

**Generalization principle (added 2026-06-29):** before adding new logic for a discovery source, check if market-skills already provides it. CoinGecko movers + Kraken tradability is in market-movers. Discovery-sector rotation might be in market-skills strategies. If it is, use the skill — don't reinvent with curl + LLM parsing.

## Five operating principles

### 1. The job is to find actionable candidates; silence is failure

An analysis cron's job is to find, surface, rank. The discipline lives in the bar and the journal, not in design choices that make the cron shut up. If the journal shows 3+ consecutive days of nothing actionable from the existing universe, that's signal to **expand the universe**, not to keep silent. The user's framing on 2026-06-29 was direct: "your job is to make money, not to tell me nothing is actionable, your job is to find actionable trades."

This is a tension to be transparent about: a high-quality scan CAN be quiet on real grounds (the market doesn't always have setups). The test is whether silence is **reflecting reality** or **hiding opportunity in un-scanned corners**. Track consecutive SILENT ticks in the journal and trigger a universe-expansion review at 3+.

### 2. Cap is on output, not on evaluation

Surface the top N picks (default 3) by R:R. Journal every idea that meets the bar, even the ones over the cap (status: open, picked: false). The cap prevents Telegram spam and respects the user's attention budget; the journal is the calibration data and makes the cap decision auditable.

If a single source produces 5+ candidates in one tick, auto-downgrade to top 3 by R:R and emit `[INFO] source <name> produced N candidates — top 3 by R:R`. One noisy source (e.g. a pumping meme) must not drown quieter sources.

### 3. Per-source conviction gates, not uniform bar relaxation

When expanding the universe, the existing source's bar does NOT relax. New sources get a STRICTER gate (≥4 for new source, ≥3 for proven thesis universe). The proven thesis universe keeps the looser bar because it has more context (notes, invalidation conditions, journal history). New sources need higher conviction to overcome the absent context.

Anti-pattern: changing the existing bar from conv ≥3 to conv ≥2 to get more candidates. That lowers signal quality everywhere. Always add new tickers as a higher gate, never lower the existing one.

### 4. Per-source sizing

Same principle, applied to position sizing:
- Proven thesis universe: full budget (EUR 200) — context is real, you've done notes work
- Swing shortlist: half budget (EUR 100) — tier 3 = unproven thesis, testing it
- External movers: half budget (EUR 100) — discovery on assets not in your notes yet
- Smart money: half budget (EUR 100); max 2 ideas/day — paid MCP means cost discipline too
- Venue-native narrative: smallest (EUR 50 notional, perp only) — non-Kraken assets, thinnest context

This bounds the downside of acting on a thesis you haven't done notes work on, and creates a clear path from "external discovery at half size" to "thesis universe at full size" once the asset has earned its keep.

### 5. Market-snapshot cross-check on every candidate (the equalizer)

Every idea, regardless of source, gets a final filter pass before counting as a pick. Use `skills/market-snapshot/scripts/run.py <TICKER> --json` (default interval 4h):

- Skip if RSI(14) > 80 (overbought, late entry)
- For long ideas on perps, skip if supertrend flipped bearish (momentum turning against the thesis)

This catches the case where L3 says LONG but price action is overbought or supertrend is flipping. Both are common failure modes for late-move picks. The cross-check is what separates "L3 says it would have been a good setup 3 days ago" from "L3 says it's a good setup NOW."

Market-snapshot is fast (single fetch + composes 3 indicators) and free; run it on every candidate regardless of source. Don't make it optional.

**Note on social-mindshare check (dropped 2026-06-29).** Old draft referenced surf-mcp social mindshare (24h change) as a cross-check. That was replaced with market-snapshot since surf-mcp is no longer in the toolchain priority. If market-skills later ships a social-mindshare indicator, re-add the check.

## Failure modes and how this pattern handles them

| Failure mode | What this pattern does | Why it works |
|---|---|---|
| "Too quiet, user trust decays" | Expands universe (sources 3-5) with per-source gating | More candidates without lowering bar anywhere |
| "Picked a meme trending on CoinGecko and got burned" | Source 3 has half sizing + cross-check | Bounded downside on discovery-only picks |
| "Surf-MCP cross-check makes it slower" | Surf-mcp is fast; run it in parallel with L3 batch | Cost is negligible; signal quality is high |
| "Tier 3 candidate outperforms tier 1" | Tier 3 sizing starts at half; promote to tier 2 if hit rate proves out | Graduated exposure, not all-or-nothing |
| "User complains bar is too tight" | Check journal — is silence real or hiding opportunity? | Separate "market has no setups" (real) from "scan is too narrow" (fix) |

## Anti-patterns

- **Tier 1 only with strict bar.** Trust decays after 3 days of "nothing actionable." The market is full of setups; tier 1 just doesn't always have them.
- **Uniform bar relaxation when expanding universe.** Don't change conv ≥3 to conv ≥2 to surface more. Keep the proven-source bar tight and add a stricter new-source bar instead.
- **Discovery sources without per-source sizing.** Acting on a Coingecko mover with full budget size = jumping on a trending ticker without position-sizing discipline.
- **Surf-MCP cross-check as optional.** It catches the overbought/late-entry cases L3 fires on. Run it on every candidate.
- **Adding sources without notes work afterwards.** A new ticker picked via discovery source needs a market-note within 48 hours or the next scan shouldn't trust the lack of context.

## Reference implementation

Daily Trade Pick cron (`092ace8cd3ff`) on 2026-06-29 used this pattern for the first time:
- Six sources (tier 1+2 + swing shortlist + Coingecko + Nansen + HL narrative + surf-mcp cross-check)
- Per-source conviction gates (≥3 for tier 1+2, ≥4 for swing shortlist and CoinGecko, ≥3+Nansen confirmation for smart money, ≥3+L3 confirmation for HL narrative)
- Top-3 cap with per-source sizing
- Surf-mcp cross-check on every candidate
- Schema gained a `source` field on every journal entry

Cron prompt source of truth: the cron job prompt file (resolved via the host's cron job config; job_id `092ace8cd3ff`). This document captures the design rationale and is the reference for any future scan-cron implementation (Morning Brief tier-3 expansion, External Scanner multi-source upgrade, Watchlist Monitor addition).

## When to extend this further

Add a 7th source (e.g. on-chain smart-contract events, GitHub commit activity for AI tokens) only when:
- The current 6 sources' calibration data shows diminishing returns on the existing set
- The user explicitly asks for the new signal type
- Notes/invalidation criteria exist or will be created within 48h

Don't add sources speculatively. Each new source increases evaluation cost and adds a per-source sizing decision; the cost is worth it only when it fills a real gap.

## Related skills and references

- `daily-trade-pick/SKILL.md` — the operational contract, references this design doc
- `agent-improvements-audit/SKILL.md` — durable log of changes; new source additions get an audit entry
- `agent-improvement-loop` — the cron-improvement feedback workflow that surfaced the 2026-06-29 expansion
- `trading-vault` — strategy-level lessons (separate from cron design)
- Reference: the originating profile's `data/agent-improvements/2026-06-29-*.md` for the audit entry of the first multi-source activation
