from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import Any

from src.config import Settings, get_settings
from src.observability.logging import configure_logging, get_logger
from src.observability.metrics import start_metrics_server
from src.platforms.limitless.client import LimitlessClient
from src.platforms.limitless.websocket import LimitlessWebSocket
from src.platforms.polymarket.client import PolymarketClient
from src.platforms.polymarket.websocket import PolymarketWebSocket
from src.risk.kill_switch import KillSwitch
from src.risk.limits import RiskEngine, RiskLimits, RiskState
from src.state.db import BotDB, TradeJournal
from src.strategies.yes_no_complementarity import YesNoComplementarityStrategy


log = get_logger(__name__)


async def run(settings: Settings) -> int:
    configure_logging(settings.log_level)
    settings.ensure_runtime_dirs()
    start_metrics_server(settings.metrics_port)
    db = BotDB(settings.database_path)
    db.init()
    journal = TradeJournal(settings.trade_journal_path)

    if settings.validate_endpoints_on_start:
        from scripts.validate_endpoints import validate_endpoints

        try:
            validation = await validate_endpoints(settings)
        except Exception as exc:
            log.exception("endpoint_validation_error", error=str(exc))
            return 4
        if not validation["ok"]:
            log.error("endpoint_validation_failed", missing=validation["missing"])
            return 4
        log.info("endpoint_validation_ok", checked=validation["checked"])

    async with PolymarketClient(settings) as poly, LimitlessClient(settings) as lim:
        geoblock = await poly.geoblock_check()
        if geoblock.blocked:
            log.error("polymarket_geoblocked", reason=geoblock.reason, raw=geoblock.raw)
            return 2
        log.info("polymarket_geoblock_ok", reason=geoblock.reason)

        if not _phase_gate(settings, db):
            return 3

        drift_count = await reconcile_startup(settings, db, poly, lim)
        if drift_count:
            log.error("startup_reconciliation_drift", drift_count=drift_count)
            if settings.live_trading:
                return 5

        kill_switch = KillSwitch(polymarket=poly if settings.live_trading else None, limitless=lim if settings.live_trading else None)
        shutdown = asyncio.Event()
        _install_signal_handlers(shutdown)

        if settings.live_trading:
            poly.start_heartbeat()

        ws_tasks: list[asyncio.Task[None]] = []
        if settings.live_trading:
            poly_ws = PolymarketWebSocket(
                settings,
                lambda frame: _record_ws_frame(db, "polymarket", frame),
            )
            lim_ws = LimitlessWebSocket(
                settings,
                lambda frame: _record_ws_frame(db, "limitless", frame),
            )
            ws_tasks.extend(
                [
                    asyncio.create_task(poly_ws.run_market(), name="polymarket-market-ws"),
                    asyncio.create_task(lim_ws.run(), name="limitless-market-ws"),
                ]
            )
            if settings.polymarket_auth_ready:
                ws_tasks.append(asyncio.create_task(poly_ws.run_user(), name="polymarket-user-ws"))

        risk = RiskEngine(RiskLimits.from_settings(settings))
        risk_state = RiskState()
        strategy = YesNoComplementarityStrategy(
            settings=settings,
            db=db,
            journal=journal,
            risk=risk,
            risk_state=risk_state,
            limitless_client=lim,
        )
        log.info(
            "bot_started",
            dry_run=settings.dry_run,
            strategy_phase=settings.strategy_phase,
            database=str(settings.database_path),
        )

        try:
            while not shutdown.is_set():
                if settings.strategy_phase >= 1:
                    opportunities = await strategy.scan()
                    for opportunity in opportunities:
                        await strategy.execute(opportunity)
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=5)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            for task in ws_tasks:
                task.cancel()
            if ws_tasks:
                await asyncio.gather(*ws_tasks, return_exceptions=True)
            result = await kill_switch.cancel_all()
            log.info(
                "shutdown_complete",
                polymarket_cancelled=result.polymarket_cancelled,
                limitless_cancelled=result.limitless_cancelled,
                kill_switch_completed=result.completed,
            )
    return 0


async def _record_ws_frame(db: BotDB, venue: str, frame: dict[str, Any]) -> None:
    event_type = str(frame.get("event") or frame.get("type") or "unknown")
    db.record_ws_event(venue, event_type, frame)


def _phase_gate(settings: Settings, db: BotDB) -> bool:
    if settings.strategy_phase == 1:
        return True
    closed_count, profitable_rate = db.phase1_stats()
    if closed_count < 10 or profitable_rate < 0.80:
        log.error(
            "strategy_phase_gate_failed",
            requested_phase=settings.strategy_phase,
            phase1_closed_trades=closed_count,
            phase1_profitable_rate=profitable_rate,
        )
        return False
    if settings.strategy_phase == 3 and not settings.enable_cross_venue:
        log.error("strategy_phase_gate_failed", requested_phase=3, reason="ENABLE_CROSS_VENUE is false")
        return False
    log.info("strategy_phase_gate_ok", phase1_closed_trades=closed_count, phase1_profitable_rate=profitable_rate)
    return True


async def reconcile_startup(
    settings: Settings,
    db: BotDB,
    poly: PolymarketClient,
    lim: LimitlessClient,
) -> int:
    if settings.dry_run:
        db.record_reconciliation("all", 0, {"dry_run": True})
        log.info("startup_reconciliation_ok", drift_count=0, dry_run=True)
        return 0
    drift_count = 0
    payload: dict[str, Any] = {}
    try:
        payload["limitless_positions"] = await lim.positions()
    except Exception as exc:
        drift_count += 1
        payload["limitless_error"] = str(exc)
    try:
        payload["polymarket_orders"] = await poly.request("GET", settings.polymarket_clob_url, "/orders", auth=True)
    except Exception as exc:
        drift_count += 1
        payload["polymarket_error"] = str(exc)
    db.record_reconciliation("all", drift_count, payload)
    log.info("startup_reconciliation_complete", drift_count=drift_count)
    return drift_count


def _install_signal_handlers(shutdown: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: shutdown.set())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket x Limitless arbitrage bot")
    parser.add_argument("--dry-run", action="store_true", help="Force DRY_RUN=true for this process")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    if args.dry_run:
        settings.dry_run = True
    exit_code = asyncio.run(run(settings))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
