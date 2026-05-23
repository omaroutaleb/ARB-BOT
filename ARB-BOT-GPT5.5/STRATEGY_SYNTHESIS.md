# Strategy Synthesis: Polymarket x Limitless Crypto Arbitrage Bot

This file reconciles the two provided research reports:

- `GPT5.5-Deepsearch.md`
- `Opus4.7-Deepresearch.md`

Where the reports agree, the implementation treats the shared recommendation as binding. Where they disagree, the implementation chooses the more conservative option for a $500 starting bankroll and leaves explicit comments in the relevant code paths.

## 1. Concrete Technical Decisions

### Runtime and Deployment

- Language/runtime: Python 3.11+.
- Process model: one async process using `asyncio`, `aiohttp`, `websockets`, and `python-socketio`.
- Containerization: Docker multi-stage image, `python:3.11-slim`, non-root final user, ARM64-compatible.
- Deployment command path: `docker compose up -d`; Docker restart policy `unless-stopped`.
- Persistence: SQLite WAL is used for crash-safe state. A JSON trade journal named `Trade.json` is also written for the user's requested simple trade log.
- Config: `.env` with `pydantic-settings`. Default strategy phase is `1`.
- Logging: JSON logs to stdout using `structlog`.
- Metrics: Prometheus metrics server on `:9090/metrics`.
- No web dashboard, ORM, queue, Redis, Kubernetes, Celery, or LLM/AI decision-making in the trading path.

### Strategy Rollout

- Phase 1 default: Limitless-only YES/NO complementarity arbitrage.
- Phase 2: Polymarket maker-rebate quoting, enabled only after Phase 1 DB stats show at least 10 closed trades and at least 80% net-profitable rate.
- Phase 3: cross-venue arbitrage, enabled only after the same Phase 1 gate passes and `ENABLE_CROSS_VENUE=true`.
- Cross-venue is opportunistic and conservative at a $500 bankroll, not the primary profit path.
- Any higher phase requested without the gate passing causes startup refusal.

### Bankroll and Position Sizing

- Starting bankroll assumption: $500.
- Default max position per arb: `$40`.
- Default max concurrent arbs: `3`.
- Default max single-platform exposure: `$300`.
- Default reserve cash per platform: `$50`.
- Default daily loss stop: `-$50`.
- Default total drawdown stop: `-$150`.
- Default Phase 1 trade size: `$20`.
- Phase 2 Polymarket maker quote size: `$5-$10`.
- Phase 3 cross-venue size: `$20-$30` per leg, with one concurrent cross-venue arb until enough history exists.

### Edge Thresholds and Fees

- Fees are runtime data and never hard-coded as production inputs.
- Limitless fee data is pulled from market/profile runtime fields such as `feeSchedule` and `feeRateBps`.
- Polymarket fee data is pulled from live CLOB market metadata fields such as `feeRateBps`, `feeSchedule`, or equivalent SDK fields.
- If fee data is missing for a market, live trading for that market is rejected.
- Default minimum edge thresholds from the Opus report:
  - Daily: `2.0%`.
  - 1h: `2.5%`.
  - 30m: `3.0%`.
- Phase 1 Limitless complementarity gross edge default: buy YES+NO only when combined executable ask plus fees is below `0.985`; sell/mint side remains stubbed until venue conversion support is verified.
- Oracle mismatch haircut: at least `0.5%` whenever objective but different oracle feeds are compared.

### Venue Endpoints

#### Polymarket

- Geoblock: `GET https://polymarket.com/api/geoblock`; blocked startup exits with code `2`.
- Discovery:
  - `https://gamma-api.polymarket.com/markets`
  - `https://gamma-api.polymarket.com/events`
  - keyset variants when present in docs.
- CLOB:
  - `https://clob.polymarket.com/book`
  - `https://clob.polymarket.com/midpoint`
  - `https://clob.polymarket.com/price`
  - `https://clob.polymarket.com/spread`
  - `https://clob.polymarket.com/prices-history`
  - `https://clob.polymarket.com/order`
  - `https://clob.polymarket.com/orders`
  - `https://clob.polymarket.com/cancel-all`
  - `https://clob.polymarket.com/cancel-market-orders`
  - `https://clob.polymarket.com/heartbeat`
- WebSockets:
  - Market: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
  - User: `wss://ws-subscriptions-clob.polymarket.com/ws/user`
  - Optional crypto reference stream: `wss://ws-live-data.polymarket.com`
- Startup heartbeat task sends every `5s`.
- WebSocket reconnect performs full resubscription.
- Tick size is fetched per token ID and cached with short TTL; tick sizes may be `0.1`, `0.01`, `0.001`, or `0.0001`.

#### Limitless

