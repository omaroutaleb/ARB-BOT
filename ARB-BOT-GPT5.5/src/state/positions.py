from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4


Platform = Literal["poly", "lim"]
Side = Literal["YES", "NO"]


class PositionStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    HEDGE_FAILED = "hedge_failed"
    CLOSED = "closed"


@dataclass
class Position:
    arb_id: str
    platform: Platform
    market_key: str
    side: Side
    intended_size: float
    filled_size: float
    avg_price: float
    status: PositionStatus
    order_ids: list[str] = field(default_factory=list)
    client_order_id: str = field(default_factory=lambda: str(uuid4()))
    oracle_source: str = "unknown"
    resolution_time: datetime | None = None
    bridge_in_flight: bool = False
    directional_unhedged: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def apply_fill(self, size: float, price: float, order_id: str | None = None) -> None:
        total_cost = self.avg_price * self.filled_size + price * size
        self.filled_size += size
        self.avg_price = total_cost / self.filled_size if self.filled_size else 0.0
        if order_id and order_id not in self.order_ids:
            self.order_ids.append(order_id)
        if self.filled_size <= 0:
            self.status = PositionStatus.PENDING
        elif self.filled_size < self.intended_size:
            self.status = PositionStatus.PARTIAL
        else:
            self.status = PositionStatus.FILLED
        self.updated_at = datetime.now(timezone.utc)

    def mark_closed(self) -> None:
        self.status = PositionStatus.CLOSED
        self.updated_at = datetime.now(timezone.utc)


class PositionTracker:
    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}

    def upsert(self, position: Position) -> None:
        self.positions[position.client_order_id] = position

    def by_arb(self, arb_id: str) -> list[Position]:
        return [position for position in self.positions.values() if position.arb_id == arb_id]

    def open_positions(self) -> list[Position]:
        return [
            position
            for position in self.positions.values()
            if position.status != PositionStatus.CLOSED
        ]

