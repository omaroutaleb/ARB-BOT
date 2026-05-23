# STRATEGY_SYNTHESIS.md

> **Contract document.** Every implementation decision in this codebase is grounded in this synthesis.
> Both research reports (`research/Opus4.7-Deepresearch.md`, `research/GPT5.5-Deepresearch.md`) were read in full.
> Where they agree, treated as ground truth. Where they disagree, the more conservative/risk-averse option was selected
> and both views are cited in the relevant code comments.
>
> **Bankroll context:** $500. This number governs every threshold below.

---

## Part 1 — Concrete technical decisions extracted from both reports

### 1.1 Platforms and chains

| Item | Decision | Source |
|---|---|---|
| Polymarket chain | Polygon (chainId 137) | Both reports |
| Polymarket collateral | pUSD (post-CLOBv2, April 28 2026), 1:1 backed USDC on Polygon | Opus §B; GPT5.5 §Operational |
| Limitless chain | Base (chainId 8453) | Both reports |
| Limitless collateral | USDC on Base, contract `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, 6 decimals | Opus §B |
| Market mechanism (Poly) | Hybrid CLOB (off-chain match, on-chain CTF Exchange V2 / Neg Risk CTF Exchange V2) | Both reports |
| Market mechanism (Lim) | Hybrid CLOB + AMM, CLOB uses per-market `venue.exchange` | Both reports |

### 1.2 Authentication

**Polymarket — two-level auth:**
1. Hold Polygon EOA private key, OR deposit-wallet `signatureType=3 POLY_1271`.
2. Sign EIP-712 `ClobAuth` typed-data (domain `{name:"ClobAuthDomain", version:"1", chainId:137}`) and `POST /auth/api-key` → `{apiKey, secret, passphrase}`.
3. Per authenticated request attach: `POLY_ADDRESS`, `POLY_SIGNATURE` (HMAC-SHA256 of request body with `secret`), `POLY_TIMESTAMP`, `POLY_API_KEY`, `POLY_PASSPHRASE`.
4. Each order independently EIP-712 signed against CTF Exchange V2 on Polygon.

**Limitless — single-level:**
1. Generate `lmts_…` key in Profile → API Keys.
2. Header `X-API-Key: lmts_…` on REST and WS handshake.
3. Each order EIP-712 signed against `venue.exchange` (per-market) with domain `{name:"Limitless CTF Exchange", version:"1", chainId:8453}`. Order fields: `salt, maker, signer, taker, tokenId, makerAmount, takerAmount, expiration, nonce, feeRateBps, side (0=BUY,1=SELL), signatureType (0=EOA)`.

### 1.3 REST endpoints (canonical list — every endpoint touched by the bot)

**Polymarket:**
- Gamma (discovery, public): `GET https://gamma-api.polymarket.com/markets?active=true&closed=false&tag_id=...`, `GET .../markets?slug=<slug>`, `GET .../events?slug=<slug>`, `GET .../markets/keyset`, `GET .../events/keyset`
- CLOB market data (public): `GET https://clob.polymarket.com/book?token_id=<clobTokenId>`, `/midpoint`, `/price?side=BUY|SELL`, `/spread`, `/prices-history`, `/tick-size/<token_id>`
- CLOB trading (L2-auth): `POST /order`, `POST /orders` (batch up to 15), `DELETE /order/<id>`, `DELETE /cancel-all`, `DELETE /cancel-market-orders`, `POST /heartbeat` (≤5s), `GET /orders`, `GET /trades`
- Geoblock: `GET https://polymarket.com/api/geoblock`
- Market info SDK call: `getClobMarketInfo(conditionID)` returns `info.fd` (fee data: rate, exponent, taker-only flag), `info.mts` (min tick), `info.mos` (min order size), `feeSchedule`

**Limitless** (base `https://api.limitless.exchange`):
- Discovery: `GET /markets/active`, `GET /markets/active/slugs`, `GET /markets/:slug` (returns `positionIds[0]=YES, [1]=NO`, `venue.exchange`, `venue.adapter`, `feeSchedule`), `GET /markets/search?query=BTC`, `GET /markets/:slug/oracle-candles`
- Trading: `POST /orders` (signed; required `ownerId`, `orderType`, `marketSlug`; optional `clientOrderId`, `postOnly`, `onBehalfOf`), `DELETE /orders/:orderId`, `POST /orders/cancel-batch`, `DELETE /orders/all/:slug`, `GET /markets/:slug/orderbook`, `GET /markets/:slug/historical-price`, `POST /orders/status/batch`
- Portfolio: `GET /portfolio/positions`, `GET /portfolio/trades`, `POST /portfolio/redeem`, `POST /portfolio/withdraw`, `GET /portfolio/allowance`, `GET /portfolio/profile` (returns `id` = ownerId, `feeRateBps`)
- Events: `GET /markets/:slug/events` (trades, orders, liquidity changes)