- Base REST URL: `https://api.limitless.exchange`.
- Discovery:
  - `GET /markets/active`
  - `GET /markets/active/slugs`
  - `GET /markets/{slug}`
  - `GET /markets/search`
- Market data:
  - `GET /markets/{slug}/orderbook`
  - `GET /markets/{slug}/historical-price`
  - `GET /markets/{slug}/oracle-candles`
  - `GET /markets/{slug}/events`
- Trading:
  - `POST /orders`
  - `DELETE /orders/{orderId}`
  - `POST /orders/cancel-batch`
  - `DELETE /orders/all/{slug}`
  - `POST /orders/status/batch`
- Portfolio:
  - `GET /portfolio/profile`
  - `GET /portfolio/positions`
  - `GET /portfolio/trades`
  - `GET /portfolio/allowance`
  - `POST /portfolio/redeem`
  - `POST /portfolio/withdraw`
- WebSocket:
  - `wss://ws.limitless.exchange`, Socket.IO namespace `/markets`.
  - Events include market prices, orderbook updates, positions, order events, transactions, and market resolution.
- On reconnect, all subscriptions are sent again.
- `subscribe_market_prices` replaces the previous subscription set, so the bot always sends the full union.
- Tick size: conservative implementation uses `0.01` and price bounds `0.01-0.99`.

### Authentication and Signing

#### Polymarket

- L1 wallet signs to derive L2 API credentials.
- Authenticated REST requests attach `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_API_KEY`, and `POLY_PASSPHRASE`.
- Order payloads are signed separately using EIP-712.
- Private keys, API secrets, passphrases, HMAC material, and full signatures are never logged.
- Deposit-wallet/proxy `signatureType` is configurable; default is conservative EOA unless configured otherwise.

#### Limitless

- REST and Socket.IO use `X-API-Key: lmts_...`.
- Orders are signed using EIP-712 against per-market `venue.exchange`.
- Domain: name `Limitless CTF Exchange`, version `1`, chain ID `8453`.
- Order fields include `salt`, `maker`, `signer`, `taker`, `tokenId`, `makerAmount`, `takerAmount`, `expiration`, `nonce`, `feeRateBps`, `side`, and `signatureType`.
- The bot fetches `venue.exchange` and fee fields from market/profile runtime data before signing.

### Order Policy

- Default inventory orders: `GTC` with `postOnly=true`.
- Hedge legs: `FAK` marketable limits with explicit price caps.
- `FOK` is disabled by policy at a $500 bankroll.
- Partial fill rule: hedge only the filled size, never the intended size.
- If hedge partially fills, flatten the residual with bounded `FAK`.
- Never average down and never widen a hedge beyond risk limits.

### Oracles and Market Matching

- Market matching is based on normalized rule signatures, not title similarity.
- Canonical signature fields:
  - underlying asset
  - direction/comparison operator
  - strike or delta-vs-open structure
  - window start and end
  - timezone-normalized expiry
  - oracle
  - price pair/source
  - tie rule
  - payout rule
  - token/outcome IDs
- Pure spatial arbitrage requires exact signature parity.
- Objective but non-identical feeds, such as Chainlink vs Pyth, are treated as compatible only for relative value with an oracle mismatch buffer.
- UMA vs Pyth is not treated as hard-arb compatible.
- Polymarket 5m "Up or Down" delta-from-open markets are not matched with Limitless fixed-strike markets.
- Time tolerances:
  - 5m/15m/30m: maximum expiry difference `5 minutes`.
  - 1h/daily: maximum expiry difference `1 hour`.
- Strike tolerance for relative value: maximum `0.5%`.
- Normalized market cache TTL: `60s`.

### Orphan-Leg Policy

If leg A fills and leg B does not fill within the configured timeout:

- Timeout by timeframe:
  - 5m: `15s`.
  - 15m/30m/1h: `60s`.
  - daily: `300s`.
- Recompute fair value using the orphan platform's opposite-side executable bid.
- If flattening loss is less than `0.5%` of bankroll (`$2.50` by default), close immediately with `FAK`.
- If flattening loss is larger and more than `30%` of time to resolution remains, hold as `directional_unhedged=True` and block new arbs until resolved or manually cleared.
- Otherwise close immediately, even at larger loss, to avoid final-window directional exposure.
- Never average down.
- Never widen the hedge limit to chase a failed leg.

### State, Recovery, and Reconciliation

- SQLite WAL mode is enabled.
- Every WebSocket frame that changes order or position state is persisted before the next trading action.
- Startup reconciles DB state against live exchange state.
- Reconciliation logs drift and refuses live trading if unresolved drift exceeds configured tolerance.
- `Trade.json` mirrors closed trade summaries for simple inspection.

### Kill Switch

