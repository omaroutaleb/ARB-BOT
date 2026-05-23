"""Hard risk limits — pre-trade gates that reject orders before submission.

STRATEGY_SYNTHESIS.md §1.9 — all defaults from Settings; overridable via .env.
Every method returns a `RiskDecision`. The strategy must consult this before
sending an order. Tests in tests/test_risk_limits.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import get_settings
from src.observability.logging import get_logger
from src.state.positions import Arb, TradeStore

log = get_logger(__name__)


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    @classmethod
    def ok(cls) -> "RiskDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str) -> "RiskDecision":
        return cls(allowed=False, reason=reason)


class RiskLimits:
    """Pre-trade risk gate. All thresholds come from Settings."""

    def __init__(self, store: TradeStore) -> None:
        self.s = get_settings()
        self.store = store

    # ---------- per-arb sizing ----------

    def check_position_size(self, notional_usd: float) -> RiskDecision:
        if notional_usd <= 0:
            return RiskDecision.deny("notional must be > 0")
        if notional_usd > self.s.MAX_POSITION_USD:
            return RiskDecision.deny(
                f"notional {notional_usd:.2f} > MAX_POSITION_USD {self.s.MAX_POSITION_USD:.2f}"
            )
        return RiskDecision.ok()

    # ---------- concurrency ----------

    async def check_concurrent_arbs(self) -> RiskDecision:
        open_arbs = await self.store.open_arbs()
        if len(open_arbs) >= self.s.MAX_CONCURRENT_ARBS:
            return RiskDecision.deny(
                f"already {len(open_arbs)} open arbs (max {self.s.MAX_CONCURRENT_ARBS})"
            )
        return RiskDecision.ok()

    # ---------- platform exposure ----------

    async def check_platform_exposure(
        self, platform: str, additional_notional_usd: float
    ) -> RiskDecision:
        current = await self._platform_exposure(platform)
        proposed = current + additional_notional_usd
        if proposed > self.s.MAX_PLATFORM_EXPOSURE_USD:
            return RiskDecision.deny(
                f"{platform} exposure would be ${proposed:.2f} "
                f"(current ${current:.2f}, max ${self.s.MAX_PLATFORM_EXPOSURE_USD:.2f})"
            )
        return RiskDecision.ok()

    async def _platform_exposure(self, platform: str) -> float:
        exposure = 0.0
        for arb in await self.store.open_arbs():
            for leg in arb.legs:
                if leg.platform != platform:
                    continue
                if not leg.is_open():
                    continue
                # Use intended-size × avg_price (or 0.5 fallback) as exposure estimate.
                price = leg.avg_price if leg.avg_price > 0 else 0.5
                exposure += leg.intended_size * price
        return exposure

    # ---------- reserves ----------

    async def check_reserve_after_trade(
        self, platform_balance_usd: float, deployed_notional_usd: float
    ) -> RiskDecision:
        remaining = platform_balance_usd - deployed_notional_usd
        if remaining < self.s.MIN_PLATFORM_RESERVE_USD:
            return RiskDecision.deny(
                f"reserve after trade ${remaining:.2f} < min ${self.s.MIN_PLATFORM_RESERVE_USD:.2f}"
            )
        return RiskDecision.ok()

    # ---------- edge minimums ----------

    def check_edge(self, duration_class: str, net_edge_pct: float) -> RiskDecision:
        threshold = self._edge_threshold_pct(duration_class)
        if threshold is None:
            return RiskDecision.deny(f"unsupported duration class {duration_class!r}")
        if net_edge_pct < threshold:
            return RiskDecision.deny(
                f"net edge {net_edge_pct:.2f}% < threshold {threshold:.2f}% for {duration_class}"
            )
        return RiskDecision.ok()

    def _edge_threshold_pct(self, duration_class: str) -> float | None:
        return {
            "daily": self.s.MIN_EDGE_DAILY_PCT,
            "weekly": self.s.MIN_EDGE_DAILY_PCT,  # treat weekly = daily threshold
            "1h": self.s.MIN_EDGE_1H_PCT,
            "30m": self.s.MIN_EDGE_30M_PCT,
        }.get(duration_class)

    # ---------- stop-losses ----------

    async def check_daily_loss_stop(self) -> RiskDecision:
        b = await self.store.bankroll()
        if b.daily_pnl_usd <= -self.s.DAILY_LOSS_STOP_USD:
            return RiskDecision.deny(
                f"daily loss stop hit (daily PnL ${b.daily_pnl_usd:.2f} ≤ "
                f"-${self.s.DAILY_LOSS_STOP_USD:.2f})"
            )
        return RiskDecision.ok()

    async def check_total_drawdown_stop(self) -> RiskDecision:
        b = await self.store.bankroll()
        drawdown = b.peak_equity_usd - b.equity_usd
        if drawdown >= self.s.TOTAL_DRAWDOWN_STOP_USD:
            return RiskDecision.deny(
                f"drawdown stop hit (peak ${b.peak_equity_usd:.2f} - "
                f"equity ${b.equity_usd:.2f} = ${drawdown:.2f} ≥ "
                f"${self.s.TOTAL_DRAWDOWN_STOP_USD:.2f})"
            )
        return RiskDecision.ok()

    # ---------- composite pre-trade gate ----------

    async def gate(
        self,
        *,
        platform: str,
        notional_usd: float,
        duration_class: str | None = None,
        net_edge_pct: float | None = None,
        platform_balance_usd: float | None = None,
    ) -> RiskDecision:
        """Run every applicable check. First failure returns. Allows trade if all pass."""
        checks: list[RiskDecision] = []
        checks.append(self.check_position_size(notional_usd))
        if checks[-1].allowed is False:
            return checks[-1]

        checks.append(await self.check_concurrent_arbs())
        if checks[-1].allowed is False:
            return checks[-1]

        checks.append(await self.check_platform_exposure(platform, notional_usd))
        if checks[-1].allowed is False:
            return checks[-1]

        if platform_balance_usd is not None:
            checks.append(
                await self.check_reserve_after_trade(platform_balance_usd, notional_usd)
            )
            if checks[-1].allowed is False:
                return checks[-1]

        if duration_class is not None and net_edge_pct is not None:
            checks.append(self.check_edge(duration_class, net_edge_pct))
            if checks[-1].allowed is False:
                return checks[-1]

        checks.append(await self.check_daily_loss_stop())
        if checks[-1].allowed is False:
            return checks[-1]
        checks.append(await self.check_total_drawdown_stop())
        if checks[-1].allowed is False:
            return checks[-1]

        return RiskDecision.ok()
