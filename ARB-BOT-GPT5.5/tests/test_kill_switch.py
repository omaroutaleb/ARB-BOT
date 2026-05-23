import asyncio

import pytest

from src.risk.kill_switch import KillSwitch


class FakeClient:
    def __init__(self, count: int):
        self.count = count

    async def cancel_all(self) -> int:
        await asyncio.sleep(0.01)
        return self.count


class FailingClient:
    async def cancel_all(self) -> int:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_kill_switch_cancels_both_venues() -> None:
    result = await KillSwitch(
        polymarket=FakeClient(2),
        limitless=FakeClient(3),
        timeout_seconds=1,
    ).cancel_all()
    assert result.completed is True
    assert result.polymarket_cancelled == 2
    assert result.limitless_cancelled == 3


@pytest.mark.asyncio
async def test_kill_switch_reports_failure() -> None:
    result = await KillSwitch(
        polymarket=FailingClient(),
        limitless=FakeClient(3),
        timeout_seconds=1,
    ).cancel_all()
    assert result.completed is False
