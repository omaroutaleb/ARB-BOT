"""Abstract strategy interface. Each phase implements this.

The supervisor in `main.py` is phase-gated: it only spins up strategies whose
prerequisites are met (see STRATEGY_SYNTHESIS.md §1.12).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from src.observability.logging import get_logger
from src.platforms.limitless.client import LimitlessClient
from src.platforms.polymarket.client import PolymarketClient
from src.risk.limits import RiskLimits
from src.state.positions import TradeStore


class Strategy(ABC):
    name: str = "abstract"

    def __init__(
        self,
        *,
        poly: PolymarketClient | None,
        lim: LimitlessClient | None,
        store: TradeStore,
        risk: RiskLimits,
        dry_run: bool,
    ) -> None:
        self.poly = poly
        self.lim = lim
        self.store = store
        self.risk = risk
        self.dry_run = dry_run
        self._stop = asyncio.Event()
        self.log = get_logger(self.name)

    @abstractmethod
    async def tick(self) -> None:
        """One iteration of the strategy loop. Called on schedule by the supervisor."""

    async def stop(self) -> None:
        self._stop.set()

    async def run(self, interval_sec: float) -> None:
        """Default supervisor loop. Strategies can override for event-driven cadence."""
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                self.log.exception("strategy.tick.error", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_sec)
            except asyncio.TimeoutError:
                continue
