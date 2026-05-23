# arb-bot-cross

Cross-platform crypto prediction-market arbitrage bot — **Polymarket (Polygon) × Limitless Exchange (Base)**, sized for a **$500 starting bankroll**.

The bot follows a strict phased rollout. Phase 1 trades only on Limitless (the genuinely riskless single-venue YES + NO complementarity arbitrage). Phase 2 adds Polymarket maker-rebate harvesting. Phase 3 enables cross-venue arbitrage on daily strike-form BTC markets — but only after Phases 1 and 2 have proven profitable.

> **Required reading before running anything:** [`STRATEGY_SYNTHESIS.md`](STRATEGY_SYNTHESIS.md). It is the contract this entire codebase is built against, reconciling [`research/Opus4.7-Deepresearch.md`](research/Opus4.7-Deepresearch.md) and [`research/GPT5.5-Deepresearch.md`](research/GPT5.5-Deepresearch.md).

---

## Deploy (3 commands)

On any Docker host — including ARM64 like an Oracle Cloud Ampere VM.

```bash
git clone <your-repo-url>
cd arb-bot-cross
cp .env.example .env    # then edit .env with real API keys + wallets
docker compose up -d
```

Watch it work:

```bash
docker compose logs -f
```

That's the whole deploy story.

Updates are manual:

```bash
git pull && docker compose up -d --build
```

---

## What you need before first run

| Item | Where to get it | Notes |
|---|---|---|
| **Polygon wallet** | Generate an EOA or use an existing Polymarket proxy wallet | Funded with at least $225 USDC and small amount of POL for gas if `POLY_SIGNATURE_TYPE=0` |
| **Polymarket API credentials** | The bot derives these from your private key on first run. No manual step. | The L1 EIP-712 signature flow creates `apiKey/secret/passphrase` via `POST /auth/api-key`. |
| **Base wallet** | Generate an EOA (can be the same key as Polygon if you wish) | Funded with at least $225 USDC on Base |
| **Limitless API key** | Profile → API Keys → "Create" in the Limitless web UI | Format `lmts_…` |
| **$50 reserve** | Optional float kept un-bridged | For emergency top-ups |
| **Polymarket geo eligibility** | The bot checks `GET https://polymarket.com/api/geoblock` at startup. If your IP is in a blocked jurisdiction (US, UK, EU, etc.) the bot refuses to start. **Do not attempt to bypass.** | |

---

## Configuration reference

All settings live in `.env`. The full list with defaults is in [`.env.example`](.env.example); the most important knobs:

| Variable | Default | What it does |
|---|---|---|
| `DRY_RUN` | `true` | When true, no real orders are submitted. **Always start here.** |
| `STRATEGY_PHASE` | `1` | `1` Limitless YES+NO only · `2` adds Polymarket maker · `3` adds cross-venue. Higher phases auto-block unless prior-phase benchmarks are met. |
| `BANKROLL_USD` | `500` | All risk thresholds scale from this. |
| `MAX_POSITION_USD` | `40` | Per-arb cap (8% of bankroll). |
| `MAX_CONCURRENT_ARBS` | `3` | Total open arbs at once. |
| `MAX_PLATFORM_EXPOSURE_USD` | `300` | Per-platform exposure cap (60%). |
| `MIN_PLATFORM_RESERVE_USD` | `50` | Mandatory reserve kept on each platform. |
| `MIN_EDGE_*_PCT` | `2.0 / 2.5 / 3.0` | Minimum net edge by duration class. |
| `DAILY_LOSS_STOP_USD` | `50` | Halts new trades for 24h after this daily loss. |
| `TOTAL_DRAWDOWN_STOP_USD` | `150` | Triggers full kill switch. |
| `POLY_PRIVATE_KEY` | — | **Required when `DRY_RUN=false`.** Never commit. |
| `LIMITLESS_API_KEY` | — | **Required when `DRY_RUN=false`.** Never commit. |
| `LIMITLESS_PRIVATE_KEY` | — | **Required when `DRY_RUN=false`.** Different key allowed; same is fine. |

Phase-promotion benchmarks (`PHASE2_MIN_CLOSED_ARBS`, `PHASE2_MIN_WIN_RATE`, `PHASE3_MIN_MAKER_FILLS`) are also in `.env.example`.

---

## What it does, by phase

### Phase 1 — single-venue YES + NO complementarity (Limitless)

When `ask(YES) + ask(NO) ≤ 0.985` on a Crypto market with ≥ $1000 24h volume, the bot buys both legs as `FAK` orders. At resolution, 1 YES + 1 NO redeems for exactly $1.00. Net edge = `1 − sum_asks − fees`. There is no cross-chain or oracle risk in this strategy.

