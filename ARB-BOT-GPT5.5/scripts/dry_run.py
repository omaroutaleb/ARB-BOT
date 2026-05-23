from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.observability.logging import configure_logging, get_logger
from src.risk.limits import RiskEngine, RiskLimits, RiskState
from src.state.db import BotDB, TradeJournal
from src.strategies.yes_no_complementarity import YesNoComplementarityStrategy


log = get_logger(__name__)


def simulated_market_books(seed: int, minute: int) -> list[dict]:
    rng = random.Random(seed + minute)
    books = []
    for index in range(4):
        yes = round(rng.uniform(0.42, 0.51), 2)
        # Force regular complementarity opportunities while keeping a few misses.
        no = round(rng.uniform(0.44, 0.50), 2)
        if index == minute % 4:
            no = round(min(no, 0.97 - yes), 2)
        books.append(
            {
                "slug": f"dry-btc-daily-{index}",
                "feeSchedule": {"feeRateBps": 20, "exponent": 1},
                "orderbook": {
                    "yesAsks": [{"price": yes, "size": 200}],
                    "noAsks": [{"price": no, "size": 200}],
                },
            }
        )
    return books


async def run_dry_run(simulated_minutes: int, speedup: float, seed: int, keep_state: bool) -> None:
    settings = Settings(
        dry_run=True,
        validate_endpoints_on_start=False,
        database_path=Path("./data/dry_run.sqlite3"),
        trade_journal_path=Path("./data/dry_run_Trade.json"),
    )
    configure_logging(settings.log_level)
    if not keep_state:
        for path in (settings.database_path, settings.trade_journal_path):
            if path.exists():
                path.unlink()
    db = BotDB(settings.database_path)
    db.init()
    journal = TradeJournal(settings.trade_journal_path)
    risk = RiskEngine(RiskLimits.from_settings(settings))
    risk_state = RiskState()
    log.info("dry_run_started", simulated_minutes=simulated_minutes, speedup=speedup)
    for minute in range(simulated_minutes):
        strategy = YesNoComplementarityStrategy(
            settings=settings,
            db=db,
            journal=journal,
            risk=risk,
            risk_state=risk_state,
            dry_run_books=simulated_market_books(seed, minute),
        )
        opportunities = await strategy.scan()
        for opportunity in opportunities:
            await strategy.execute(opportunity)
        await asyncio.sleep(60 / speedup)
    closed_count, profitable_rate = db.phase1_stats()
    log.info(
        "dry_run_finished",
        simulated_minutes=simulated_minutes,
        closed_trades=closed_count,
        profitable_rate=profitable_rate,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run accelerated paper-trading simulation")
    parser.add_argument("--simulated-minutes", type=int, default=10)
    parser.add_argument("--speedup", type=float, default=120.0)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--keep-state", action="store_true", help="Do not clear prior dry-run DB/journal")
    args = parser.parse_args()
    asyncio.run(run_dry_run(args.simulated_minutes, args.speedup, args.seed, args.keep_state))


if __name__ == "__main__":
    main()
