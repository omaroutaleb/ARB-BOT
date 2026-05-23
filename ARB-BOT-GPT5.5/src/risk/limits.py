from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.config import Settings


class RiskStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    HALTED = "halted"


@dataclass(frozen=True)
class RiskDecision:
    status: RiskStatus
    reason: str

    @property
    def approved(self) -> bool:
        return self.status == RiskStatus.APPROVED


@dataclass(frozen=True)
class RiskLimits:
    bankroll_usd: float = 500.0
    max_position_usd: float = 40.0
    max_concurrent_arbs: int = 3
    max_single_platform_exposure_usd: float = 300.0
    reserve_cash_usd: float = 50.0
    daily_loss_stop_usd: float = -50.0
    total_drawdown_stop_usd: float = -150.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "RiskLimits":
        return cls(
            bankroll_usd=settings.bankroll_usd,
            max_position_usd=settings.max_position_usd,
            max_concurrent_arbs=settings.max_concurrent_arbs,
            max_single_platform_exposure_usd=settings.max_single_platform_exposure_usd,
            reserve_cash_usd=settings.reserve_cash_usd,
            daily_loss_stop_usd=settings.daily_loss_stop_usd,
            total_drawdown_stop_usd=settings.total_drawdown_stop_usd,
        )


@dataclass
class RiskState:
    open_arbs: int = 0
    platform_exposure_usd: dict[str, float] | None = None
    daily_pnl_usd: float = 0.0
    total_pnl_usd: float = 0.0
    directional_unhedged: bool = False

    def __post_init__(self) -> None:
        if self.platform_exposure_usd is None:
            self.platform_exposure_usd = {"polymarket": 0.0, "limitless": 0.0}


class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def check_trade(self, *, venue: str, notional_usd: float, state: RiskState) -> RiskDecision:
        if state.daily_pnl_usd <= self.limits.daily_loss_stop_usd:
            return RiskDecision(RiskStatus.HALTED, "daily loss stop reached")
        if state.total_pnl_usd <= self.limits.total_drawdown_stop_usd:
            return RiskDecision(RiskStatus.HALTED, "total drawdown stop reached")
        if state.directional_unhedged:
            return RiskDecision(RiskStatus.REJECTED, "directional orphan position blocks new arbs")
        if state.open_arbs >= self.limits.max_concurrent_arbs:
            return RiskDecision(RiskStatus.REJECTED, "max concurrent arbs reached")
        if notional_usd > self.limits.max_position_usd:
            return RiskDecision(RiskStatus.REJECTED, "position exceeds max position size")
        current = (state.platform_exposure_usd or {}).get(venue, 0.0)
        if current + notional_usd > self.limits.max_single_platform_exposure_usd:
            return RiskDecision(RiskStatus.REJECTED, "single-platform exposure limit reached")
        if self.limits.bankroll_usd - (current + notional_usd) < self.limits.reserve_cash_usd:
            return RiskDecision(RiskStatus.REJECTED, "reserve cash would be breached")
        return RiskDecision(RiskStatus.APPROVED, "approved")

    def min_edge_bps_for_timeframe(self, timeframe: str) -> int:
        normalized = timeframe.lower()
        if normalized in {"daily", "1d", "day"}:
            return 200
        if normalized in {"1h", "hourly", "hour"}:
            return 250
        if normalized in {"30m", "30min"}:
            return 300
        return 300

