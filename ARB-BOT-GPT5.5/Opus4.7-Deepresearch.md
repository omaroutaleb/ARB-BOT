# Cross-Platform Crypto Prediction-Market Arbitrage Bot — Polymarket × Limitless Exchange ($500 Bankroll)

## TL;DR

- **At $500, a true cross-venue (Polymarket↔Limitless) arbitrage bot is structurally marginal**: combined round-trip taker fees alone can exceed 4.5% on 50/50-priced markets, oracle mismatch (UMA/Chainlink on Polymarket vs. Pyth on Limitless) injects tail risk, and capital fragmentation across Polygon and Base eliminates the ability to chase fast spreads. Build it, but only as a third-stage strategy.
- **What IS viable at $500:** (1) single-venue YES + NO complementarity arbitrage on Limitless daily/hourly crypto markets, and (2) maker-rebate harvesting on Polymarket short-dated BTC markets where takers pay up to 1.80% and 20–25% of that flows back to makers daily.
- **Realistic bankroll thresholds:** $500–$2,000 = single-venue maker-only; $2,000–$10,000 = cross-venue daily arb starts clearing costs; $10,000+ = full multi-timeframe cross-venue.

---

## Key Findings

1. **Polymarket has moved from zero fees to a dynamic, share-based taker-fee model in three waves in 2026.** Per the official Polymarket Changelog: Jan 6 enabled taker fees on 15-minute crypto markets; Mar 6 — "taker fees and maker rebates extend to all crypto markets including 1H, 4H, daily, and weekly"; Mar 30 launched Fee Structure V2 with category rates (crypto fee rate **0.072**, sports 0.03, finance/politics/tech 0.04, economics/culture/weather/other 0.05). Geopolitics markets remain fee-free. The fee formula is `Fee = C × p × feeRate × (p × (1−p))^exponent` and peaks at ~1.80% at p=0.50 for crypto. Makers earn 20% (crypto) to 50% (finance) rebates daily.

2. **Limitless Exchange fees are a dynamic curve, not a flat rate.** Per the Limitless Fees doc: AMM markets are flat 0.40%; CLOB taker buys range **0.40%–3.00%** (peak at $0.50), CLOB taker sells range **0.42%–1.50%** (peak at $0.50). Makers (resting limit orders) pay zero.

3. **Oracle architectures are different.** Polymarket uses UMA Optimistic Oracle for most markets, BUT its 5-minute BTC up/down markets resolve directly against the Chainlink BTC/USD data stream. Limitless resolves the majority of crypto markets automatically via **Pyth Network**, with manual review (24–72h) for non-financial markets. Two markets that *look* the same can resolve differently because their oracles aggregate prices differently.

4. **Strike structure is fundamentally different on the highest-volume products.** Polymarket's flagship 5-minute crypto product is "BTC Up or Down" — a *delta* vs. opening price (not a strike). The Block and Tekedia confirm this market reached **$60 million in daily trading volume one month post-launch** — but it has no direct equivalent on Limitless (whose markets are fixed-strike "above/below $X by deadline"). Cross-venue arbitrage requires fixed-strike products on BOTH sides, which means daily/weekly windows, not 5-minute ones.

5. **YES + NO complementarity arbitrage on Polymarket has historically captured very real money.** Academic on-chain measurement (Saguillo, Ghafouri, Kiffer & Suarez-Tangil at IMDEA Networks, *"Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets,"* arXiv:2508.03474) found **$5.90M extracted by buying YES+NO below $1.00** and **$4.68M extracted by selling YES+NO above $1.00** across 7,051 single-condition markets, with $39.59M total arbitrage extracted April 2024–April 2025. This is the strategy that is genuinely available to a $500 bot.

6. **Bridging Polygon ↔ Base is fast and cheap, but not instant.** Per Across Protocol docs and DeFiLlama's bridges dashboard, Across L2→L2 USDC transfers complete in **1–4 minutes** (or seconds with CCTP V2 fast-mode), at an **all-in fee of $1–$2 on a $1,000 transfer (0.10–0.20%)**. At a $225 transfer that's roughly $0.23–$0.45. Still far too slow for 5m or 15m arb cycles — bridge planning must be done in advance, not reactively.

7. **The Polymarket→Limitless migration guide is the single best technical Rosetta Stone.** Polymarket needs L1 EIP-712 + 5 `POLY_*` HMAC headers per request; Limitless needs a single `X-API-Key: lmts_…` header. Order signing on Limitless uses the same EIP-712 shape as Polymarket CTF Exchange, but with `chainId=8453`, domain name `"Limitless CTF Exchange"`, and `verifyingContract = venue.exchange` (fetched per-market).

---

## Details

### A. Executive Summary — Is this viable with $500?

**Honest answer: At $500, true cross-venue arbitrage is structurally marginal-to-unviable as a primary profit strategy, but viable as a learning vehicle and as a foundation for two adjacent strategies that DO work at this size: (1) single-platform YES/NO complementarity arbitrage on Limitless, and (2) maker-rebate harvesting on Polymarket short-dated crypto markets.** Architect the bot so the cross-venue leg is opportunistic, not the bread-and-butter.

