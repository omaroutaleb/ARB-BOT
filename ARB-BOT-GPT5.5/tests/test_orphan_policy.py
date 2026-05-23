import pytest

from src.risk.orphan_policy import OrphanAction, OrphanContext, evaluate_orphan, orphan_timeout_seconds


def test_wait_before_timeout() -> None:
    decision = evaluate_orphan(
        OrphanContext(
            bankroll_usd=500,
            leg_price=0.5,
            filled_size=20,
            opposite_bid=0.49,
            seconds_since_fill=10,
            timeframe="5m",
            seconds_to_resolution=200,
            total_window_seconds=300,
        )
    )
    assert decision.action == OrphanAction.WAIT


def test_close_small_loss_after_timeout() -> None:
    decision = evaluate_orphan(
        OrphanContext(
            bankroll_usd=500,
            leg_price=0.5,
            filled_size=20,
            opposite_bid=0.45,
            seconds_since_fill=16,
            timeframe="5m",
            seconds_to_resolution=200,
            total_window_seconds=300,
        )
    )
    assert decision.action == OrphanAction.CLOSE_IMMEDIATELY
    assert decision.estimated_loss_usd == pytest.approx(1.0)


def test_hold_directional_when_no_opposite_bid() -> None:
    decision = evaluate_orphan(
        OrphanContext(
            bankroll_usd=500,
            leg_price=0.5,
            filled_size=20,
            opposite_bid=None,
            seconds_since_fill=61,
            timeframe="1h",
            seconds_to_resolution=2000,
            total_window_seconds=3600,
        )
    )
    assert decision.action == OrphanAction.HOLD_DIRECTIONAL
    assert decision.directional_unhedged is True


def test_hold_directional_large_loss_with_time_remaining() -> None:
    decision = evaluate_orphan(
        OrphanContext(
            bankroll_usd=500,
            leg_price=0.7,
            filled_size=40,
            opposite_bid=0.4,
            seconds_since_fill=61,
            timeframe="1h",
            seconds_to_resolution=2400,
            total_window_seconds=3600,
        )
    )
    assert decision.action == OrphanAction.HOLD_DIRECTIONAL
    assert decision.directional_unhedged is True


def test_close_large_loss_near_resolution() -> None:
    decision = evaluate_orphan(
        OrphanContext(
            bankroll_usd=500,
            leg_price=0.7,
            filled_size=40,
            opposite_bid=0.4,
            seconds_since_fill=301,
            timeframe="daily",
            seconds_to_resolution=100,
            total_window_seconds=86400,
        )
    )
    assert decision.action == OrphanAction.CLOSE_IMMEDIATELY


def test_timeout_table() -> None:
    assert orphan_timeout_seconds("5m") == 15
    assert orphan_timeout_seconds("15m") == 60
    assert orphan_timeout_seconds("daily") == 300
