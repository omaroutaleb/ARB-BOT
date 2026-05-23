from __future__ import annotations

from src.strategies.base import Opportunity, Strategy


class CrossVenueStrategy(Strategy):
    name = "cross_venue"

    async def scan(self) -> list[Opportunity]:
        # Conservative choice from STRATEGY_SYNTHESIS.md: one report favored
        # short-window cross-venue parity, while the other found $500 cross-venue
        # marginal. This remains gated until Phase 1 has >=10 closed trades and
        # >=80% net-profitable rate, and live metadata proves strict rule parity.
        return []

    async def execute(self, opportunity: Opportunity) -> None:
        raise NotImplementedError("Phase 3 cross-venue trading is gated and not active by default")

