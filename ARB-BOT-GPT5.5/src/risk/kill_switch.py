from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from src.observability.logging import get_logger


log = get_logger(__name__)


class CancelAllClient(Protocol):
    async def cancel_all(self) -> int: ...


@dataclass(frozen=True)
class KillSwitchResult:
    polymarket_cancelled: int
    limitless_cancelled: int
    completed: bool


class KillSwitch:
    def __init__(
        self,
        *,
        polymarket: CancelAllClient | None,
        limitless: CancelAllClient | None,
        timeout_seconds: float = 1.0,
    ):
        self.polymarket = polymarket
        self.limitless = limitless
        self.timeout_seconds = timeout_seconds

    async def cancel_all(self) -> KillSwitchResult:
        async def cancel(client: CancelAllClient | None, venue: str) -> int:
            if client is None:
                return 0
            try:
                return await client.cancel_all()
            except Exception:
                log.exception("kill_switch_cancel_failed", venue=venue)
                raise

        try:
            poly, lim = await asyncio.wait_for(
                asyncio.gather(
                    cancel(self.polymarket, "polymarket"),
                    cancel(self.limitless, "limitless"),
                ),
                timeout=self.timeout_seconds,
            )
            log.info(
                "kill_switch_cancelled",
                polymarket_cancelled=poly,
                limitless_cancelled=lim,
            )
            return KillSwitchResult(poly, lim, True)
        except Exception:
            log.exception("kill_switch_failed")
            return KillSwitchResult(0, 0, False)

