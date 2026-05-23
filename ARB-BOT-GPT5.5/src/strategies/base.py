from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Opportunity:
    strategy: str
    venue: str
    market_key: str
    edge: Decimal
    size_usd: float
    payload: dict[str, Any]


class Strategy(ABC):
    name: str

    @abstractmethod
    async def scan(self) -> list[Opportunity]:
        """Return currently actionable opportunities."""

    @abstractmethod
    async def execute(self, opportunity: Opportunity) -> None:
        """Execute or simulate an opportunity."""

