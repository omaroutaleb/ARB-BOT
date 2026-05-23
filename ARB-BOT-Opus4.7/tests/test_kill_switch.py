"""Kill switch tests — must cancel all orders on both venues within 1 second.

STRATEGY_SYNTHESIS.md Non-negotiable §4.9 and §1.14.
"""

import asyncio
import time

import pytest

from src.risk.kill_switch import cancel_all, cancel_limitless_slugs


pytestmark = pytest.mark.asyncio


class FakePoly:
    def __init__(self, *, returns=None, raises=None, delay_sec=0.0):
        self.returns = returns
        self.raises = raises
        self.delay_sec = delay_sec
        self.cancel_all_calls = 0

    async def cancel_all(self):
        self.cancel_all_calls += 1
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        if self.raises:
            raise self.raises
        return self.returns


class FakeLim:
    def __init__(self, *, batch_returns=None, batch_raises=None, slug_returns=None, slug_raises=None, delay_sec=0.0):
        self.batch_returns = batch_returns
        self.batch_raises = batch_raises
        self.slug_returns = slug_returns
        self.slug_raises = slug_raises
        self.delay_sec = delay_sec
        self.cancel_batch_calls = 0
        self.cancel_market_calls: list[str] = []

    async def cancel_batch(self, order_ids):
        self.cancel_batch_calls += 1
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        if self.batch_raises:
            raise self.batch_raises
        return self.batch_returns

    async def cancel_market(self, slug):
        self.cancel_market_calls.append(slug)
        if self.slug_raises:
            raise self.slug_raises
        return self.slug_returns


class TestCancelAll:
    async def test_calls_both_platforms(self):
        p = FakePoly(returns={"canceled": 3})
        l = FakeLim(batch_returns={"cancelled": 2})
        out = await cancel_all(p, l, reason="test")
        assert p.cancel_all_calls == 1
        assert l.cancel_batch_calls == 1
        assert out["polymarket"] == 3
        assert out["limitless"] == 2

    async def test_handles_none_client(self):
        out = await cancel_all(None, None, reason="test")
        assert out == {"polymarket": 0, "limitless": 0}

    async def test_polymarket_exception_doesnt_block_limitless(self):
        p = FakePoly(raises=RuntimeError("boom"))
        l = FakeLim(batch_returns={"cancelled": 5})
        out = await cancel_all(p, l, reason="test")
        assert out["polymarket"] == -1
        assert out["limitless"] == 5

    async def test_completes_under_one_second(self):
        # The kill switch contract is "within 1 second" — verify with small delays.
        p = FakePoly(returns={"canceled": 1}, delay_sec=0.05)
        l = FakeLim(batch_returns={"cancelled": 1}, delay_sec=0.05)
        start = time.monotonic()
        await cancel_all(p, l, reason="test", timeout_sec=1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0

    async def test_unknown_count_returns_negative_one(self):
        p = FakePoly(returns={})           # no `canceled` key
        l = FakeLim(batch_returns={})
        out = await cancel_all(p, l, reason="test")
        assert out["polymarket"] == -1
        assert out["limitless"] == -1

    async def test_timeout_doesnt_raise(self):
        # If a venue hangs, the kill switch must return without exception.
        p = FakePoly(returns={"canceled": 1}, delay_sec=5.0)
        l = FakeLim(batch_returns={"cancelled": 1})
        await cancel_all(p, l, reason="hang_test", timeout_sec=0.2)


class TestCancelLimitlessSlugs:
    async def test_empty_slug_list_no_calls(self):
        l = FakeLim(slug_returns={"cancelled": 0})
        n = await cancel_limitless_slugs(l, [])
        assert n == 0
        assert l.cancel_market_calls == []

    async def test_none_client_returns_zero(self):
        n = await cancel_limitless_slugs(None, ["a", "b"])
        assert n == 0

    async def test_sums_per_slug_counts(self):
        l = FakeLim(slug_returns={"cancelled": 2})
        n = await cancel_limitless_slugs(l, ["s1", "s2", "s3"])
        assert n == 6
        assert sorted(l.cancel_market_calls) == ["s1", "s2", "s3"]

    async def test_one_slug_error_doesnt_break_others(self):
        # We exercise the failure path by patching cancel_market to raise on one slug.
        calls: list[str] = []
        l = FakeLim(slug_returns={"cancelled": 1})
        original = l.cancel_market

        async def patched(slug):
            calls.append(slug)
            if slug == "bad":
                raise RuntimeError("boom")
            return {"cancelled": 1}

        l.cancel_market = patched  # type: ignore[assignment]
        n = await cancel_limitless_slugs(l, ["good", "bad", "good2"])
        assert n == 2
        assert sorted(calls) == ["bad", "good", "good2"]
