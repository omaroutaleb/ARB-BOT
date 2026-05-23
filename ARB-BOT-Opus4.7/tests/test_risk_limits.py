"""Risk-limit pre-trade gate tests.

STRATEGY_SYNTHESIS.md §1.9 — every default threshold has a test.
"""

import pytest

from src.risk.limits import RiskLimits


pytestmark = pytest.mark.asyncio


@pytest.fixture
def risk(store):
    return RiskLimits(store)


class TestPositionSize:
    def test_zero_rejected(self, risk):
        assert not risk.check_position_size(0).allowed

    def test_negative_rejected(self, risk):
        assert not risk.check_position_size(-5).allowed

    def test_under_max_allowed(self, risk):
        assert risk.check_position_size(20).allowed

    def test_at_max_allowed(self, risk):
        assert risk.check_position_size(40).allowed

    def test_over_max_rejected(self, risk):
        d = risk.check_position_size(40.01)
        assert not d.allowed
        assert "MAX_POSITION_USD" in d.reason


class TestConcurrentArbs:
    async def test_empty_state_allows(self, risk):
        assert (await risk.check_concurrent_arbs()).allowed

    async def test_at_cap_rejects(self, risk, store):
        from src.state.positions import Arb, Leg
        for _ in range(3):
            arb = Arb(strategy="phase1_yes_no")
            arb.legs = [Leg(arb_id=arb.arb_id, platform="limitless", market_key="x",
                            side="YES", intended_size=10, status="pending")]
            await store.upsert_arb(arb)
        d = await risk.check_concurrent_arbs()
        assert not d.allowed


class TestPlatformExposure:
    async def test_under_cap_allows(self, risk):
        d = await risk.check_platform_exposure("polymarket", 100.0)
        assert d.allowed

    async def test_over_cap_rejects(self, risk):
        d = await risk.check_platform_exposure("polymarket", 1000.0)
        assert not d.allowed
        assert "exposure" in d.reason


class TestReserveCheck:
    async def test_sufficient_reserve_ok(self, risk):
        d = await risk.check_reserve_after_trade(platform_balance_usd=225, deployed_notional_usd=100)
        assert d.allowed

    async def test_insufficient_reserve_rejected(self, risk):
        d = await risk.check_reserve_after_trade(platform_balance_usd=70, deployed_notional_usd=30)
        assert not d.allowed


class TestEdgeThresholds:
    def test_daily_under_threshold_rejects(self, risk):
        d = risk.check_edge("daily", 1.0)
        assert not d.allowed

    def test_daily_at_threshold_passes(self, risk):
        d = risk.check_edge("daily", 2.0)
        assert d.allowed

    def test_hourly_threshold_higher(self, risk):
        # 2.25% should pass daily (2.0%) but fail 1h (2.5%)
        assert risk.check_edge("daily", 2.25).allowed
        assert not risk.check_edge("1h", 2.25).allowed

    def test_30m_threshold_highest(self, risk):
        assert not risk.check_edge("30m", 2.5).allowed
        assert risk.check_edge("30m", 3.0).allowed

    def test_unsupported_duration_rejects(self, risk):
        assert not risk.check_edge("nonexistent", 99).allowed


class TestStopLosses:
    async def test_no_loss_ok(self, risk):
        assert (await risk.check_daily_loss_stop()).allowed
        assert (await risk.check_total_drawdown_stop()).allowed

    async def test_daily_loss_stop_triggers(self, risk, store):
        # Force the bankroll into a deep daily loss.
        async with store._lock:
            store._data["bankroll"]["daily_pnl_usd"] = -50.01
            store._flush_unlocked()
        d = await risk.check_daily_loss_stop()
        assert not d.allowed
        assert "daily loss stop" in d.reason

    async def test_drawdown_stop_triggers(self, risk, store):
        async with store._lock:
            store._data["bankroll"]["peak_equity_usd"] = 500.0
            store._data["bankroll"]["equity_usd"] = 349.0  # 151 drawdown
            store._flush_unlocked()
        d = await risk.check_total_drawdown_stop()
        assert not d.allowed
        assert "drawdown" in d.reason


class TestCompositeGate:
    async def test_clean_pass(self, risk):
        d = await risk.gate(
            platform="limitless",
            notional_usd=20.0,
            duration_class="daily",
            net_edge_pct=2.5,
            platform_balance_usd=225.0,
        )
        assert d.allowed, d.reason

    async def test_oversized_position_blocked(self, risk):
        d = await risk.gate(platform="limitless", notional_usd=100.0)
        assert not d.allowed
        assert "MAX_POSITION_USD" in d.reason

    async def test_insufficient_edge_blocked(self, risk):
        d = await risk.gate(
            platform="limitless", notional_usd=20.0,
            duration_class="daily", net_edge_pct=1.0,
        )
        assert not d.allowed
        assert "edge" in d.reason
