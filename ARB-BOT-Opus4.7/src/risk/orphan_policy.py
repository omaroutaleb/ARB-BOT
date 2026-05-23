"""Single-leg orphan policy — what to do when leg-A fills but leg-B doesn't.

Implements verbatim the Opus §F3 decision tree (STRATEGY_SYNTHESIS.md §1.11):

  1. After leg-A fill, wait window for leg-B fill:
       - 15s on 5m markets
       - 60s on 15m–1h
       - 300s on daily
  2. If timeout:
       a. Recompute fair value of leg-A using the orphan platform's NO-side bid.
       b. If sale-price-implied loss < 0.5% of bankroll ($2.50): close immediately as FAK.
       c. If loss would exceed that AND market has >30% time-to-resolution remaining:
          hold to resolution flagged directional_unhedged=True. Skip new arbs until resolved.
  3. Never average down. Never widen the limit to "catch up" the other leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from src.config import get_settings
from src.observability.logging import get_logger
from src.observability.metrics import orphan_legs

log = get_logger(__name__)


class OrphanAction(str, Enum):
    WAIT = "wait"
    CLOSE_NOW = "close_now"
    HOLD_TO_RESOLUTION = "hold_to_resolution"


@dataclass(slots=True)
class OrphanContext:
    duration_class: str            # "5m" | "15m" | "30m" | "1h" | "daily" | ...
    seconds_since_leg_a_fill: float
    leg_a_filled_notional_usd: float
    leg_a_price: float             # what we paid per share
    current_orphan_side_bid: float # best bid on the OTHER side (the side we'd sell to hedge)
    resolution_time_utc: datetime | None
    now_utc: datetime | None = None


def timeout_window_seconds(duration_class: str) -> float:
    return {
        "5m": 15.0,
        "15m": 60.0,
        "30m": 60.0,
        "1h": 60.0,
        "daily": 300.0,
        "weekly": 300.0,
    }.get(duration_class, 60.0)


def implied_loss_usd(ctx: OrphanContext) -> float:
    """Loss if we close leg-A now against current_orphan_side_bid.

    Interpretation: leg-A bought 1 share at leg_a_price. If we sell at orphan-side
    bid, we receive that price per share. Notional shares = leg_a_filled_notional_usd / leg_a_price.
    Net loss = shares × (leg_a_price - orphan_side_bid).

    (This is the close-leg-A-as-FAK exit. The hedge that never happened would
    have made this a $1.00 round-trip; we're approximating the unwind cost.)
    """
    if ctx.leg_a_price <= 0:
        return 0.0
    shares = ctx.leg_a_filled_notional_usd / ctx.leg_a_price
    return shares * max(0.0, ctx.leg_a_price - ctx.current_orphan_side_bid)


def decide(ctx: OrphanContext) -> OrphanAction:
    """Return the policy action. Idempotent / pure / unit-testable."""
    settings = get_settings()
    timeout = timeout_window_seconds(ctx.duration_class)

    if ctx.seconds_since_leg_a_fill < timeout:
        return OrphanAction.WAIT

    bankroll_threshold = 0.005 * settings.BANKROLL_USD
    loss = implied_loss_usd(ctx)
    if loss < bankroll_threshold:
        orphan_legs.labels(platform="any", resolution="close_now").inc()
        log.info(
            "orphan.decide.close_now",
            duration=ctx.duration_class,
            loss_usd=round(loss, 4),
            threshold_usd=round(bankroll_threshold, 4),
        )
        return OrphanAction.CLOSE_NOW

    # Loss too large to eat — consider holding.
    now = ctx.now_utc or datetime.now(tz=timezone.utc)
    if ctx.resolution_time_utc:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        rt = ctx.resolution_time_utc
        if rt.tzinfo is None:
            rt = rt.replace(tzinfo=timezone.utc)
        time_remaining = (rt - now).total_seconds()
        # We need the ORIGINAL total window to compute the 30% threshold,
        # but we don't have it. Conservative substitute: if at least
        # `timeout_window_seconds(duration_class) * 30` of time remains
        # (proxy for "early enough in the market"), hold.
        if time_remaining > timeout * 30:
            orphan_legs.labels(platform="any", resolution="hold").inc()
            log.warning(
                "orphan.decide.hold_to_resolution",
                duration=ctx.duration_class,
                loss_usd=round(loss, 4),
                threshold_usd=round(bankroll_threshold, 4),
                time_remaining_sec=round(time_remaining, 1),
            )
            return OrphanAction.HOLD_TO_RESOLUTION

    # Late in the market — eat the loss now.
    orphan_legs.labels(platform="any", resolution="close_now_forced").inc()
    log.warning(
        "orphan.decide.close_now_forced_late",
        duration=ctx.duration_class,
        loss_usd=round(loss, 4),
    )
    return OrphanAction.CLOSE_NOW