### 1.4 WebSocket subscriptions

**Polymarket:**
- Market channel: `wss://ws-subscriptions-clob.polymarket.com/ws/market` — send `{type:"MARKET", assets_ids:[yesTokenId, noTokenId, ...]}`. PING every 10s. Events: `book`, `price_change`, `last_trade_price`, `tick_size_change`, `best_bid_ask`.
- User channel: same host `/ws/user` — send `{auth:{apiKey,secret,passphrase}, type:"USER", markets:[conditionIds]}`.
- RTDS (Chainlink/Binance crypto): `wss://ws-live-data.polymarket.com` — subscribe `{topic:"crypto_prices_chainlink", filters:'{"symbol":"btc/usd"}'}`.

**Limitless (Socket.IO):**
- Connect: `wss://ws.limitless.exchange/markets`, transport `websocket`, header `X-API-Key`.
- Emit: `subscribe_market_prices {marketSlugs, marketAddresses}`, `subscribe_positions {marketSlugs}`, `subscribe_order_events` (no payload).
- Receive: `orderbookUpdate`, `orderEvent` (source `OME`|`SETTLEMENT`), `positions`, `marketResolved`.

### 1.5 Order types and tick sizes

| Topic | Polymarket | Limitless |
|---|---|---|
| Order types supported | GTC, GTD (limit, with `postOnly`), FOK, FAK | GTC (postOnly), FAK, FOK |
| Tick sizes | 0.1, 0.01, 0.001, 0.0001 per market — fetch via `getTickSize(token_id)` | 0.01 across 0.01–0.99 |
| Min order | 5 shares server-enforced floor (per py-clob-client error: `"Size (1.08) lower than the minimum: 5"`) | Not documented; probe with size=1 |
| Bot default | `GTC postOnly=true` for inventory; `FAK` for second-leg hedge | Same |
| Forbidden at $500 bankroll | `FOK` on thin books | `FOK` on thin books |

### 1.6 Fees (NEVER hard-code — always pull from runtime market data)

- **Polymarket crypto:** `Fee = C × p × feeRate × (p × (1−p))^exponent`. Crypto `feeRate` ≈ 0.072 (Opus) / 0.07 (GPT5.5). Peak ≈ 1.80% at p=0.50. Maker rebate 20% Crypto, 50% Finance. **Bot pulls per-market `fd` (fee data) and `feeRateBps` live; constants here are last-resort fallback.**
- **Limitless CLOB:** Buy 0.40%–3.00% (peak $0.50); Sell 0.42%–1.50% (peak $0.50). Maker = 0. AMM = flat 0.40%. **Bot pulls `feeRateBps` from `/portfolio/profile` and per-market `feeSchedule`.**
- **Round-trip taker-taker at p≈0.50:** ~3.7%–6.4% on $50 notional. ⇒ **Required quoted divergence ≥4–5%** before bid/ask. Conclusion: maker-first whenever possible.

### 1.7 Resolution oracles (cross-venue compatibility matrix)

| Source | Polymarket | Limitless |
|---|---|---|
| UMA Optimistic Oracle | Most markets | Not used |
| Chainlink BTC/USD stream | 5m BTC up/down markets, some short-window | Some markets (oracle-candles endpoint) |
| Pyth Network | Not used | Majority of crypto markets |
| Manual review | Negligible | Non-financial events (24–72h) |
| Binance candles | Some hourly/daily BTC markets (GPT5.5) | — |

**Compatibility rule for cross-venue arb:** both sides must use an **objective price feed**. UMA-only markets are NOT compatible (subjective dispute layer). The objective set the bot accepts: `{Chainlink-BTC/USD-stream, Pyth-Crypto.BTC/USD, Chainlink-ETH/USD-stream, Pyth-Crypto.ETH/USD}`. Cross-venue trades against any other oracle are refused. **Mandatory 0.5% oracle-divergence haircut** even on compatible objective pairs.

### 1.8 Rate limits

