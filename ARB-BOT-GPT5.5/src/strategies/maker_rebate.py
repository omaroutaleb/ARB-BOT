from __future__ import annotations

from src.strategies.base import Opportunity, Strategy


class MakerRebateStrategy(Strategy):
    name = "maker_rebate"

    async def scan(self) -> list[Opportunity]:
        # Phase 2 strategy scaffold. Live quoting is disabled until Phase 1 stats
        # pass the gate and Polymarket fee/rebate metadata is fetched per market.
        return []

    async def execute(self, opportunity: Opportunity) -> None:
        raise NotImplementedError("Phase 2 maker quoting is gated behind Phase 1 profitability stats")

