# arb-edge-logger

**Purpose:** measure whether arbitrage edge actually exists between Limitless and Polymarket, before building any more bots. This is a passive observer — no trading.

## What it does (plain English)

Every 30 seconds:
1. Asks Limitless and Polymarket for the current order books on every active crypto market they have (BTC, ETH, SOL, XRP, DOGE, and more — auto-discovered).
2. For each market, computes "if I tried to deploy $25, $100, $500, $1000 right now, what would I actually pay walking the full orderbook?"
3. Subtracts all realistic costs: venue fees (pulled live from each market's metadata, not hard-coded), oracle-mismatch haircut, and depth penalties.
4. Writes the raw numbers to SQLite. No filtering. Every observation is recorded.

After a week of running, the `analyze.py` script tells you:
- How often was naive top-of-book arb visible? (the false-positive number)
- How often did it survive realistic costing? (the honest number)
- Which markets / sizes / time-windows produced repeatable positive edge?

## What it does NOT do

- No trading. No `.env` with private keys. Read-only HTTP.
- No filtering at logging time. Every observation lands in the DB — re-analyze with different thresholds anytime.
- No assumptions. When a fee field is missing, it falls back to the documented worst-case, not zero.
- No magic asset matching. Cross-venue pairs require: same asset, direction within 0.5%, strike within 1%, deadline within 5 min, AND both oracles in the objective set (Chainlink × Pyth same asset).

## Precision guarantees

1. **Self-test runs before the logger starts.** 18 synthetic tests prove the math is right. If any fail, the container refuses to come up.
2. **Both venues fetched simultaneously** via `asyncio.gather()`, each response timestamped. Cross-venue observations record the millisecond skew; observations with skew > 100ms are flagged unreliable and excluded from "edge exists" claims (but still recorded so you can see how often this happens).
3. **Full orderbook depth walked** — not just top-of-book. The `walk_asks_for_usd_buy` function eats through levels in order, returns weighted average and flags `depth_exhausted` if the book ran out before the target size was filled.
4. **Two answers always computed:** naive (top-of-book) and realistic (walked). Analysis shows both side-by-side so you can see exactly how much edge is illusion.
5. **Source attribution on every fee.** Each fee number includes a `fees_source` string explaining where it came from (e.g. `limitless_market_meta_bps=100` vs `limitless_curve_fallback_buy_pct=2.480`). You can audit every cost.

## Files

| File | Purpose |
|------|---------|
| `math_core.py` | Pure functions: book walking, fee math, edge calculation. No I/O. |
| `selftest.py` | 18 synthetic tests proving math_core is correct |
| `discover.py` | Finds active markets, classifies asset/strike/deadline/oracle, writes `pairs.json` |
| `logger.py` | Main loop; polls books, walks depth, stores observations |
| `analyze.py` | Queries SQLite, produces the report |
| `Dockerfile`, `docker-compose.yml`, `entrypoint.sh` | Containerization |

## Quick start

```bash
docker compose up -d --build
docker compose logs -f
```

Wait at least a few hours (preferably a week) for meaningful data to accumulate.

Then analyze:

```bash
docker compose exec arb-edge-logger python -m analyze
docker compose exec arb-edge-logger python -m analyze --min-obs 50 --size 100
```

The DB at `./data/edge_observations.sqlite3` is host-mounted. Survives container restarts.

## Outputs

- `data/pairs.json` — current universe (refreshes hourly)
- `data/edge_observations.sqlite3` — all raw observations
- Docker logs — one-line cycle summary every 30s

## How to read the analysis

When you run `analyze.py`, you get three sections:

1. **Naive vs Realistic** — answers "how much of the visible edge is illusion?" If naive_pos is high and net_pos is zero, that's confirmation that paper-trading bots will lie to you, exactly as feared.
2. **Single-venue YES+NO** — most likely place to find real edge, because there's no cross-chain risk. If repeatable edge exists at $100+ size, it's worth trading.
3. **Cross-venue** — the harder case. Look at the skew distribution; if <50% of observations are skew-reliable, your retail latency is fundamentally too slow for this strategy.

## When to stop and when to keep going

- Realistic edge at $100+ is positive >5% of the time across hundreds of observations: **real signal, build a bot**.
- Realistic edge is positive only at $25 size and disappears at $100: **edge exists in theory but capacity is too small to matter** (skip).
- Realistic edge is negative or near-zero across all sizes: **edge is illusion, you've saved months**.
- Realistic edge looks great but `depth_ok = False` most of the time: **the orderbook is too thin to actually fill**, fake signal.

## Tuning

Environment variables (in `docker-compose.yml`):

| Var | Default | What |
|-----|---------|------|
| `LOGGER_POLL_INTERVAL_SEC` | 30 | Seconds between full cycles |
| `DISCOVERY_REFRESH_SEC` | 3600 | How often to re-discover pairs |
| `SIZE_LADDER_USD` | 25,100,500,1000 | Notional sizes evaluated per observation |

Stricter skew threshold? Edit `SKEW_RELIABLE_THRESHOLD_MS` in `math_core.py`. Looser oracle haircut? Edit `ORACLE_HAIRCUT_PCT_DEFAULT`.

## Coexistence

This service runs alongside `ARB-BOT-Opus4.7` and `ARB-BOT-GPT5.5` on the same VM. They all hit the same Limitless API. Limitless rate limit is ~3 req/s; at 30s intervals across ~30 markets with concurrency 10, we use ~10 req/cycle = ~0.3 req/s sustained. Combined with the bots' ~0.1 req/s, we're well under budget.

No port conflict (this service exposes no ports).