- **Polymarket** (published):
  - CLOB general: 9,000/10s
  - `/book`: 1,500/10s
  - `/prices-history`: 1,000/10s
  - `POST /order`: burst 5,000/10s, sustained 48,000/10min
  - Bridge: 50/10s
  - Gamma `/markets`: 300/10s
- **Limitless** (unpublished): docs say "2 concurrent, 300 ms min delay between requests." Bot enforces both.

### 1.9 Position sizing and risk limits (from Opus §F1 + GPT5.5 architecture)

| Rule | Default value | Configurable env |
|---|---|---|
| Max position per arb (both legs notional) | $40 (8% of $500) | `MAX_POSITION_USD` |
| Max concurrent open arbs | 3 | `MAX_CONCURRENT_ARBS` |
| Max single-platform exposure | $300 (60%) | `MAX_PLATFORM_EXPOSURE_USD` |
| Reserve cash per platform | $50 minimum | `MIN_PLATFORM_RESERVE_USD` |
| Min required edge — daily | 2.0% | `MIN_EDGE_DAILY_PCT` |
| Min required edge — 1h | 2.5% | `MIN_EDGE_1H_PCT` |
| Min required edge — 30m | 3.0% | `MIN_EDGE_30M_PCT` |
| Daily loss stop | −$50 (10%) | `DAILY_LOSS_STOP_USD` |
| Total drawdown stop | −$150 (30%) | `TOTAL_DRAWDOWN_STOP_USD` |

### 1.10 Capital allocation at $500

- **$225 on Polymarket** (pUSD on Polygon)
- **$225 on Limitless** (USDC on Base)
- **$50 reserve in EOA** on whichever chain needs faster topping up

### 1.11 Orphan-leg policy (when leg-A fills but leg-B doesn't)

Per Opus §F3 (the most explicit version):
1. After leg-A fill, wait window for leg-B fill:
   - 15s on 5m markets
   - 60s on 15m–1h
   - 300s on daily
2. If timeout:
   a. Recompute fair value of leg-A using the orphan platform's NO-side bid.
   b. If sale-price-implied loss < 0.5% of bankroll ($2.50): close immediately as FAK.
   c. If loss would exceed that AND market has >30% time-to-resolution remaining: hold to resolution flagged `directional_unhedged=True`. Skip new arbs until it resolves.
3. Never average down. Never widen the limit to "catch up" the other leg.

### 1.12 Phased rollout (config-gated, hard-enforced)

| Phase | Strategy | Capital | Promote when |
|---|---|---|---|
| **Phase 1** | YES + NO complementarity on Limitless (single-venue) daily/hourly crypto, vol ≥ $1k/24h | $20/arb, max 3 concurrent | ≥10 closed arbs, ≥80% net-profitable, no orphan incidents |
| **Phase 2** | + Polymarket maker-rebate harvesting on 15m crypto | $5–$10 GTC postOnly | ≥30 maker fills, positive net rebate-plus-PnL |
| **Phase 3** | + Cross-venue daily strike BTC arb (only if Phases 1+2 net-profitable) | $20–$30/leg, max 1 concurrent until 30 closed | ≥60% win rate, avg edge ≥1.5% |

`STRATEGY_PHASE` env var; defaults to `1`; higher phases refuse to enable unless prior-phase benchmarks met (read from state file).

### 1.13 Bridging

Across Protocol: USDC L2↔L2 in 1–4 min (Opus); ~$1–$2 fee on $1000, ~$0.30 on $225. **Never in the hot path.** Treated as treasury rebalance, not execution path.

### 1.14 Logging, observability, kill switch

- `structlog` JSON to stdout; never log secrets, API keys, signatures.
- Prometheus on `:9090/metrics` (counters: orders submitted/filled/cancelled per venue; gauges: open positions, bankroll, equity).
- SIGTERM/SIGINT handler MUST cancel every open order on both venues within 1s before exit; verified by test.

### 1.15 Geo / compliance

- Startup `GET https://polymarket.com/api/geoblock` — if `blocked=true`, exit code 2.
- Bot refuses to start in jurisdictions Polymarket lists as blocked. No VPN bypass attempts.
- Limitless: relies on user ToS compliance; no programmatic geoblock check available.

### 1.16 Polymarket-specific safety primitives

- Heartbeat task: `POST /heartbeat` every 5s; without it Polymarket auto-cancels all open orders within ~15s.
- WS reconnect → full re-subscribe of every channel.
- Order signing: EIP-712 against current CTF Exchange V2 address (fetched at runtime via `getClobMarketInfo`).

