from decimal import Decimal

import pytest

from src.fees.calculator import (
    FeeDataMissing,
    FeeSchedule,
    calculate_fee,
    effective_edge_after_fees,
    total_fees,
)


def test_polymarket_fee_uses_runtime_schedule() -> None:
    schedule = FeeSchedule.from_market(
        "polymarket",
        {"feeSchedule": {"feeRate": "0.072", "exponent": "1"}},
    )
    fee = calculate_fee(schedule, side="BUY", role="taker", price=0.5, size=100)
    assert fee == Decimal("0.90000")


def test_limitless_curve_fee_interpolates_runtime_points() -> None:
    schedule = FeeSchedule.from_market(
        "limitless",
        {
            "feeSchedule": {
                "buy": {"points": [{"price": "0.01", "bps": "40"}, {"price": "0.50", "bps": "300"}]},
                "sell": {"points": [["0.01", "42"], ["0.50", "150"]]},
            }
        },
    )
    fee = calculate_fee(schedule, side="BUY", role="taker", price=0.5, size=100)
    assert fee == Decimal("1.500")


def test_limitless_curve_uses_flat_runtime_bps_when_no_points() -> None:
    schedule = FeeSchedule.from_market(
        "limitless",
        {"feeSchedule": {"sell": {"feeRateBps": "100"}}},
    )
    fee = calculate_fee(schedule, side="SELL", role="taker", price=0.25, size=20)
    assert fee == Decimal("0.0500")


def test_maker_fee_defaults_to_zero_from_runtime_schedule() -> None:
    schedule = FeeSchedule.from_market("limitless", {"feeSchedule": {"feeRateBps": 25}})
    assert calculate_fee(schedule, side="BUY", role="maker", price=0.5, size=100) == Decimal("0.00")


def test_missing_fee_data_is_rejected() -> None:
    with pytest.raises(FeeDataMissing):
        FeeSchedule.from_market("polymarket", {"slug": "missing-fees"})


def test_bad_curve_without_bps_is_rejected() -> None:
    schedule = FeeSchedule.from_market("limitless", {"feeSchedule": {"buy": {"points": []}}})
    with pytest.raises(FeeDataMissing):
        calculate_fee(schedule, side="BUY", role="taker", price=0.5, size=1)


def test_total_fees_and_net_edge() -> None:
    schedule = FeeSchedule.from_market("polymarket", {"feeSchedule": {"feeRateBps": 100, "exponent": 1}})
    fees = total_fees(
        [
            (schedule, "BUY", "taker", 0.4, 50),
            (schedule, "SELL", "maker", 0.6, 50),
        ]
    )
    assert fees > 0
    edge = effective_edge_after_fees(
        sell_price=0.55,
        buy_price=0.50,
        size=40,
        buy_fee=fees,
        sell_fee=Decimal("0"),
    )
    assert edge > 0
