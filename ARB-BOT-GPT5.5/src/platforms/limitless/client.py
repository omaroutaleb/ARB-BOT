from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from src.config import Settings
from src.observability.logging import get_logger
from src.observability.metrics import ORDER_FAILURES, ORDERS_SUBMITTED, REQUEST_LATENCY


log = get_logger(__name__)


class LimitlessClient:
    venue = "limitless"

    def __init__(self, settings: Settings, session: aiohttp.ClientSession | None = None):
        self.settings = settings
        self._session = session
        self._owns_session = session is None
        self._semaphore = asyncio.Semaphore(settings.limitless_max_concurrent_requests)
        self._last_request_at = 0.0
        self._delay_lock = asyncio.Lock()
        self.open_order_ids: set[str] = set()

    async def __aenter__(self) -> "LimitlessClient":
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_session and self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def _headers(self, auth: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if auth:
            if self.settings.limitless_api_key is None:
                raise RuntimeError("Limitless API key is required for authenticated request")
            headers["X-API-Key"] = self.settings.limitless_api_key.get_secret_value()
        elif self.settings.limitless_api_key is not None:
            headers["X-API-Key"] = self.settings.limitless_api_key.get_secret_value()
        return headers

    async def _throttle(self) -> None:
        async with self._delay_lock:
            now = time.monotonic()
            min_delay = self.settings.limitless_request_min_delay_ms / 1000
            wait = self._last_request_at + min_delay - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> Any:
        url = f"{self.settings.limitless_api_url}{path}"
        async with self._semaphore:
            await self._throttle()
            start = time.perf_counter()
            try:
                async with self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._headers(auth=auth),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    if response.status >= 400:
                        log.error(
                            "limitless_request_failed",
                            method=method,
                            path=path,
                            status=response.status,
                            body=text[:500],
                        )
                        response.raise_for_status()
                    return json.loads(text) if text else None
            finally:
                REQUEST_LATENCY.labels(self.venue, method.upper(), path).observe(
                    time.perf_counter() - start
                )

    async def active_markets(self) -> Any:
        return await self.request("GET", "/markets/active")

    async def active_slugs(self) -> Any:
        return await self.request("GET", "/markets/active/slugs")

    async def market(self, slug: str) -> dict[str, Any]:
        payload = await self.request("GET", f"/markets/{slug}")
        if not isinstance(payload, dict):
            raise ValueError(f"Limitless market metadata missing for slug={slug}")
        return payload

    async def orderbook(self, slug: str) -> dict[str, Any]:
        payload = await self.request("GET", f"/markets/{slug}/orderbook")
        if not isinstance(payload, dict):
            raise ValueError(f"Limitless orderbook missing for slug={slug}")
        return payload

    async def profile(self) -> dict[str, Any]:
        payload = await self.request("GET", "/portfolio/profile", auth=True)
        if not isinstance(payload, dict):
            raise ValueError("Limitless profile response missing")
        return payload

    async def positions(self) -> Any:
        return await self.request("GET", "/portfolio/positions", auth=True)

    async def trades(self) -> Any:
        return await self.request("GET", "/portfolio/trades", auth=True)

    async def allowance(self) -> Any:
        return await self.request("GET", "/portfolio/allowance", auth=True)

    async def post_order(self, order: dict[str, Any]) -> Any:
        ORDERS_SUBMITTED.labels(self.venue, str(order.get("orderType", "UNKNOWN"))).inc()
        payload = await self.request("POST", "/orders", json_body=order, auth=True)
        order_id = _extract_order_id(payload)
        if order_id:
            self.open_order_ids.add(order_id)
        return payload

    async def cancel_all(self) -> int:
        if not self.open_order_ids:
            return 0
        try:
            payload = await self.request(
                "POST",
                "/orders/cancel-batch",
                json_body={"orderIds": sorted(self.open_order_ids)},
                auth=True,
            )
        except Exception:
            ORDER_FAILURES.labels(self.venue, "cancel_all").inc()
            raise
        count = _extract_cancel_count(payload)
        self.open_order_ids.clear()
        return count

    async def cancel_market_orders(self, slug: str) -> int:
        payload = await self.request("DELETE", f"/orders/all/{slug}", auth=True)
        return _extract_cancel_count(payload)


def _extract_cancel_count(payload: Any) -> int:
    if isinstance(payload, dict):
        for key in ("cancelled", "canceled", "count", "orders"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, list):
                return len(value)
    if isinstance(payload, list):
        return len(payload)
    return 0


def _extract_order_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("orderId", "order_id", "id"):
            value = payload.get(key)
            if value:
                return str(value)
        order = payload.get("order")
        if isinstance(order, dict):
            return _extract_order_id(order)
    return None
