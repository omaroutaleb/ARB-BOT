from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OrphanAction(StrEnum):
    WAIT = "wait"
    CLOSE_IMMEDIATELY = "close_immediately"
    HOLD_DIRECTIONAL = "hold_directional"


@dataclass(frozen=True)
class OrphanContext:
    bankroll_usd: float
    leg_price: float
    filled_size: float
    opposite_bid: float | None
    seconds_since_fill: float
    timeframe: str
    seconds_to_resolution: float
    total_window_seconds: float


@dataclass(frozen=True)
class OrphanDecision:
    action: OrphanAction
    reason: str
    estimated_loss_usd: float
    directional_unhedged: bool = False


def orphan_timeout_seconds(timeframe: str) -> int:
    normalized = timeframe.lower()
    if normalized == "5m":
        return 15
    if normalized in {"15m", "30m", "1h", "hourly", "hour"}:
        return 60
    return 300


def evaluate_orphan(ctx: OrphanContext) -> OrphanDecision:
    timeout = orphan_timeout_seconds(ctx.timeframe)
    if ctx.seconds_since_fill < timeout:
        return OrphanDecision(OrphanAction.WAIT, "hedge timeout has not elapsed", 0.0)
    if ctx.opposite_bid is None:
        return OrphanDecision(
            OrphanAction.HOLD_DIRECTIONAL,
            "no executable opposite-side bid; block new arbs",
            ctx.leg_price * ctx.filled_size,
            directional_unhedged=True,
        )
    estimated_loss = max(0.0, (ctx.leg_price - ctx.opposite_bid) * ctx.filled_size)
    small_loss_threshold = ctx.bankroll_usd * 0.005
    if estimated_loss < small_loss_threshold:
        return OrphanDecision(
            OrphanAction.CLOSE_IMMEDIATELY,
            "flattening loss is below 0.5% bankroll",
            estimated_loss,
        )
    remaining_fraction = (
        ctx.seconds_to_resolution / ctx.total_window_seconds
        if ctx.total_window_seconds > 0
        else 0.0
    )
    if remaining_fraction > 0.30:
        return OrphanDecision(
            OrphanAction.HOLD_DIRECTIONAL,
            "loss is large and more than 30% of window remains",
            estimated_loss,
            directional_unhedged=True,
        )
    return OrphanDecision(
        OrphanAction.CLOSE_IMMEDIATELY,
        "final-window risk overrides loss threshold",
        estimated_loss,
    )