Why cross-venue is hard at $500:

1. **Capital fragmentation.** Split $500 50/50 → $250 per chain. Reserve 20% for the unfilled-leg buffer → $200 deployable per side, below the depth threshold where quotes are usually firm.
2. **Resolution oracle mismatch.** Polymarket's 5-minute BTC markets resolve against Chainlink BTC/USD data stream; Limitless markets resolve overwhelmingly against Pyth. The "same" market can settle differently.
3. **Strike/expiry mismatch.** Polymarket's high-volume crypto product is delta-vs-open; Limitless is strike-vs-deadline. Only daily/weekly windows align.
4. **Fee asymmetry.** Polymarket crypto peak taker fee ~1.80% at p=0.50; Limitless CLOB buy peaks ~3.00%. Round-trip taker-taker at 50/50 can exceed 4.5%, which wipes out almost every visible cross-venue spread.
5. **Bridge timing.** Polygon↔Base round-trips take 1–10 minutes and cost ~0.10–0.20% per crossing. Too slow to rebalance reactively on a 5–30 minute horizon.

**Bankroll thresholds for viability:**
- $500–$2,000: single-venue maker-only; cross-venue is opportunistic only.
- $2,000–$10,000: cross-venue daily/weekly arb starts to clear costs on liquid markets.
- $10,000+: full multi-timeframe cross-venue with dedicated capital pools and weekly bridge rebalancing.

### B. Platform Comparison Table

