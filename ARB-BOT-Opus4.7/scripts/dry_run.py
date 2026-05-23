"""Paper-trading driver. Boots the full bot with DRY_RUN=true forced on.

Used as the brief's "Definition of Done" verification step — must run for
≥10 minutes without error and log what trades WOULD have been submitted.

Usage:
    python -m scripts.dry_run [seconds=600]
"""

from __future__ import annotations

import asyncio
import os
import sys

# Force dry-run BEFORE settings is imported.
os.environ["DRY_RUN"] = "true"

from src.config import reload_settings        # noqa: E402
from src.main import run                      # noqa: E402
from src.observability.logging import configure_logging, get_logger  # noqa: E402


async def _bounded(duration_sec: float) -> int:
    """Run main and stop it after `duration_sec`."""
    main_task = asyncio.create_task(run(), name="dry_run.main")

    async def _stopper() -> None:
        await asyncio.sleep(duration_sec)
        # Politely cancel: SIGINT-like shutdown
        for task in asyncio.all_tasks():
            if task is asyncio.current_task() or task is main_task:
                continue
            if task.get_name() in ("phase1_yes_no", "phase2_maker", "phase3_cross",
                                    "poly.heartbeat", "pnl.daily_reset"):
                task.cancel()
        main_task.cancel()

    stopper = asyncio.create_task(_stopper(), name="dry_run.stopper")
    try:
        rc = await main_task
    except asyncio.CancelledError:
        rc = 0
    stopper.cancel()
    return rc or 0


def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    reload_settings()
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    log = get_logger("dry_run")
    log.info("dry_run.start", duration_sec=duration)
    rc = asyncio.run(_bounded(duration))
    log.info("dry_run.end", duration_sec=duration, rc=rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
