# Polymarket x Limitless Arbitrage Bot

Python 3.11, Dockerized, rule-based arbitrage infrastructure for crypto prediction markets on Polymarket and Limitless. The bot defaults to the conservative Phase 1 rollout from `STRATEGY_SYNTHESIS.md`: Limitless YES/NO complementarity only. Polymarket maker quoting and cross-venue trading are gated behind Phase 1 profitability stats.

## Three-Command Deploy

```bash
git clone <repo>
cd arbitrage-bot
cp .env.example .env
```

Fill in `.env`, then run:

```bash
docker compose up -d
docker compose logs -f
```

## What Starts Up

Startup performs:

- strict endpoint validation against `docs.polymarket.com/llms.txt` and `docs.limitless.exchange/llms.txt`;
- Polymarket geoblock check via `GET https://polymarket.com/api/geoblock`;
- SQLite WAL initialization;
- startup reconciliation against live venue state;
- Prometheus metrics server on `:9090/metrics`;
- Polymarket heartbeat every 5 seconds in live mode;
- SIGINT/SIGTERM kill switch that cancels open orders on both venues within a 1 second budget.

If Polymarket reports the current IP as blocked, the process exits with code `2`. It never attempts to bypass geoblocking.

## Environment

Copy `.env.example` and set at minimum:

- `DRY_RUN=false` for live trading.
- `POLYMARKET_ADDRESS`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`.
- `LIMITLESS_API_KEY`, `LIMITLESS_PRIVATE_KEY`, `LIMITLESS_ADDRESS`.
- `STRATEGY_PHASE=1` to begin.

Risk defaults are intentionally small for a $500 bankroll:

- `MAX_POSITION_USD=40`
- `MAX_CONCURRENT_ARBS=3`
- `MAX_SINGLE_PLATFORM_EXPOSURE_USD=300`
- `DAILY_LOSS_STOP_USD=-50`
- `TOTAL_DRAWDOWN_STOP_USD=-150`

Higher phases refuse to start unless the DB shows at least 10 closed Phase 1 trades with an 80% or better profitable rate. Phase 3 also requires `ENABLE_CROSS_VENUE=true`.

## Local Verification

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/python scripts/dry_run.py --simulated-minutes 10 --speedup 600
```

Latest verification in this workspace:

```text
31 passed
Required test coverage of 80.0% reached. Total coverage: 90.40%
```

Dry-run tail from a 10 simulated-minute run:

```json
{"strategy": "yes_no_complementarity", "market": "dry-btc-daily-0", "pnl_usd": 2.839166666666667, "event": "dry_run_trade_closed", "level": "info", "timestamp": "2026-05-23T00:17:55.088716Z"}
{"strategy": "yes_no_complementarity", "market": "dry-btc-daily-1", "pnl_usd": 3.256666666666667, "event": "dry_run_trade_closed", "level": "info", "timestamp": "2026-05-23T00:17:55.091471Z"}
{"strategy": "yes_no_complementarity", "market": "dry-btc-daily-2", "pnl_usd": 4.269565217391305, "event": "dry_run_trade_closed", "level": "info", "timestamp": "2026-05-23T00:17:55.093681Z"}
{"strategy": "yes_no_complementarity", "market": "dry-btc-daily-3", "pnl_usd": 3.256666666666667, "event": "dry_run_trade_closed", "level": "info", "timestamp": "2026-05-23T00:17:55.095992Z"}
{"simulated_minutes": 10, "closed_trades": 39, "profitable_rate": 1.0, "event": "dry_run_finished", "level": "info", "timestamp": "2026-05-23T00:17:55.200196Z"}
```

## Monitoring

Metrics are exposed at:

```bash
curl http://localhost:9090/metrics
```

Important log events:

- `endpoint_validation_ok`
- `polymarket_geoblock_ok`
- `startup_reconciliation_complete`
- `bot_started`
- `dry_run_trade_closed` or `live_orders_submitted`
- `kill_switch_cancelled`
- `shutdown_complete`

## Troubleshooting

- `endpoint_validation_error`: the bot could not fetch live docs or a hard-coded endpoint was not found. This is fail-fast by design. Re-run `python scripts/validate_endpoints.py --no-strict` to inspect missing entries without changing live startup behavior.
- `polymarket_geoblocked`: the detected location is blocked. Stop; do not use a VPN or proxy bypass.
- `strategy_phase_gate_failed`: Phase 2/3 was requested before Phase 1 met the required DB stats.
- `fee_data_missing`: a market did not expose runtime fee data. The bot skips it because hard-coded fees are forbidden.
- `Limitless live order metadata missing`: the market/profile response did not include required signing data such as `venue.exchange`, `positionIds`, `feeRateBps`, or `ownerId`.

## Current Scope

Implemented:

- strict strategy synthesis contract;
- Polymarket and Limitless REST clients;
- Polymarket geoblock and heartbeat;
- Polymarket and Limitless WS reconnect/resubscribe scaffolds;
- EIP-712 signing for both venues;
- runtime fee calculator;
- market normalization and oracle compatibility;
- hard risk limits, orphan policy, and kill switch;
- SQLite WAL state plus `Trade.json` journal;
- Phase 1 dry-run and live Limitless dual-FAK submission path;
- Docker and Compose deploy.

Phase 2 and Phase 3 strategy classes are present but intentionally gated. They do not place live orders until Phase 1 profitability stats allow the bot to move forward.