| Dimension | Polymarket | Limitless Exchange |
|---|---|---|
| **Chain** | Polygon (chainId 137) | Base (chainId 8453) |
| **Collateral** | pUSD (post-CLOBv2 April 28, 2026), 1:1 backed by USDC on Polygon | **Native USDC on Base** (Circle, `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, 6 decimals) — confirmed by Limitless smart-contracts doc |
| **Market mechanism** | Hybrid CLOB (off-chain match, on-chain settlement via CTF Exchange V2 / Neg Risk CTF Exchange V2) | Hybrid CLOB + AMM markets; CLOB uses per-market venue-routed CTF Exchange |
| **Maker fee** | 0% (rebates 20% on Crypto, up to 50% on Finance) | 0% (LP rewards in USDC) |
| **Taker fee (crypto)** | Dynamic, share-based: `C × p × 0.072 × p(1−p)`; peaks ~1.80% at p=0.50 | Buy 0.40–3.00% (peak $0.50), Sell 0.42–1.50% (peak $0.50); AMM flat 0.40% |
| **Minimum order** | **5 shares server-enforced floor** (per py-clob-client error: *"Size (1.08) lower than the minimum: 5"*); effective USD floor ~$1–$5 depending on price | No documented absolute floor; LP-rewards eligibility threshold ~100 shares per market. Implementer must probe with size=1 to discover server floor. |
| **Tick sizes** | **0.1, 0.01, 0.001, 0.0001** per market (Polymarket docs Orders Overview); orders failing tick get `INVALID_ORDER_MIN_TICK_SIZE` | Price 0.01–0.99 in 1-cent steps (per `POST /orders` OpenAPI: `"Order price as decimal (0.01-0.99, required for GTC orders)"`) |
| **Order types** | GTC, GTD (limit, with `postOnly`), FOK, FAK (market) | GTC (with `postOnly`), FAK, FOK |
| **Resolution oracle (crypto)** | UMA Optimistic Oracle (most); **Chainlink BTC/USD data stream** for 5m BTC markets | **Pyth Network** for majority of markets; manual Limitless team review for non-financial events |
| **Resolution time** | UMA: ~2h challenge then auto-resolve; Chainlink 5m: near-instant; UMA disputed: 4–6 days | Pyth: automatic at deadline; manual: 24–72h |
| **API auth** | L1 EIP-712 → L2 (5 headers `POLY_ADDRESS`, `POLY_SIGNATURE` HMAC-SHA256, `POLY_TIMESTAMP`, `POLY_API_KEY`, `POLY_PASSPHRASE`) | Single `X-API-Key: lmts_…` header; orders EIP-712 signed against `venue.exchange` |
| **REST hosts** | `gamma-api.polymarket.com`, `clob.polymarket.com`, `data-api.polymarket.com`, `bridge.polymarket.com` | `https://api.limitless.exchange` (unified) |
| **WebSocket** | `wss://ws-subscriptions-clob.polymarket.com/ws/market` (public), `/ws/user` (auth); plus RTDS `wss://ws-live-data.polymarket.com` for crypto prices and sports `wss://sports-api.polymarket.com/ws` | `wss://ws.limitless.exchange` Socket.IO, namespace `/markets` |
| **Rate limits** | CLOB general 9,000/10s; `/book` 1,500/10s; `POST /order` burst 5,000/10s, sustained 48,000/10min; Bridge 50/10s; Gamma `/markets` 300/10s | 2 concurrent requests, 300 ms minimum delay between requests |
| **Withdrawal mechanics** | Bridge via fun.xyz proxy (`bridge.polymarket.com`); `redeemPositions` on CTF contract after resolution | `POST /portfolio/withdraw` for server-wallet users; direct EOA self-custody otherwise; `POST /portfolio/redeem` for resolved positions |
| **KYC** | None for global API (EOA/deposit-wallet); KYC required for US-regulated Polymarket USA venue | No KYC — wallet-based onboarding (subject to ToS) |
| **Geo blocks** | 30+ blocked countries (US, UK, France, Germany, Belgium, Netherlands, Italy, Australia, Japan UI-only, others); Ontario, Crimea/Donetsk/Luhansk; check via `GET https://polymarket.com/api/geoblock` | Permissionless on-chain access; user responsible for local-law compliance |
| **Gas model** | Gasless (relayer-paid) for proxy/safe/deposit wallets; EOA users need POL | Gasless via Privy server wallets in delegated flow; EOAs pay Base gas (~$0.01–$0.05) |

### C. Arbitrage Opportunity Types

In order of expected viability at $500:

**C1. YES + NO complementarity arbitrage WITHIN one platform** (highest-EV at $500). Because YES + NO must equal $1.00 collateralized, when `ask(YES) + ask(NO) < $1.00` (after fees) you can buy 1 YES + 1 NO for less than $1 and either merge them via the CTF or hold to resolution for guaranteed $1. The mirror: when `bid(YES) + bid(NO) > $1.00`, mint via split (USDC → 1 YES + 1 NO for $1) and sell each leg. Academic on-chain measurement (Saguillo et al., arXiv:2508.03474) found $5.90M extracted on the sub-$1 side and $4.68M on the above-$1 side across 7,051 markets — proof that this strategy is real, competitive, and accessible. Limitless documentation explicitly notes the Negrisk architecture has "built-in arbitrage" via share conversion.

**C2. Cross-platform direct divergence on equivalent markets** (your stated goal, hardest). When `P_poly(YES) − P_lim(YES) > total_round_trip_cost`, buy the cheaper YES on platform A AND buy NO on platform B such that total cost < $1.00. Only viable at daily+ horizons with strike-form markets on both sides.

**C3. Near-equivalent markets with adjusted edge** — different strikes or expiries on each platform, hedged via BTC vol; residual basis risk usually swamps a $500 bankroll's EV.

**C4. Time-decay / mean-reversion on short-dated single-venue markets** — 5m BTC markets on Polymarket compress to $0.92–$0.99 in the last 30 seconds; maker bots that quote against panic takers earn the rebate AND convergence. This strategy displaced the original "Binance-latency arb" wallets after fees were enabled.

**C5. Time-decay cross-platform** — Polymarket 5m BTC vs. Limitless hourly is NOT equivalent and should not be paired directly.

### D. Execution Strategy by Timeframe (with $500 bankroll)

| Timeframe | Realistic edge | Polling cadence | Position size | Hold time | Verdict |
|---|---|---|---|---|---|
| **5m** | Cross-venue: ~none. Single-venue maker: 0.5–3% per fill via rebates and convergence | WS-only; REST poll only on subscribe loss. Heartbeat every 5–10s | $5–$20 per fill | Seconds–5min | **Skip cross-venue. Maker-only on Polymarket BTC 5m.** |
| **15m** | Maker rebate + capture is meaningful; cross-venue still mostly noise | WS + 30s REST recon | $10–$30 per fill | ≤15min | **Good single-venue learning ground.** |
| **30m/1h** | Cross-venue *occasionally* clears costs on illiquid markets when one venue lags news | WS subscribed, REST poll 30–60s | $20–$50 per leg | 30–60min | **First realistic cross-venue tier.** |
| **Daily** | Cross-venue arb on equivalent BTC-strike markets; YES+NO complementarity is the strongest pure-edge | WS for watchlist + 60–120s REST recon | $25–$100 per leg | Hours–24h | **Best starting point for cross-platform.** |

**Recommended timeframe rollout:** Daily → 1h → 30m → 15m/5m (maker-only). Validate edge at each level before enabling the next.

### E. Technical Implementation Specifics

#### E1. Polymarket — Required Endpoints

Market discovery (Gamma API, public):
- `GET https://gamma-api.polymarket.com/markets?active=true&closed=false&tag_id=...`
- `GET https://gamma-api.polymarket.com/markets?slug=<slug>`
- `GET https://gamma-api.polymarket.com/events?slug=<slug>`
- `GET https://gamma-api.polymarket.com/markets/keyset` and `/events/keyset` (cursor-paginated; per Polymarket Changelog these are replacing offset-based)

Market data (CLOB API, public):
- `GET https://clob.polymarket.com/book?token_id=<clobTokenId>`
- `GET https://clob.polymarket.com/midpoint?token_id=...`
- `GET https://clob.polymarket.com/price?token_id=...&side=BUY|SELL`
- `GET https://clob.polymarket.com/spread?token_id=...`
- `GET https://clob.polymarket.com/prices-history?market=<conditionId>&interval=...`
- `GET https://clob.polymarket.com/tick-size/<token_id>` — required per order
- `getClobMarketInfo(conditionID)` via SDK → returns `info.fd` (fee data: rate, exponent, taker-only flag), `info.mts` (min tick size), `info.mos` (min order size), `feeSchedule`

Trading (L2-authenticated):
- `POST https://clob.polymarket.com/order`
- `POST https://clob.polymarket.com/orders` (batch up to 15)
- `DELETE https://clob.polymarket.com/order/<id>`
- `DELETE https://clob.polymarket.com/cancel-all`
- `DELETE https://clob.polymarket.com/cancel-market-orders`
- `POST https://clob.polymarket.com/heartbeat` — every ≤5s (auto-cancel triggers at ~10s+5s buffer)
- `GET https://clob.polymarket.com/orders`
- `GET https://clob.polymarket.com/trades`

Geoblock (check at startup, before any trade):
- `GET https://polymarket.com/api/geoblock`

#### E2. Limitless — Required Endpoints (base `https://api.limitless.exchange`)

Discovery:
- `GET /markets/active`
- `GET /markets/active/slugs` (slug, strike, ticker, deadline list)
- `GET /markets/:slug` → returns `positionIds[0]=YES, [1]=NO`, `venue.exchange`, `venue.adapter`, `feeSchedule`
- `GET /markets/search?query=BTC`
- `GET /markets/:slug/oracle-candles` (Chainlink candle data for Chainlink-resolved markets)

Trading:
- `POST /orders` (signed; required: `ownerId`, `orderType`, `marketSlug`; optional: `clientOrderId`, `postOnly`, `onBehalfOf`)
- `DELETE /orders/:orderId`
- `POST /orders/cancel-batch`
- `DELETE /orders/all/:slug`
- `GET /markets/:slug/orderbook`
- `GET /markets/:slug/historical-price`
- `POST /orders/status/batch`

Portfolio:
- `GET /portfolio/positions`
- `GET /portfolio/trades`
- `POST /portfolio/redeem`
- `POST /portfolio/withdraw`
- `GET /portfolio/allowance`
- `GET /portfolio/profile` → returns `id` (use as `ownerId`) and `feeRateBps`

#### E3. Authentication

**Polymarket (two-level):**
1. Hold a Polygon private key or set up a deposit wallet with `signatureType=3 POLY_1271`.
2. Sign EIP-712 `ClobAuth` typed-data (domain `{name:"ClobAuthDomain", version:"1", chainId:137}`) and `POST /auth/api-key` → `{apiKey, secret, passphrase}`.
3. On every authenticated request, attach: `POLY_ADDRESS`, `POLY_SIGNATURE` (HMAC-SHA256 of request body using `secret`), `POLY_TIMESTAMP`, `POLY_API_KEY`, `POLY_PASSPHRASE`.
4. Each order is independently EIP-712 signed against **CTF Exchange V2** on Polygon, even with L2 headers present.

**Limitless (single-level for individual use):**
1. Generate `lmts_…` key in Profile → API Keys.
2. Header `X-API-Key: lmts_…` on REST and on WS handshake.
3. Each order EIP-712 signed against `venue.exchange` (per-market) with domain `{name:"Limitless CTF Exchange", version:"1", chainId:8453}` and Order fields `salt, maker, signer, taker, tokenId, makerAmount, takerAmount, expiration, nonce, feeRateBps, side (0=BUY,1=SELL), signatureType (0=EOA)`.

For multi-account/partner bots: derive an HMAC scoped token via `POST /auth/api-tokens/derive` with `[trading, account_creation, delegated_signing]` and use `lmts-api-key`, `lmts-signature`, `lmts-timestamp` headers.

#### E4. Order Types

- **Default: `GTC` with `postOnly=true`** for inventory-acquisition. Guarantees maker on both venues (zero fees).
- **`FAK` (Fill-And-Kill)** as the "marketable limit" for the second leg when one side has filled and you need to lock the hedge — gives you a price cap, executes what's available, cancels the remainder.
- **Avoid `FOK`** at $500 — fill-or-kill on thin books fails too often and leaves naked exposure.
- Polymarket tick sizes: 0.1, 0.01, 0.001, 0.0001. Fetch via `getTickSize(token_id)` and round prices to it. Limitless: 1-cent tick across the 0.01–0.99 range.

#### E5. WebSocket Subscription Pattern

Polymarket market channel:
```
ws = connect("wss://ws-subscriptions-clob.polymarket.com/ws/market")
ws.send({ type: "MARKET", assets_ids: [yesTokenId, noTokenId, ...] })
# PING every 10s
# Events: book, price_change, last_trade_price, tick_size_change, best_bid_ask
```

Polymarket user channel:
```
ws = connect("wss://ws-subscriptions-clob.polymarket.com/ws/user")
ws.send({ auth:{apiKey,secret,passphrase}, type:"USER", markets:[conditionIds] })
```

Polymarket RTDS (free Chainlink/Binance crypto price stream):
```
ws = connect("wss://ws-live-data.polymarket.com")
subscribe({ topic:"crypto_prices_chainlink", filters:'{"symbol":"btc/usd"}' })
```

Limitless (Socket.IO):
```
sock = io('wss://ws.limitless.exchange/markets',
          { transports:['websocket'], extraHeaders:{'X-API-Key':KEY} })
sock.emit('subscribe_market_prices', { marketSlugs:[...], marketAddresses:[...] })
sock.emit('subscribe_positions',     { marketSlugs:[...] })
sock.emit('subscribe_order_events')   # no payload, per-user
sock.on('orderbookUpdate', handleBook)
sock.on('orderEvent', handle)         # discriminate source: 'OME' | 'SETTLEMENT'
sock.on('positions', handlePos)
sock.on('marketResolved', handleResolution)
```

Both platforms require **re-subscribing on every reconnect**. On Limitless, repeated `subscribe_*` calls REPLACE the previous set — always send the full union.

#### E6. State Management — Cross-Platform Position Tracker

```python
Position = {
  arb_id: UUID,                 # links the two legs
  platform: 'poly' | 'lim',
  market_key: <slug/conditionId>,
  side: 'YES' | 'NO',
  intended_size: float,
  filled_size: float,
  avg_price: float,
  status: 'pending'|'partial'|'filled'|'hedge_failed'|'closed',
  order_ids: [...],
  client_order_id: UUID,        # use SAME id across legs for dedup
  oracle_source: 'UMA'|'Chainlink'|'Pyth'|'manual',
  resolution_time: datetime,
  bridge_in_flight: bool,
}
```

Persist to SQLite (WAL mode) or Redis after every WS event. The bot must survive a mid-arb crash without losing track of exposed legs.

#### E7. Partial Fill Handling

- Leg-A fills X of Y → immediately submit leg-B as `FAK` with `size=X` (not Y) and price cap = `(1.00 − leg_A_price − target_edge)`.
- Leg-B FAK fills 0 shares: trigger `hedge_failed` state and run leg-A-only exit policy (Section F3).
- Leg-B fills 0 < Z < X: reduce leg-A by selling `X−Z` shares as FAK at worst-acceptable price, realising small loss to flatten exposure.

### F. Risk Management & Position Sizing

#### F1. Hard limits

| Rule | Value | Rationale |
|---|---|---|
| Max position per arb (both legs' notional) | $40 (8% of bankroll) | One bad resolution = max 8% drawdown |
| Max concurrent open arbs | 3 | Caps capital-at-risk at ~24% |
| Max single-platform exposure | $300 (60%) | Preserves bridging optionality |
| Reserve cash on each platform | $50 minimum | For hedging an unfilled-leg emergency |
| Minimum required edge (post-fees & slippage) | 2.0% daily, 2.5% 1h, 3.0% 30m | Covers oracle-mismatch tail risk |
| Daily loss stop | −$50 (10%) | Pause 24h; post-mortem |
| Total drawdown stop | −$150 (30%) | Halt the bot; full review |

#### F2. Capital allocation
- $225 on Polymarket (pUSD on Polygon)
- $225 on Limitless (USDC on Base)
- $50 reserve in an EOA on whichever chain needs faster topping up

#### F3. Single-leg orphan policy

If leg-A fills and leg-B fails to fill within 15s (5m markets) / 60s (15m–1h) / 300s (daily):
1. Recompute fair value of leg-A using the orphan platform's NO-side bid.
2. If sale price implies loss < 0.5% bankroll ($2.50): close immediately as FAK.
3. If loss would be larger AND market has > 30% time-to-resolution remaining: hold to resolution as a flagged `directional_unhedged=True` position; skip new arbs until it resolves.
4. Never average down. Never widen the limit to "catch up" the other leg.

#### F4. Gas/fee budget — minimum edge math (for a $50 round-trip at p≈0.50)

| Cost element | Polymarket leg | Limitless leg |
|---|---|---|
| Taker fee (50/50 worst case) | ~1.80% ($0.45) | Buy ~3.00% ($0.75) / Sell ~1.50% ($0.38) |
| Gas (relayer model) | $0 (pUSD relayer) | ~$0.01–$0.05 (Base) |
| Slippage (1 tick = $0.01) | ~$0.50 | ~$0.50 |
| Bridge cost (if rebalancing) | amortized ~$0.10 per trade | — |
| **Total round-trip** | **~$0.95–$1.95** | **~$0.88–$1.25** |

Net cost of a $50 taker × taker round-trip: **~$1.83–$3.20 = 3.7–6.4%**. Required quoted divergence to clear: **> ~4–5%** before bid-ask. **Conclusion: route at least one leg as maker (`postOnly` GTC) whenever possible. Two takers = unprofitable at $500.**

### G. Market Identification Logic (Cross-Platform Matching)

```python
def normalize(market):
    return {
      'asset': extract_asset(market.title),
      'direction': extract_direction(market.title),    # above | below | updown
      'strike_usd': extract_strike(market.title),
      'expiry_utc': market.deadline_utc,
      'duration_class': bucket(market.duration),
      'oracle': market.resolution_source,
      'yes_token_id': market.yes_token_id,
      'no_token_id': market.no_token_id,
      'platform': 'poly' | 'lim',
      'raw_title': market.title,
      'tick_size': market.tick_size,
    }

def oracle_compatible(a, b):
    objective = {'Chainlink-BTC/USD-stream', 'Pyth-Crypto.BTC/USD'}
    return (a in objective and b in objective) or a == b

def find_pairs(poly, lim):
    pairs = []
    for p in poly:
      for l in lim:
        if p.asset != l.asset: continue
        if p.direction != l.direction: continue
        if abs(p.strike_usd - l.strike_usd) / p.strike_usd > 0.005: continue
        tol = 5*60 if p.duration_class in ('5m','15m','30m') else 3600
        if abs((p.expiry_utc - l.expiry_utc).total_seconds()) > tol: continue
        if not oracle_compatible(p.oracle, l.oracle): continue
        pairs.append((p, l))
    return pairs
```

**Matching pitfalls:**
- Polymarket "BTC Up or Down 5m" is delta-from-window-open, NOT a strike. Treat as un-arbable cross-venue.
- Polymarket "≥" vs. Limitless ">=" wording: both use ≥; direction logic is consistent.
- Time zone: Polymarket titles often use ET, deadlines are UTC. Always parse `deadline_utc`, never the title.
- Cache normalized objects with a 60s TTL.

### H. THINGS TO AVOID

1. **Treating UMA and Pyth as equivalent oracles.** Apply ≥0.5% oracle-divergence haircut to every cross-venue edge calc.
2. **Trusting visible orderbook depth.** Always use `FAK` with a price cap, never `FOK` with size > top-of-book.
3. **Stale price polling.** WS only for trading decisions. REST polling = guaranteed latency-arb victim.
4. **Bridge timing risk.** Across is fast (1–4 min, 0.10–0.20%) but never start an arb whose horizon is < bridge round-trip + 2× safety margin.
5. **Sub-economic sizing.** Trades under $20 round-trip are not economic on Polymarket crypto markets at taker prices (5-share minimum × $0.50 = $2.50 floor, but worst-case fees consume that).
6. **Geo restrictions.** Polymarket geoblocks US, UK, France, Germany, Belgium, Netherlands, Italy, Japan (UI), Australia, 20+ more. Check `GET https://polymarket.com/api/geoblock` at startup; refuse to trade if `blocked=true`. Using VPN violates ToS and can cause fund freezes.
7. **Fee changes mid-trade.** Per the Polymarket Changelog, fees changed on Jan 6, Mar 6, and Mar 30, 2026. The bot MUST fetch `feeRateBps` and the `feeSchedule` per-order via `getClobMarketInfo()` and the per-market object, never hard-code rates.
8. **Locking up capital on the wrong side.** Pre-trade, check executable inventory on the *target* (leg-B) side, not just the visible quote. If leg-B's book < 2× intended size at target price, trade is too aggressive.
9. **MEV/frontrunning on Polygon.** The off-chain matcher limits direct mempool MEV, but the USDC↔pUSD swap step can be sandwiched. Use the relayer (gasless) path.
10. **Trying to arb at too tight an edge.** With $500, any displayed cross-venue edge < ~3% is noise.
11. **Limitless server-wallet allowance preconditions.** Poll `GET /profiles/partner-accounts/:profileId/allowances` until `ready=true` before the first order, or trades revert.
12. **Over-leveraging illiquid markets.** Most short-dated Polymarket crypto markets (excluding the 5m BTC up/down, which sees ~$60M/day per The Block) have 24h volume under $5,000. Filter to markets with ≥10× your intended position size in 24h volume.
13. **Heartbeat omission.** Polymarket cancels all open orders if heartbeat omitted >~15s. Run a 5s heartbeat task, don't rely on WS alone.
14. **Destructive resubscription on Limitless.** Each `subscribe_market_prices` REPLACES the previous set. Always send the full union.
15. **Hallucinated endpoints.** Every endpoint in this document was drawn from current docs (May 2026). The implementing bot MUST hit `https://docs.polymarket.com/llms.txt` and `https://docs.limitless.exchange/llms.txt` at build time and fail-fast if any URL has changed. Do not let an LLM invent paths.
16. **YES/NO index assumption.** Both platforms put YES at index 0 and NO at index 1, but ALWAYS verify per-market by checking outcome strings; Negrisk markets can re-order.
17. **Settlement-vs-resolution confusion on Limitless.** API can mark a market `status: RESOLVED` BEFORE the on-chain CTF payout vector is posted. Do not call `/portfolio/redeem` until `payoutDenominator(conditionId) > 0` on-chain.
18. **0.5%-tick problem on Limitless.** Prices restricted to 0.01–0.99 in 1-cent steps. A "fair value" of 0.995 cannot be quoted; nearest legal price 0.99 implies 0.5% implicit slippage on high-confidence trades. Account in EV.

### I. Recommended Starting Configuration

**Phase 0 — Setup (Day 1–3, no live trading):**
- Generate a Polygon EOA, fund with ~$5 in POL for gas (if not using gasless).
- Create deposit wallet (`signatureType=3`) on Polymarket; derive L2 API creds via the official SDK (`@polymarket/clob-client-v2` or `py-clob-client-v2`).
- Bridge $225 USDC → Polygon; convert to pUSD via the Polymarket one-time approval.
- Generate Limitless API key; verify USDC allowance via `GET /portfolio/allowance`; set USDC approval to `venue.exchange` (and `venue.adapter` for Negrisk SELL).
- Bridge $225 USDC → Base via Across (1–4 min, ~$0.30 on $225).
- Keep $50 unbridged float.
- Unit-test EIP-712 signers against reference vectors; confirm orders accepted in `--dry-run`.

**Phase 1 — Single-platform validation (Week 1):**

**Strategy: YES + NO complementarity arbitrage on Limitless daily/hourly crypto markets only.**
- Universe: All `category=Crypto` markets on Limitless with `duration_class ∈ {daily, hourly}` AND 24h volume ≥ $1,000.
- Edge threshold: trade when `ask(YES) + ask(NO) ≤ $0.985` (1.5% gross → ~0.7% net after fees).
- Order type: `FAK` on both legs, sent simultaneously.
- Position size: $20 per arb (5–10% of Limitless allocation).
- Max concurrent: 3.
- Polling: WS only, 30s REST recon.

**Phase 2 — Add Polymarket maker-rebate harvesting (Week 2–3):**
- Quote 1-tick-inside on Polymarket BTC 15m markets with $5–$10 GTC `postOnly=true` orders.
- Cancel + re-quote on price changes via WS.
- Heartbeat every 5s.
- Edge target: positive net = (rebate ~20% of taker fee on Crypto) + (fill-side convergence).

**Phase 3 — Cross-venue arb (Week 4+, only if Phases 1 & 2 are profitable):**
- Universe: BTC daily markets in strike form ("BTC above $X on date") on BOTH platforms.
- Match strike within 0.5% and expiry within 1 hour; both oracles must be objective price feeds (Chainlink on Polymarket, Pyth on Limitless).
- Edge threshold: ≥3.0% gross divergence after the 0.5% oracle-mismatch haircut.
- Position size: $20–$30 per leg.
- Leg-A as maker (`postOnly` GTC); if filled, leg-B as `FAK` with 60s timeout.
- Max 1 cross-venue arb concurrent until ≥30 closed cross-venue trades.

**Rough expected returns at $500** (not promises):
- Phase 1 only: $5–$25/week
- Phase 1+2: $10–$40/week
- Phase 3 added: typically +$0–$10/week incremental, mostly for diversification, not yield

### J. Open Questions & Documentation Gaps

1. **Polymarket pUSD allowance specifics post-CLOBv2.** What is the exact ERC-20 approval target for the new CTF Exchange V2 contract on Polygon? The migration notes say "approve pUSD to the CTF Exchange," but the V2 contract address must be confirmed at runtime via `getClobMarketInfo()` or `docs.polymarket.com/resources/contract-addresses`.
2. **Limitless absolute minimum order size.** Docs do not publish a hard floor. Probe with size=1 share on a test market and capture the precise error code.
3. **Limitless withdrawal fees.** `POST /portfolio/withdraw` docs do not state a platform fee. Confirm with `help@limitless.network` whether any cost beyond Base gas applies.
4. **Polymarket Chainlink BTC/USD reference timestamp tolerance** for 5m markets. Anecdotally "Price To Beat" is captured at +0ms from window boundary, but no published tolerance.
5. **UMA dispute behavior on numeric-strike crypto markets.** Can a "BTC above $110,000 by Friday" market be disputed when Chainlink shows an unambiguous price? Confirm Polymarket's policy for fully objective markets.
6. **Cross-platform geoblock interaction.** If user's IP is geoblocked from Polymarket but their proxy wallet was deployed earlier, can the proxy still settle resolved positions? The bot must handle close-only mode.
7. **Limitless rate-limit specifics.** Docs state only "2 concurrent / 300ms minimum delay." Find burst behavior under load and the response code for breaches (429 vs. silent queue).
8. **Limitless `feeRateBps` source.** `Get Your Profile` returns a `feeRateBps`. Is this the exact value the bot must include in every signed order, and does it ever differ per market via `feeSchedule`?
9. **Polymarket maker-rebate eligibility on partial fills.** Are partially filled GTC orders rebated only on the maker portion? Implied yes; verify via the `/rebates/maker-fees` endpoint after a test fill.
10. **Bridge route reliability for $200–$300 transfers.** No bridge advertises a guaranteed SLA. The bot should track success/fail timing on small sample transfers and select the best route per session.

---

## Recommendations

**Stage 1 (do this now):** Build the codebase with the architecture in Section E, but enable ONLY Phase 1 (YES+NO complementarity arb on Limitless daily/hourly crypto markets). Run for at least 14 calendar days. **Benchmark to advance:** ≥10 closed arbs, ≥80% net-profitable rate, no orphan-leg incidents.

**Stage 2 (Week 3):** Add Phase 2 maker-rebate harvesting on Polymarket 15m crypto markets. Quote tiny ($5–$10) GTC `postOnly` orders 1 tick inside the spread. **Benchmark to advance:** ≥30 maker fills, positive net rebate-plus-PnL.

**Stage 3 (Week 4+):** Only after Phases 1 & 2 are net-profitable, enable Phase 3 cross-venue arb on daily strike-form BTC markets, capped at 1 concurrent arb until 30 closed cross-venue trades. **Benchmark to advance to higher cadence (1h, 30m):** ≥60% win rate, average net edge ≥1.5% of position size.

**Benchmarks that should reverse a decision:**
- If daily loss ≥ $50 (10% of bankroll): pause 24h, post-mortem, do not raise position sizes.
- If total drawdown ≥ $150 (30%): halt bot, full code/strategy review before resuming.
- If Polymarket announces another fee structure change: immediately re-pull `feeSchedule` for every active market and re-validate edge thresholds before resuming.
- If your bankroll grows to $2,000: increase cross-venue size cap to $100/leg and add 1h timeframe.
- If your bankroll shrinks below $300: revert to Phase 1 only; the maker strategy on Polymarket needs a $50 minimum reserve to operate reliably.

**Operational must-haves before any live trade:**
- Geoblock check at startup (`GET https://polymarket.com/api/geoblock`); refuse to start if blocked.
- Endpoint validation at startup against `docs.polymarket.com/llms.txt` and `docs.limitless.exchange/llms.txt`; fail-fast if any URL hard-coded in code is no longer listed.
- Heartbeat task on Polymarket (every 5s).
- Re-subscribe handler on every WS reconnect (Polymarket and Limitless).
- Persistent state (SQLite WAL) updated after every WS frame.
- A "kill switch" REST endpoint or signal handler that cancels all orders on both venues within 1 second.

---

## Caveats

- **The fee landscape is moving.** Polymarket changed fees three times in early 2026 (per its Changelog) and is still rolling out the QCX-acquired US-regulated venue with a separate flat-0.30% taker fee structure. Limitless is still incentivizing makers heavily under its LP rewards program in anticipation of LMTS token-related volume. Treat every fee table as a snapshot.
- **Liquidity depth is uneven and self-reported volume figures are marketing.** Limitless cites "$1B+ traded" cumulatively on its docs landing page; Kaiko Research confirms Polymarket's user distribution is overwhelmingly retail (~74% of accounts trade <$100), which means visible orderbook depth in most crypto markets does NOT support $100+ takes without slippage. The bot must size to live executable inventory, not displayed quotes.
- **Limitless has a contested customer-service incident on record** (a CoinLaunch reviewer alleging a $21,000 withholding from December 2024). The bot's withdrawal pathway and the use of EOA-held funds rather than server-wallet funds is a deliberate risk mitigation here.
- **The Polymarket 5-minute BTC market generated $60M/day** in volume one month after launch, per The Block — that's enormous for a single contract but reflects automated/HFT activity, NOT retail liquidity that a $500 bot can compete with. Treat that market as a maker-only target if you touch it at all.
- **Polymarket's published rate limits exist but the WS quotas are NOT documented in the rate-limits page.** NautilusTrader notes that Polymarket previously documented an upper bound of "500 subscriptions per connection" elsewhere — verify with Polymarket support before mass-subscribing.
- **UMA disputes are rare on objective numeric crypto markets but not impossible.** Treat resolution as "deterministic at high probability, with a 4–6 day worst-case delay if disputed." Capital tied up in a disputed market is dead weight.
- **Bankroll honesty:** at $500, you should expect the bot to be primarily an *educational and infrastructure* investment for 2–4 weeks before yielding meaningful net profit. Anyone telling you a $500 prediction-market arb bot will produce $50+/week consistently is selling you something. The realistic week-1 outcome is breakeven-to-small-loss as you tune fee handling, partial-fill logic, and oracle compatibility.
- **All endpoints and parameters in this document MUST be re-validated against the live `llms.txt` indices at build time.** Documentation drifts; addresses change; new endpoints replace deprecated ones (e.g., Polymarket recently replaced offset-based `/markets` and `/events` with keyset-based equivalents). Do not deploy hard-coded URLs without a runtime sanity check.