### 1.17 Limitless-specific safety primitives

- Socket.IO `subscribe_market_prices` REPLACES (not merges) prior subscription — bot must always send the full union of currently-needed markets.
- `RESOLVED` status in API can precede on-chain payout vector being posted. Bot must verify `payoutDenominator(conditionId) > 0` on-chain before calling `/portfolio/redeem`.
- Probe minimum size with `size=1` on a test market at startup; capture error to learn floor.

---

## Part 2 — Disagreements between the two reports

Where the reports disagree, the more conservative path was taken. Each is enforced in code with a citing comment.

### 2.1 Polymarket crypto fee rate

- **Opus 4.7:** crypto rate `0.072` (Section B, FX-V2 March 30, 2026 changelog).
- **GPT5.5:** crypto rate `0.07` (current fee page).
- **Decision:** **Query live `feeRateBps` / `fd` per market on every order.** Hard-coded constants are fallback only. Default fallback uses **0.072 (Opus)** since it's higher = more conservative (over-estimates cost, under-estimates edge).

### 2.2 Polymarket 5m BTC product structure

- **Opus 4.7:** "BTC Up or Down 5m" is a *delta-vs-window-open*, NOT a fixed strike. Cannot be cross-venue arb'd. Treat as un-arbable.
- **GPT5.5:** Official site results show "comparable Chainlink-based BTC 5m windows" on Polymarket. Suggests 5m parity may exist.
- **Decision:** **Opus is more conservative — exclude all 5m BTC up/down markets from cross-venue matching.** The bot's market matcher will refuse to pair any market whose normalized form is delta-vs-open (no fixed numeric strike). Only fixed-strike "BTC ≥ $X by deadline T" markets are considered cross-venue candidates.

### 2.3 Hourly/daily oracle compatibility

- **Opus 4.7:** Daily strike-form BTC markets ARE the cross-venue target (Phase 3).
- **GPT5.5:** Current hourly/daily examples diverge — Pyth (Limitless) vs Binance candles (Polymarket) — making them "relative value, not arbitrage."
- **Decision:** **Merge — daily is Phase 3 target ONLY when both sides pass oracle compatibility check.** A Polymarket daily BTC market with Binance-candle resolution will NOT pair with a Limitless Pyth-resolved market, regardless of strike/expiry match. The oracle compatibility matrix (§1.7) is the gate.

### 2.4 Strategy priority order

- **Opus 4.7:** Phase 1 = Limitless YES+NO complementarity → Phase 2 = Polymarket maker rebate → Phase 3 = cross-venue.
- **GPT5.5:** Emphasizes maker-first cross-hedged market-making as the strongest medium-frequency design.
- **Decision:** **Follow Opus phased rollout.** The brief (Section 4 item 11) explicitly references Opus's phases. Single-venue complementarity is genuinely riskless (1 YES + 1 NO = $1 collateralized on the same platform) — the safest possible bot first-step.

### 2.5 Bridge timing

- **Opus 4.7:** Across is "fast and cheap" (1–4 min, 0.10–0.20%) but explicitly states: "never start an arb whose horizon is < bridge round-trip + 2× safety margin."
- **GPT5.5:** Treats bridge as "treasury process, not execution path."
- **Decision:** Both agree — **bridge never in hot path, only treasury rebalance.** Pre-funded inventory on both chains.

### 2.6 Resolution time confidence

- **Opus 4.7:** UMA disputed resolution can take 4–6 days. Capital tied up in disputed market is dead weight.
- **GPT5.5:** Less specific but flags "settlement ≠ resolution" gap on Limitless.
- **Decision:** Apply both — bot reserves bankroll-fraction for capital lock-up risk, and verifies `payoutDenominator > 0` on-chain before any redeem call.

---

## Part 3 — Open questions and how the code defends against each

The reports flag these as documentation gaps. The bot must handle each defensively at runtime, not assume.

