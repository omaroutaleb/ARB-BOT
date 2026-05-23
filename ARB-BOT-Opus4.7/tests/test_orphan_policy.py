"""Orphan-leg policy tests — the most consequential decision tree in the bot.

STRATEGY_SYNTHESIS.md §1.11.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.risk.orphan_policy import (
    OrphanAction,
    OrphanContext,
    decide,
    implied_loss_usd,
    timeout_window_seconds,
)


class TestTimeoutWindows:
    def test_5m_is_15_seconds(self):
        assert timeout_window_seconds("5m") == 15.0

    def test_15m_through_1h_is_60(self):
        assert timeout_window_seconds("15m") == 60.0
        assert timeout_window_seconds("30m") == 60.0
        assert timeout_window_seconds("1h") == 60.0

    def test_daily_is_300(self):
        assert timeout_window_seconds("daily") == 300.0

    def test_unknown_defaults_to_60(self):
        assert timeout_window_seconds("nonexistent") == 60.0


class TestImpliedLoss:
    def test_no_loss_if_bid_matches_buy_price(self):
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=999,
            leg_a_filled_notional_usd=20, leg_a_price=0.40,
            current_orphan_side_bid=0.40, resolution_time_utc=None,
        )
        assert implied_loss_usd(ctx) == 0.0

    def test_loss_proportional_to_slippage(self):
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=999,
            leg_a_filled_notional_usd=20, leg_a_price=0.40,
            current_orphan_side_bid=0.38, resolution_time_utc=None,
        )
        # 50 shares × $0.02 loss = $1.00
        assert implied_loss_usd(ctx) == pytest.approx(1.0, rel=1e-6)


class TestDecide:
    def test_inside_window_waits(self):
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=10,
            leg_a_filled_notional_usd=20, leg_a_price=0.5,
            current_orphan_side_bid=0.5, resolution_time_utc=None,
        )
        assert decide(ctx) == OrphanAction.WAIT

    def test_small_loss_closes_now(self):
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=500,
            leg_a_filled_notional_usd=20, leg_a_price=0.5,
            current_orphan_side_bid=0.48, resolution_time_utc=None,
        )
        # 40 shares × $0.02 = $0.80 < $2.50 (0.5% × $500)
        assert decide(ctx) == OrphanAction.CLOSE_NOW

    def test_large_loss_with_long_remaining_holds(self):
        now = datetime.now(tz=timezone.utc)
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=500,
            leg_a_filled_notional_usd=20, leg_a_price=0.5,
            current_orphan_side_bid=0.10,  # huge loss
            resolution_time_utc=now + timedelta(seconds=60_000),  # plenty of time
            now_utc=now,
        )
        assert decide(ctx) == OrphanAction.HOLD_TO_RESOLUTION

    def test_large_loss_with_short_remaining_forces_close(self):
        now = datetime.now(tz=timezone.utc)
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=500,
            leg_a_filled_notional_usd=20, leg_a_price=0.5,
            current_orphan_side_bid=0.10,
            resolution_time_utc=now + timedelta(seconds=120),  # short remaining
            now_utc=now,
        )
        assert decide(ctx) == OrphanAction.CLOSE_NOW

    def test_zero_price_doesnt_divide_by_zero(self):
        ctx = OrphanContext(
            duration_class="daily", seconds_since_leg_a_fill=500,
            leg_a_filled_notional_usd=20, leg_a_price=0.0,
            current_orphan_side_bid=0.5, resolution_time_utc=None,
        )
        assert decide(ctx) == OrphanAction.CLOSE_NOW   # zero loss → close now
