# ARB-BOT

> **If you are an LLM coding agent (Claude Code, Codex, etc.) opened against this repo for an audit task:** read [`AUDIT_PROMPT.md`](AUDIT_PROMPT.md) first. It contains your mission, the repo conventions, VM access details, the data-flow model, and the full audit checklist. Treat it as your contract.

Two prediction-market arbitrage bots side-by-side, each built from a different deep-research model's strategy synthesis. Both target the same market: **Polymarket (Polygon) × Limitless Exchange (Base)** crypto markets with a $500 starting bankroll.

## Audit / hand-off

This repo is also the source-of-truth for two paper-trading bots running on an OCI VM. The VM auto-syncs hourly:

- **Code:** GitHub → VM (`git pull`; bots rebuilt if source changed)
- **Runtime data:** VM → GitHub (`runtime/` directory contains current `Trade.json`, `bot.sqlite3` dump, log tails, Prometheus metrics)

So an audit doesn't need VM access — `git clone` gives you both the source and recent observed behavior. See [`AUDIT_PROMPT.md`](AUDIT_PROMPT.md) for details.

| Folder | Origin | Primary strategy file |
|---|---|---|
| [`ARB-BOT-Opus4.7/`](ARB-BOT-Opus4.7/) | Claude Opus 4.7 deep research | `src/strategies/yes_no_complementarity.py` |
| [`ARB-BOT-GPT5.5/`](ARB-BOT-GPT5.5/) | GPT-5.5 deep research | `src/strategies/yes_no_complementarity.py` |

Both bots:
- Run in Docker
- Phase 1 strategy = YES + NO complementarity on Limitless (single-venue, riskless)
- Honest dry-run / paper-trade mode (scans real markets, simulates fills)
- Persist trade data (`Trade.json` for Opus, `bot.sqlite3` for GPT5.5)

Per-bot deploy instructions live in each folder's `README.md`.

## Running both side-by-side

The two bots default to port 9090 for Prometheus metrics. To coexist on one host, remap one container's host port — see the `docker-compose.yml` in each folder. In this repo, `ARB-BOT-GPT5.5/docker-compose.yml` is already remapped to host port `9091:9090`.

## Patches applied to GPT5.5 bot (vs. its original source)

These are committed in the GPT5.5 tree; if you want to compare against pristine, diff against the original drop.

1. `src/config.py` — added `@field_validator("strategy_phase", mode="before")` to coerce `STRATEGY_PHASE` env-string `"1"` to int (Pydantic Literal type rejects strings by default).
2. `src/strategies/yes_no_complementarity.py`:
   - `scan()` — always scan live markets (orig used empty `dry_run_books` fixture when `DRY_RUN=true`); added per-tick INFO summary.
   - `_live_market_books()` — tolerate per-market fetch failures (orig raised on AMM markets with no orderbook); skip `/portfolio/profile` 404 in dry-run when no API key is configured.
   - `_book_from_market_detail()` — extract YES/NO asks from `market.tradePrices.buy.market[0/1]` since Limitless `/orderbook` returns only one token's book at a time.
   - `_extract_slugs_with_filter()` — pre-filter universe to BTC/ETH/SOL slugs before fetching detail.
3. `docker-compose.yml` — port remap `9091:9090` (for dual-deploy with the Opus bot).
4. `.env` — `VALIDATE_ENDPOINTS_ON_START=false` (default validator does strict full-URL substring matching against `llms.txt`; would need separate doc-parser work to fix properly).

## Bankroll context

Both bots default to `BANKROLL_USD=500`. Position sizing, concurrent-arb limits, daily/total stop-losses all scale from that. See each bot's `STRATEGY_SYNTHESIS.md` for the full risk table.

## What this repo is **not**

- Not financial advice. The strategy synthesis docs in each folder are honest about expected returns (single-digit dollars/week at $500).
- Not a license to run other people's keys. Both folders include `.env.example` only; never commit `.env`.
