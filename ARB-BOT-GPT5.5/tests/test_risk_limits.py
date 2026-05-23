from src.config import Settings
from src.risk.limits import RiskEngine, RiskLimits, RiskState, RiskStatus


def test_default_limits_match_research() -> None:
    limits = RiskLimits()
    assert limits.max_position_usd == 40
    assert limits.max_concurrent_arbs == 3
    assert limits.max_single_platform_exposure_usd == 300
    assert limits.daily_loss_stop_usd == -50
    assert limits.total_drawdown_stop_usd == -150


def test_approves_small_trade() -> None:
    engine = RiskEngine(RiskLimits())
    decision = engine.check_trade(venue="limitless", notional_usd=20, state=RiskState())
    assert decision.status == RiskStatus.APPROVED
    assert decision.approved is True


def test_rejects_position_too_large() -> None:
    engine = RiskEngine(RiskLimits())
    decision = engine.check_trade(venue="limitless", notional_usd=41, state=RiskState())
    assert decision.status == RiskStatus.REJECTED


def test_halts_on_daily_loss() -> None:
    engine = RiskEngine(RiskLimits())
    decision = engine.check_trade(venue="limitless", notional_usd=20, state=RiskState(daily_pnl_usd=-50))
    assert decision.status == RiskStatus.HALTED


def test_halts_on_total_drawdown() -> None:
    engine = RiskEngine(RiskLimits())
    decision = engine.check_trade(venue="limitless", notional_usd=20, state=RiskState(total_pnl_usd=-150))
    assert decision.status == RiskStatus.HALTED


def test_rejects_when_orphan_blocks_new_arbs() -> None:
    engine = RiskEngine(RiskLimits())
    decision = engine.check_trade(
        venue="limitless",
        notional_usd=20,
        state=RiskState(directional_unhedged=True),
    )
    assert decision.status == RiskStatus.REJECTED


def test_rejects_max_concurrent() -> None:
    engine = RiskEngine(RiskLimits())
    decision = engine.check_trade(venue="limitless", notional_usd=20, state=RiskState(open_arbs=3))
    assert decision.status == RiskStatus.REJECTED


def test_rejects_platform_exposure_limit() -> None:
    engine = RiskEngine(RiskLimits())
    state = RiskState(platform_exposure_usd={"limitless": 290, "polymarket": 0})
    decision = engine.check_trade(venue="limitless", notional_usd=20, state=state)
    assert decision.status == RiskStatus.REJECTED


def test_rejects_reserve_breach() -> None:
    engine = RiskEngine(RiskLimits(max_single_platform_exposure_usd=1000))
    state = RiskState(platform_exposure_usd={"limitless": 440, "polymarket": 0})
    decision = engine.check_trade(venue="limitless", notional_usd=20, state=state)
    assert decision.status == RiskStatus.REJECTED


def test_limits_from_settings_and_timeframe_thresholds() -> None:
    limits = RiskLimits.from_settings(Settings(dry_run=True, max_position_usd=33))
    assert limits.max_position_usd == 33
    engine = RiskEngine(limits)
    assert engine.min_edge_bps_for_timeframe("daily") == 200
    assert engine.min_edge_bps_for_timeframe("1h") == 250
    assert engine.min_edge_bps_for_timeframe("30m") == 300
    assert engine.min_edge_bps_for_timeframe("5m") == 300