### Phase 2 — Polymarket maker rebate (15m BTC)

Quote tiny `GTC postOnly` orders 1 tick inside best bid on Polymarket BTC 15m markets. Maker rebate is ~20% of taker fee on Crypto category, and 15m markets typically compress to $0.92–$0.99 in the last 30 seconds. Net edge = rebate + convergence.

### Phase 3 — cross-venue (daily BTC strike-form only)

Match Polymarket × Limitless daily markets by canonical signature: same asset, same direction (≥ / ≤), strikes within 0.5%, expiries within 1 hour, **both oracles in the cross-venue compatible set** (Chainlink BTC/USD × Pyth BTC/USD; Chainlink ETH/USD × Pyth ETH/USD). Apply a 0.5% oracle-divergence haircut, then require ≥ 3% net edge. Leg-A is `postOnly` GTC; if filled, leg-B is `FAK` with a 60-second orphan-policy timeout.

---

## Phase gating (the safety net)

Higher phases refuse to enable unless prior-phase benchmarks pass:

| Promotion | Requires |
|---|---|
| Phase 1 → 2 | ≥ 10 closed arbs · ≥ 80% net-profitable · 0 orphan incidents |
| Phase 2 → 3 | Phase 2 satisfied · ≥ 30 Polymarket maker fills |

If you set `STRATEGY_PHASE=3` before benchmarks are met, the bot logs a `phase_gate.phase3_blocked` warning and falls back to the highest unlocked phase. Stats are read from `state/Trade.json`.

---

## Observability

### Logs

JSON to stdout, redacted for secrets. Example tail from a dry-run start:

```
{"duration_sec": 25.0, "event": "dry_run.start", "level": "info", "timestamp": "2026-05-23T00:29:54Z"}
{"phase": 1, "dry_run": true, "event": "startup.begin", "level": "info", "timestamp": "2026-05-23T00:29:54Z"}
{"path": "state/Trade.json", "event": "state.initialized", "level": "info", "timestamp": "2026-05-23T00:29:54Z"}
{"blocked": false, "country": "MA", "event": "polymarket.geoblock.checked", "level": "info", "timestamp": "2026-05-23T00:29:54Z"}
{"requested": 1, "active": 1, "event": "startup.phase_resolved", "level": "info", "timestamp": "2026-05-23T00:29:54Z"}
{"strategies": ["phase1_yes_no"], "event": "startup.complete", "level": "info", "timestamp": "2026-05-23T00:29:54Z"}
{"event": "shutdown.complete", "level": "info", "timestamp": "2026-05-23T00:30:19Z"}
{"duration_sec": 25.0, "rc": 0, "event": "dry_run.end", "level": "info", "timestamp": "2026-05-23T00:30:19Z"}
```

### Metrics

Prometheus on `:9090/metrics`. Scrape from any Prometheus-compatible system. Key series:

- `arbbot_orders_submitted_total{platform,side,strategy}`
- `arbbot_orders_filled_total{platform,side,strategy}`
- `arbbot_orders_rejected_total{platform,code}`
- `arbbot_open_positions{platform}`
- `arbbot_bankroll_usd`, `arbbot_equity_usd`, `arbbot_drawdown_usd`
- `arbbot_orphan_legs_total{platform,resolution}`
- `arbbot_kill_switch_triggers_total{reason}`
- `arbbot_heartbeats_sent_total` (Polymarket)

---

## Operational basics

### Pre-deploy endpoint validation

```bash
docker compose run --rm arbbot python -m scripts.validate_endpoints
```

This fetches `docs.polymarket.com/llms.txt` and `docs.limitless.exchange/llms.txt` and verifies every hard-coded URL is still listed. Exit code 1 on drift. Run before each deploy.

### Paper trading

```bash
docker compose run --rm arbbot python -m scripts.dry_run 600
```

Runs the full strategy loop for 600 seconds with order submission disabled. Logs what it WOULD have done.

### Tests

Local (no Docker):

```bash
python -m venv .venv
.venv/Scripts/activate    # Linux/Mac: source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ --cov=src --cov-report=term-missing
```

Coverage on the safety-critical modules:

| Module | Coverage |
|---|---|
| `src/risk/limits.py` | 87% |
| `src/risk/orphan_policy.py` | 96% |
| `src/risk/kill_switch.py` | 91% |
| `src/fees/calculator.py` | 93% |

83 tests pass total.

### Kill switch

`SIGTERM` or `SIGINT` (e.g. `docker compose down`, Ctrl-C) triggers a cancel-all on both venues within 1 second, then flushes state and exits. Verified by `tests/test_kill_switch.py::TestCancelAll::test_completes_under_one_second`.

