"""Entry point. Wires every module together and runs the strategy supervisor.

Startup order (STRATEGY_SYNTHESIS.md §1.14, §1.15, §1.16, Non-negotiable §4):
  1. Load config, configure logging, start metrics server
  2. Load state from Trade.json
  3. Geoblock check (refuse start if blocked)
  4. Build clients (Polymarket + Limitless)
  5. Authenticate (skip in DRY_RUN)
  6. Phase gate — decide which strategies are eligible
  7. Start heartbeat task (Polymarket — required even at Phase 1 if Polymarket WS used)
  8. Start strategy loops
  9. Wait for SIGTERM/SIGINT → kill switch → flush state → exit
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from src.config import StrategyPhase, get_settings
from src.observability.logging import configure_logging, get_logger
from src.observability.metrics import (
    bankroll_usd,
    equity_usd,
    start_metrics_server,
    strategy_phase as strategy_phase_gauge,
)
from src.platforms.limitless.client import LimitlessClient
from src.platforms.polymarket.client import PolymarketClient
from src.platforms.polymarket.geoblock import check_geoblock, GeoblockedError
from src.risk.kill_switch import cancel_all
from src.risk.limits import RiskLimits
from src.state.positions import TradeStore, get_store
from src.strategies.base import Strategy
from src.strategies.cross_venue import CrossVenueStrategy
from src.strategies.maker_rebate import MakerRebateStrategy
from src.strategies.yes_no_complementarity import YesNoComplementarityStrategy

log = get_logger("main")


# ---------- phase gate ----------

async def determine_active_phase(store: TradeStore) -> StrategyPhase:
    """Honor STRATEGY_PHASE env var, but refuse to enable higher phases if
    benchmarks not met (§1.12). Always allow Phase 1."""
    settings = get_settings()
    requested = settings.STRATEGY_PHASE
    stats = await store.stats()

    if requested == StrategyPhase.PHASE_1_SINGLE_VENUE:
        return requested

    phase2_ready = (
        stats.closed_arbs >= settings.PHASE2_MIN_CLOSED_ARBS
        and stats.win_rate() >= settings.PHASE2_MIN_WIN_RATE
        and stats.orphan_incidents == 0
    )
    phase3_ready = (
        phase2_ready and stats.maker_fills_polymarket >= settings.PHASE3_MIN_MAKER_FILLS
    )

    if requested == StrategyPhase.PHASE_2_PLUS_MAKER and not phase2_ready:
        log.warning(
            "phase_gate.phase2_blocked",
            requested=requested.value,
            closed_arbs=stats.closed_arbs,
            required_closed=settings.PHASE2_MIN_CLOSED_ARBS,
            win_rate=stats.win_rate(),
            required_win_rate=settings.PHASE2_MIN_WIN_RATE,
            orphan_incidents=stats.orphan_incidents,
        )
        return StrategyPhase.PHASE_1_SINGLE_VENUE
    if requested == StrategyPhase.PHASE_3_CROSS_VENUE and not phase3_ready:
        log.warning(
            "phase_gate.phase3_blocked",
            requested=requested.value,
            phase2_ready=phase2_ready,
            maker_fills=stats.maker_fills_polymarket,
            required_maker_fills=settings.PHASE3_MIN_MAKER_FILLS,
        )
        return StrategyPhase.PHASE_2_PLUS_MAKER if phase2_ready else StrategyPhase.PHASE_1_SINGLE_VENUE
    return requested


# ---------- main loop ----------

async def run() -> int:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    log.info("startup.begin", phase=settings.STRATEGY_PHASE.value, dry_run=settings.DRY_RUN)

    start_metrics_server(settings.METRICS_PORT)
    bankroll_usd.set(settings.BANKROLL_USD)
    equity_usd.set(settings.BANKROLL_USD)

    store = get_store()
    await store.load()
    snap = await store.bankroll()
    bankroll_usd.set(snap.bankroll_usd)
    equity_usd.set(snap.equity_usd)

    # ---- geoblock check ----
    try:
        await check_geoblock()
    except GeoblockedError as exc:
        log.error("startup.geoblocked", error=str(exc))
        return 2
    except Exception as exc:
        log.error("startup.geoblock_check_failed", error=str(exc))
        if not settings.DRY_RUN:
            return 3

    # ---- build clients ----
    poly = PolymarketClient()
    lim = LimitlessClient()
    await poly.start()
    await lim.start()

    if not settings.DRY_RUN:
        try:
            settings.require_polymarket_creds()
            settings.require_limitless_creds()
            await poly.authenticate()
            await lim.portfolio_profile()
        except Exception as exc:
            log.error("startup.auth_failed", error=str(exc))
            await poly.close()
            await lim.close()
            return 4

    # ---- phase gate ----
    active_phase = await determine_active_phase(store)
    strategy_phase_gauge.set(active_phase.value)
    log.info("startup.phase_resolved", requested=settings.STRATEGY_PHASE.value, active=active_phase.value)

    risk = RiskLimits(store)
    strategies: list[Strategy] = []
    intervals: dict[str, float] = {}

    if active_phase >= StrategyPhase.PHASE_1_SINGLE_VENUE:
        strategies.append(YesNoComplementarityStrategy(
            poly=poly, lim=lim, store=store, risk=risk, dry_run=settings.DRY_RUN,
        ))
        intervals["phase1_yes_no"] = 30.0
    if active_phase >= StrategyPhase.PHASE_2_PLUS_MAKER:
        strategies.append(MakerRebateStrategy(
            poly=poly, lim=lim, store=store, risk=risk, dry_run=settings.DRY_RUN,
        ))
        intervals["phase2_maker"] = 10.0
    if active_phase >= StrategyPhase.PHASE_3_CROSS_VENUE:
        strategies.append(CrossVenueStrategy(
            poly=poly, lim=lim, store=store, risk=risk, dry_run=settings.DRY_RUN,
        ))
        intervals["phase3_cross"] = 60.0

    stop_event = asyncio.Event()
    tasks: list[asyncio.Task] = []

    # ---- Polymarket heartbeat (only if Polymarket auth was performed) ----
    if not settings.DRY_RUN and active_phase >= StrategyPhase.PHASE_2_PLUS_MAKER:
        tasks.append(asyncio.create_task(poly.heartbeat_loop(stop_event), name="poly.heartbeat"))

    # ---- strategy tasks ----
    for strat in strategies:
        interval = intervals.get(strat.name, 30.0)
        tasks.append(asyncio.create_task(strat.run(interval), name=strat.name))

    # ---- daily PnL reset task ----
    tasks.append(asyncio.create_task(_daily_pnl_resetter(store, stop_event), name="pnl.daily_reset"))

    # ---- signal handlers ----
    loop = asyncio.get_running_loop()
    shutdown_initiated = False

    async def _shutdown(reason: str) -> None:
        nonlocal shutdown_initiated
        if shutdown_initiated:
            return
        shutdown_initiated = True
        log.warning("shutdown.initiated", reason=reason)
        stop_event.set()
        for strat in strategies:
            await strat.stop()
        try:
            await cancel_all(poly, lim, reason=reason, timeout_sec=1.0)
        except Exception as exc:
            log.error("shutdown.cancel_all_failed", error=str(exc))
        await store.flush()

    def _on_signal(signum: int) -> None:
        name = signal.Signals(signum).name
        log.warning("signal.received", signal=name)
        asyncio.create_task(_shutdown(f"signal_{name}"))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal, int(sig))
        except (NotImplementedError, RuntimeError):
            # Windows lacks add_signal_handler for SIGTERM
            signal.signal(sig, lambda s, f: asyncio.create_task(_shutdown(f"signal_{signal.Signals(s).name}")))

    log.info("startup.complete", strategies=[s.name for s in strategies])

    # ---- run until shutdown ----
    try:
        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await poly.close()
        await lim.close()
        log.info("shutdown.complete")

    return 0


async def _daily_pnl_resetter(store: TradeStore, stop: asyncio.Event) -> None:
    """Reset daily PnL counter at UTC midnight."""
    from datetime import datetime, timedelta, timezone
    while not stop.is_set():
        now = datetime.now(tz=timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep_for = (tomorrow - now).total_seconds()
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            await store.reset_daily_pnl()
            log.info("daily_pnl.reset")


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
