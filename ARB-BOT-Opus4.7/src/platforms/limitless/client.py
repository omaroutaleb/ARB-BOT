"""Limitless Exchange REST client.

Auth: single `X-API-Key: lmts_…` header. (STRATEGY_SYNTHESIS.md §1.2)
Rate-limit: 2 concurrent + 300ms min spacing (research §1.8 — Limitless does not
publish numeric limits; we self-enforce a conservative default).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from src.config import get_settings
from src.observability.logging import get_logger
from src.observability.metrics import orders_rejected

log = get_logger(__name__)


class LimitlessError(RuntimeError):
    pass


class _RateLimiter:
    """Conservative limiter: max-concurrent semaphore + min-interval gate."""

    def __init__(self, max_concurrent: int, min_interval_ms: int) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._gate = asyncio.Lock()
        self._min_interval = min_interval_ms / 1000.0
        self._last_release = 0.0

    async def __aenter__(self) -> None:
        await self._sem.acquire()
        async with self._gate:
            wait_for = self._last_release + self._min_interval - time.monotonic()
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_release = time.monotonic()

    async def __aexit__(self, *_: Any) -> None:
        self._sem.release()


class LimitlessClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._session: aiohttp.ClientSession | None = None
        self._limiter = _RateLimiter(
            self.settings.LIMITLESS_MAX_CONCURRENT,
            self.settings.LIMITLESS_MIN_INTERVAL_MS,
        )
        self._profile_id: str | None = None
        self._fee_rate_bps: int | None = None

    # ---------- lifecycle ----------

    async def __aenter__(self) -> "LimitlessClient":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                json_serialize=lambda obj: json.dumps(obj, separators=(",", ":")),
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self, auth: bool = False) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if auth:
            if self.settings.LIMITLESS_API_KEY is None:
                raise LimitlessError("LIMITLESS_API_KEY not set")
            h["X-API-Key"] = self.settings.LIMITLESS_API_KEY.get_secret_value()
        return h

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.start()
        assert self._session is not None
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        params: dict | None = None,
        body: Any = None,
    ) -> Any:
        sess = await self._sess()
        url = f"{self.settings.LIMITLESS_REST_URL}{path}"
        async with self._limiter:
            async with sess.request(
                method,
                url,
                params=params,
                json=body,
                headers=self._headers(auth=auth),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    if "/orders" in path and method == "POST":
                        orders_rejected.labels(platform="limitless", code=str(resp.status)).inc()
                    raise LimitlessError(
                        f"{resp.status} {resp.reason} url={resp.url} body={text[:500]}"
                    )
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text

    # ---------- discovery ----------

    async def markets_active(self) -> list[dict]:
        return await self._request("GET", "/markets/active") or []

    async def markets_active_slugs(self) -> list[dict]:
        return await self._request("GET", "/markets/active/slugs") or []

    async def market(self, slug_or_addr: str) -> dict | None:
        return await self._request("GET", f"/markets/{slug_or_addr}")

    async def markets_search(self, query: str) -> list[dict]:
        return await self._request("GET", "/markets/search", params={"query": query}) or []

    async def oracle_candles(self, slug_or_addr: str, interval: str = "1h") -> list[dict]:
        return await self._request(
            "GET", f"/markets/{slug_or_addr}/oracle-candles", params={"interval": interval}
        ) or []

    async def orderbook(self, slug: str) -> dict:
        return await self._request("GET", f"/markets/{slug}/orderbook") or {}

    async def market_events(self, slug: str) -> list[dict]:
        return await self._request("GET", f"/markets/{slug}/events") or []

    async def historical_price(self, slug: str, interval: str = "1h") -> list[dict]:
        return await self._request(
            "GET", f"/markets/{slug}/historical-price", params={"interval": interval}
        ) or []

    # ---------- trading ----------

    async def submit_order(self, payload: dict) -> dict:
        return await self._request("POST", "/orders", auth=True, body=payload) or {}

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", f"/orders/{order_id}", auth=True) or {}

    async def cancel_batch(self, order_ids: list[str]) -> dict:
        return await self._request(
            "POST", "/orders/cancel-batch", auth=True, body={"orderIds": order_ids}
        ) or {}

    async def cancel_market(self, slug: str) -> dict:
        return await self._request("DELETE", f"/orders/all/{slug}", auth=True) or {}

    async def order_status_batch(self, order_ids: list[str]) -> list[dict]:
        return await self._request(
            "POST", "/orders/status/batch", auth=True, body={"orderIds": order_ids}
        ) or []

    # ---------- portfolio ----------

    async def portfolio_profile(self) -> dict:
        prof = await self._request("GET", "/portfolio/profile", auth=True) or {}
        self._profile_id = prof.get("id")
        if "feeRateBps" in prof:
            self._fee_rate_bps = int(prof["feeRateBps"])
        return prof

    async def positions(self) -> list[dict]:
        return await self._request("GET", "/portfolio/positions", auth=True) or []

    async def trades(self) -> list[dict]:
        return await self._request("GET", "/portfolio/trades", auth=True) or []

    async def allowance(self) -> dict:
        return await self._request("GET", "/portfolio/allowance", auth=True) or {}

    async def redeem(self, condition_id: str) -> dict:
        return await self._request(
            "POST", "/portfolio/redeem", auth=True, body={"conditionId": condition_id}
        ) or {}

    async def withdraw(self, amount: str, to: str) -> dict:
        return await self._request(
            "POST", "/portfolio/withdraw", auth=True, body={"amount": amount, "to": to}
        ) or {}

    # ---------- cached fields ----------

    def cached_profile_id(self) -> str | None:
        return self._profile_id

    def cached_fee_rate_bps(self) -> int | None:
        return self._fee_rate_bps
