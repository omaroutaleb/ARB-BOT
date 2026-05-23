from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal


Venue = Literal["polymarket", "limitless"]
LiquidityRole = Literal["maker", "taker"]
Side = Literal["BUY", "SELL"]


class FeeDataMissing(ValueError):
    """Raised when live market/profile fee data is unavailable."""


@dataclass(frozen=True)
class FeeSchedule:
    venue: Venue
    raw: dict[str, Any]
    fee_rate_bps: Decimal | None = None
    fee_rate_decimal: Decimal | None = None
    exponent: Decimal = Decimal("1")
    maker_fee_bps: Decimal = Decimal("0")

    @classmethod
    def from_market(cls, venue: Venue, market: dict[str, Any], profile: dict[str, Any] | None = None) -> "FeeSchedule":
        raw_schedule = _first_dict(
            market.get("feeSchedule"),
            market.get("fee_schedule"),
            market.get("fees"),
            market.get("fd"),
            market,
        )
        profile = profile or {}
        fee_rate_bps = _decimal_or_none(
            raw_schedule.get("feeRateBps")
            or raw_schedule.get("fee_rate_bps")
            or market.get("feeRateBps")
            or profile.get("feeRateBps")
        )
        fee_rate_decimal = _decimal_or_none(
            raw_schedule.get("feeRate")
            or raw_schedule.get("fee_rate")
            or raw_schedule.get("rate")
            or market.get("feeRate")
        )
        exponent = _decimal_or_none(raw_schedule.get("exponent")) or Decimal("1")
        if fee_rate_bps is None and fee_rate_decimal is None and not _has_curve(raw_schedule):
            raise FeeDataMissing(f"{venue} fee schedule missing runtime rate fields")
        return cls(
            venue=venue,
            raw=raw_schedule,
            fee_rate_bps=fee_rate_bps,
            fee_rate_decimal=fee_rate_decimal,
            exponent=exponent,
            maker_fee_bps=_decimal_or_none(raw_schedule.get("makerFeeBps")) or Decimal("0"),
        )


def calculate_fee(
    schedule: FeeSchedule,
    *,
    side: Side,
    role: LiquidityRole,
    price: float,
    size: float,
) -> Decimal:
    """Return fee in collateral units using only runtime schedule data."""

    if role == "maker":
        return _notional(price, size) * _bps_to_decimal(schedule.maker_fee_bps)
    if schedule.venue == "polymarket":
        return _polymarket_taker_fee(schedule, price=price, size=size)
    if schedule.venue == "limitless":
        return _limitless_taker_fee(schedule, side=side, price=price, size=size)
    raise FeeDataMissing(f"unsupported venue={schedule.venue}")


def total_fees(
    legs: list[tuple[FeeSchedule, Side, LiquidityRole, float, float]],
) -> Decimal:
    return sum(
        calculate_fee(schedule, side=side, role=role, price=price, size=size)
        for schedule, side, role, price, size in legs
    )


def effective_edge_after_fees(
    *,
    sell_price: float,
    buy_price: float,
    size: float,
    buy_fee: Decimal,
    sell_fee: Decimal,
    slippage: Decimal = Decimal("0"),
    basis_buffer: Decimal = Decimal("0"),
) -> Decimal:
    gross = (Decimal(str(sell_price)) - Decimal(str(buy_price))) * Decimal(str(size))
    return gross - buy_fee - sell_fee - slippage - basis_buffer


def _polymarket_taker_fee(schedule: FeeSchedule, *, price: float, size: float) -> Decimal:
    rate = _schedule_rate_decimal(schedule)
    p = Decimal(str(price))
    notional = _notional(price, size)
    # Both reports warn that Polymarket has had multiple live fee representations.
    # This implementation uses the runtime `feeRate`/`feeRateBps` plus exponent,
    # never a hard-coded category table.
    curve = (p * (Decimal("1") - p)) ** schedule.exponent
    return notional * rate * curve


def _limitless_taker_fee(
    schedule: FeeSchedule,
    *,
    side: Side,
    price: float,
    size: float,
) -> Decimal:
    p = Decimal(str(price))
    notional = _notional(price, size)
    curve_key = "buy" if side == "BUY" else "sell"
    curve = schedule.raw.get(curve_key) or schedule.raw.get(f"{curve_key}Curve")
    if isinstance(curve, dict):
        bps = _interpolate_curve_bps(curve, p)
        return notional * _bps_to_decimal(bps)
    return notional * _schedule_rate_decimal(schedule)


def _schedule_rate_decimal(schedule: FeeSchedule) -> Decimal:
    if schedule.fee_rate_decimal is not None:
        return schedule.fee_rate_decimal
    if schedule.fee_rate_bps is not None:
        return _bps_to_decimal(schedule.fee_rate_bps)
    raise FeeDataMissing(f"{schedule.venue} fee rate is missing")


def _interpolate_curve_bps(curve: dict[str, Any], p: Decimal) -> Decimal:
    points = curve.get("points") or curve.get("bpsByPrice") or []
    parsed: list[tuple[Decimal, Decimal]] = []
    for point in points:
        if isinstance(point, dict):
            price = _decimal_or_none(point.get("price") or point.get("p"))
            bps = _decimal_or_none(point.get("bps") or point.get("feeRateBps"))
        elif isinstance(point, (list, tuple)) and len(point) == 2:
            price = _decimal_or_none(point[0])
            bps = _decimal_or_none(point[1])
        else:
            continue
        if price is not None and bps is not None:
            parsed.append((price, bps))
    if not parsed:
        flat = _decimal_or_none(curve.get("feeRateBps") or curve.get("bps"))
        if flat is None:
            raise FeeDataMissing("Limitless curve has no usable bps points")
        return flat
    parsed.sort(key=lambda item: item[0])
    if p <= parsed[0][0]:
        return parsed[0][1]
    if p >= parsed[-1][0]:
        return parsed[-1][1]
    for (p0, b0), (p1, b1) in zip(parsed, parsed[1:]):
        if p0 <= p <= p1:
            span = p1 - p0
            weight = (p - p0) / span if span else Decimal("0")
            return b0 + (b1 - b0) * weight
    return parsed[-1][1]


def _notional(price: float, size: float) -> Decimal:
    return Decimal(str(price)) * Decimal(str(size))


def _bps_to_decimal(bps: Decimal) -> Decimal:
    return bps / Decimal("10000")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _has_curve(raw: dict[str, Any]) -> bool:
    return any(key in raw for key in ("buy", "sell", "buyCurve", "sellCurve"))