You can also stop with `docker compose stop` — the compose file's `stop_grace_period: 10s` gives the kill switch time to complete.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Exit code `2` at startup, `startup.geoblocked` log | Your IP is in a Polymarket-blocked country | Run from a non-blocked jurisdiction. **Do not** VPN around it — ToS violation, fund freeze risk. |
| Exit code `3` at startup, `startup.geoblock_check_failed` | Couldn't reach `polymarket.com/api/geoblock` | Check network egress / firewall. The bot fails closed. |
| Exit code `4`, `startup.auth_failed` | Bad / missing private keys or API key | Verify `.env` values. Polymarket needs both `POLY_PRIVATE_KEY` and `POLY_WALLET_ADDRESS`. Limitless needs `LIMITLESS_API_KEY` + `LIMITLESS_PRIVATE_KEY` + `LIMITLESS_WALLET_ADDRESS`. |
| `phase_gate.phaseN_blocked` | You set a higher `STRATEGY_PHASE` than benchmarks allow | Either reduce `STRATEGY_PHASE` or let the lower phase accumulate stats first. The state file is `state/Trade.json`. |
| Many `ws.reconnect` events | Network issue or venue maintenance | Bot will auto-recover. If sustained > 5 min, check venue status pages. |
| Polymarket orders auto-cancel after ~15s | Heartbeat task isn't running | Heartbeat only runs at Phase 2+ and `DRY_RUN=false`. If running Phase 2 and you see this, check logs for `polymarket.heartbeat.failed`. |
| `state.corrupt_recovered` log | `Trade.json` was malformed (crashed mid-write) | The bot moved it to a `.corrupt.<timestamp>.json` backup and started fresh. Inspect the backup if you need history. |

---

## What the bot will NOT do

Verbatim from `STRATEGY_SYNTHESIS.md` §4:

1. Hard-code fees, tick sizes, addresses, oracle sources, min order sizes, or rate limits.
2. Match markets by ticker + expiry alone.
3. Treat UMA and Pyth as equivalent oracles.
4. Trust visible orderbook depth (uses `FAK` with price cap, never `FOK` at $500 bankroll).
5. Poll for orderbook updates instead of WS.
6. Bridge to complete a hedge (pre-funded inventory only).
7. Use double market orders (default = maker on at least one leg).
8. Subscribe partially on WS reconnect.
9. Trade markets without sufficient depth (filter: 24h volume ≥ 10× position size).
10. Treat `RESOLVED` as redeemable (verifies `payoutDenominator > 0` on-chain first).
11. Skip Polymarket heartbeat.
12. Commit secrets.
13. Catch exceptions silently.
14. Add LLM / sentiment in the hot path.
15. Bypass geoblock with VPN.

---

## File layout

```
arb-bot-cross/
├── README.md                       # this file
├── STRATEGY_SYNTHESIS.md           # ground truth — read first
├── docker-compose.yml
├── Dockerfile                      # multi-stage, non-root, ARM64-compatible
├── .env.example                    # all env vars documented
├── pyproject.toml                  # pinned versions
├── research/                       # the two input reports
├── src/
│   ├── main.py                     # entry point + signal handlers + phase gate
│   ├── config.py                   # pydantic-settings
│   ├── platforms/
│   │   ├── polymarket/             # REST + WS + EIP-712 + geoblock + heartbeat
│   │   └── limitless/              # REST + Socket.IO + EIP-712
│   ├── strategies/
│   │   ├── yes_no_complementarity.py
│   │   ├── maker_rebate.py
│   │   └── cross_venue.py
│   ├── matching/                   # canonical rule signature + pair finder
│   ├── risk/                       # limits + orphan policy + kill switch
│   ├── state/                      # Trade.json store
│   ├── fees/                       # runtime fee math
│   ├── oracles/                    # compatibility matrix
│   └── observability/              # structlog + prometheus
├── tests/                          # pytest — risk + fees + matcher + EIP-712
└── scripts/
    ├── validate_endpoints.py       # pre-deploy llms.txt drift check
    └── dry_run.py                  # paper-trading
```

---

## Expectations at $500

Honest numbers, lifted from the research synthesis:

- **Phase 1 only:** $5–$25/week
- **Phase 1 + 2:** $10–$40/week
- **Phase 3 added:** typically +$0–$10/week incremental, mostly diversification

Week 1 is usually breakeven-to-small-loss as fee handling, partial-fill logic, and oracle compatibility get tuned to live conditions. Treat the first 2–4 weeks as an infrastructure investment.

If you grow to ~$2,000 bankroll, raise the cross-venue size cap to $100/leg in `.env` and add the 1h timeframe. If you fall below $300, revert to Phase 1 only.

---

## License

Proprietary. See your repo's terms.