| # | Open question | Source | Code defense |
|---|---|---|---|
| 1 | Exact pUSD allowance target on CTF Exchange V2 contract | Opus §J1 | Read contract address at runtime via `getClobMarketInfo()` → `info.exchange`; never hard-code |
| 2 | Limitless absolute minimum order size | Opus §J2 | Probe at startup with size=1 on a test market; cache the discovered floor in state; surface in log |
| 3 | Limitless withdrawal fees | Opus §J3 | Treat as unknown; require manual operator-initiated withdrawals only; bot does not auto-withdraw |
| 4 | Polymarket Chainlink reference timestamp tolerance for 5m | Opus §J4 | N/A — 5m up/down excluded from cross-venue (see §2.2) |
| 5 | UMA dispute behavior on objective crypto markets | Opus §J5 | Treat any Polymarket non-Chainlink crypto market as "may take 4–6 days" worst case; size accordingly |
| 6 | Cross-platform geoblock interaction with deployed proxy | Opus §J6 | If startup geoblock returns `blocked=true`, exit code 2; do NOT attempt close-only mode (out of scope for $500) |
| 7 | Limitless rate-limit burst behavior, 429 vs queue | Opus §J7 | Treat any 429 / 5xx response as breach → exponential backoff starting 1s, max 30s; circuit-breaker on 3 consecutive |
| 8 | Limitless `feeRateBps` per-market vs global profile | Opus §J8 | Fetch BOTH: `profile.feeRateBps` (global default) AND per-market `feeSchedule`; per-market wins if present |
| 9 | Polymarket maker-rebate eligibility on partial fills | Opus §J9 | Conservative: assume rebate is on filled-portion only; compute rebate per fill not per order |
| 10 | Bridge reliability for $200–$300 transfers | Opus §J10 | Bot tracks bridge attempts (timestamp, fee, latency, outcome) in state; uses moving average for treasury decisions |
| 11 | Limitless numeric API rate limits | GPT5.5 §Open Questions | Enforce conservative defaults: 2 concurrent, 300 ms min spacing, configurable via env |
| 12 | Active cross-listed market overlap is dynamic | GPT5.5 §Open Questions | Refresh market matcher universe every 60s; never assume yesterday's match still exists |
| 13 | 30m native support not in Limitless data endpoints | GPT5.5 §Open Questions | 30m cross-venue disabled by default; only 1h+ engaged in Phase 3 |
| 14 | Bridge latency SLA not specified | GPT5.5 §Open Questions | Bot operates purely on pre-funded inventory; bridge is offline operator task |

---

## Part 4 — Things the bot will never do (anti-patterns enforced in code)

Direct lifts from both reports (Opus §H + GPT5.5 anti-pattern table):

1. **Hard-code fees, tick sizes, addresses, oracle sources, min order sizes, or rate limits.** All fetched at runtime.
2. **Match markets by ticker + expiry alone.** Canonical rule signature required: asset, direction, strike (within 0.5%), expiry (within tolerance), oracle, payout shape.
3. **Treat UMA and Pyth as equivalent.** Apply oracle-divergence haircut, hard gate on objective-only set.
4. **Trust visible orderbook depth.** Use FAK with price cap, never FOK at $500 bankroll.
5. **Poll for orderbook updates instead of WS.** WS is source of truth; REST only for recovery / reconciliation.
6. **Bridge to complete a hedge.** Pre-funded inventory only.
7. **Use double market orders.** Default = maker on at least one leg.
8. **Subscribe partially on WS reconnect.** Always full re-subscribe union.
9. **Trade markets without sufficient depth.** Filter: 24h volume ≥ 10× intended position size.
10. **Treat `RESOLVED` as redeemable.** Verify `payoutDenominator > 0` on-chain before calling redeem.
11. **Skip heartbeat on Polymarket.** Every 5s, no exceptions.
12. **Commit `.env`, private keys, signed payloads.** `.gitignore` enforces; logger redaction enforces.
13. **Catch exceptions silently.** Log structured, then retry-with-backoff or propagate.
14. **Add LLM calls / sentiment in hot path.** This bot is rule-based.
15. **Bypass geoblock with VPN.** Violates ToS; can cause fund freezes.

---

## Part 5 — Definition of done (mirroring brief §9)

- [ ] This file (`STRATEGY_SYNTHESIS.md`) exists and reconciles both reports ✅
- [ ] `docker compose up -d` starts the bot cleanly with valid `.env`
- [ ] Logs show JSON, geoblock check, endpoint validation, state init, both WS connections established
- [ ] `pytest` passes with ≥80% coverage on `src/risk/` and `src/fees/`
- [ ] `scripts/dry_run.py` runs 10 min without error and logs would-be trades
- [ ] Three-command README deploy works on clean ARM64 VM
- [ ] Kill switch: SIGTERM produces "cancelled N orders on each venue" log line within 1s before exit

---

*End of synthesis. All code in `src/` traces a decision back to a numbered section here.*