- SIGINT and SIGTERM trigger cancel-all on both venues.
- The cancel fanout is bounded to complete within `1s`.
- The bot logs `cancelled N orders on each venue` before exit when venue responses are available.
- If cancellation fails, the exception is structured-logged and the process exits non-zero after the timeout.

### Endpoint Validation

- `scripts/validate_endpoints.py` fetches:
  - `https://docs.polymarket.com/llms.txt`
  - `https://docs.limitless.exchange/llms.txt`
- Hard-coded production endpoint paths are checked against the live docs indices.
- Missing docs entries fail validation unless the endpoint is explicitly marked as an allowed operational special case, such as Polymarket geoblock.

## 2. Disagreements and Conservative Choices

### Cross-Venue 5m/15m Viability

- GPT5.5 view: 5m and 15m BTC/ETH Chainlink-style markets may be the best candidates for true rule-parity spatial arbitrage.
- Opus4.7 view: Polymarket's high-volume 5m product is delta-from-open and Limitless products are often fixed-strike/Pyth; cross-venue is marginal at $500 and should be Phase 3.
- Chosen implementation: Opus4.7's conservative rollout. The matcher can represent strict 5m/15m parity when live metadata proves it, but Phase 1 defaults to Limitless-only and Phase 3 is gated.

### Oracle Compatibility

- GPT5.5 view: 5m/15m examples can be Chainlink-compatible; objective feeds can sometimes be hard mirrored.
- Opus4.7 view: Polymarket often uses UMA/Chainlink while Limitless uses Pyth; mismatches create tail risk.
- Chosen implementation: exact same oracle is required for hard arb. Chainlink-vs-Pyth is only relative value with a haircut. UMA-vs-Pyth is not hard-compatible.

### Best Starting Strategy

- GPT5.5 view: primary strategy is matched short-dated spatial arb when exact parity exists.
- Opus4.7 view: with $500, the viable starting strategy is single-venue Limitless YES/NO complementarity, then Polymarket maker rebates, then cross-venue.
- Chosen implementation: Phase 1 Limitless-only by default, with hard phase gates.

### Persistence Format

- User brief requests `Trade.json`.
- Deliverable tree also requests SQLite WAL state.
- Chosen implementation: SQLite WAL is authoritative for crash recovery; `Trade.json` is a secondary append-style journal for user-facing trade summaries.

### Limitless Rate Limits

- GPT5.5 view: numeric rate limits were not published in reviewed docs.
- Opus4.7 view: use 2 concurrent requests and 300ms minimum delay.
- Chosen implementation: the client enforces the conservative 2-concurrent/300ms throttle by default while keeping values configurable.

### Polymarket Fees

- GPT5.5 view: multiple fee representations exist; fetch per-market fee data.
- Opus4.7 view: 2026 crypto fee model peaks near 1.8%, but also says to fetch `feeRateBps` and `feeSchedule`.
- Chosen implementation: no fee constants in trade decisions; all production fees come from runtime market/profile data.

## 3. Open Questions and Defensive Handling

- Polymarket CTF Exchange V2/pUSD approval address:
  - Defensive handling: do not submit live orders unless market metadata exposes the needed exchange/fee fields or SDK signing confirms a valid target.
- Limitless absolute minimum order size:
  - Defensive handling: use market `minSize` when available; otherwise dry-run/probe mode surfaces missing value and live mode rejects markets without a known minimum.
- Limitless withdrawal fee:
  - Defensive handling: withdrawal automation is not part of the trading hot path; treasury functions log TODO and avoid assuming a fee.
- Limitless `feeRateBps` exact source:
  - Defensive handling: prefer market fee schedule; fall back to profile `feeRateBps`; reject live orders if neither exists.
- Polymarket Chainlink 5m timestamp tolerance:
  - Defensive handling: strict signature matching requires explicit rule metadata; titles alone cannot create a hard-arb match.
- UMA dispute behavior on objective crypto markets:
  - Defensive handling: UMA markets are marked settlement-delayed and are not considered deterministic hard-arb parity against Pyth.
- Cross-platform close-only/geoblock status:
  - Defensive handling: geoblock failure blocks new live trading. Recovery/close-only paths are surfaced but not used to bypass restrictions.
- Polymarket maker rebate details for partial fills:
  - Defensive handling: maker rebates are tracked as upside only until confirmed by trade/rebate API records; strategies must be profitable before unconfirmed rebates.
- Bridge route reliability:
  - Defensive handling: bridges are treasury-only; the execution engine never assumes bridge completion inside a trade cycle.
- Endpoint doc drift:
  - Defensive handling: startup validation can fail fast before live trading, and scripts report any hard-coded endpoint not found in live `llms.txt`.

