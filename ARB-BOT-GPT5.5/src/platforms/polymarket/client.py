from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

from src.config import Settings
from src.observability.logging import get_logger
from src.observability.metrics import ORDER_FAILURES, ORDERS_SUBMITTED, REQUEST_LATENCY
from src.platforms.polymarket.geoblock import GeoblockResult, check_geoblock


log = get_logger(__name__)


@dataclass(frozen=True)
class PolymarketL2Credentials:
    address: str
    api_key: str
    api_secret: str
    passphrase: str


class PolymarketClient:
    venue = "polymarket"

    def __init__(self, settings: Settings, session: aiohttp.ClientSession | None = None):
        self.settings = settings
        self._session = session
        self._owns_session = session is None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "PolymarketClient":
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop_heartbeat()
        if self._owns_session and self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    @property
    def credentials(self) -> PolymarketL2Credentials | None:
        if not self.settings.polymarket_auth_ready:
            return None
        assert self.settings.polymarket_api_key is not None
        assert self.settings.polymarket_api_secret is not None
        assert self.settings.polymarket_api_passphrase is not None
        assert self.settings.polymarket_address is not None
        return PolymarketL2Credentials(
            address=self.settings.polymarket_address,
            api_key=self.settings.polymarket_api_key.get_secret_value(),
            api_secret=self.settings.polymarket_api_secret.get_secret_value(),
            passphrase=self.settings.polymarket_api_passphrase.get_secret_value(),
        )

    async def geoblock_check(self) -> GeoblockResult:
        return await check_geoblock(self.settings.polymarket_geoblock_url)

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        credentials = self.credentials
        if credentials is None:
            raise RuntimeError("Polymarket L2 credentials are required for authenticated request")
        timestamp = str(int(time.time()))
        message = f"{timestamp}{method.upper()}{path}{body}"
        signature = hmac.new(
            credentials.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "POLY_ADDRESS": credentials.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": credentials.api_key,
            "POLY_PASSPHRASE": credentials.passphrase,
        }

    async def request(
        self,
        method: str,
        base_url: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{base_url}{path}{query}"
        body = json.dumps(json_body, separators=(",", ":"), sort_keys=True) if json_body else ""
        headers = {"Content-Type": "application/json"}
        if auth:
            headers.update(self._auth_headers(method, f"{path}{query}", body))
        start = time.perf_counter()
        try:
            async with self.session.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    log.error(
                        "polymarket_request_failed",
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

    async def get_gamma_markets(self, **params: Any) -> Any:
        return await self.request("GET", self.settings.polymarket_gamma_url, "/markets", params=params)

    async def get_book(self, token_id: str) -> Any:
        return await self.request(
            "GET",
            self.settings.polymarket_clob_url,
            "/book",
            params={"token_id": token_id},
        )

    async def get_tick_size(self, token_id: str) -> float:
        payload = await self.request(
            "GET",
            self.settings.polymarket_clob_url,
            f"/tick-size/{token_id}",
        )
        if isinstance(payload, dict):
            value = payload.get("tick_size") or payload.get("tickSize") or payload.get("minimum_tick_size")
        else:
            value = payload
        if value is None:
            raise ValueError(f"Polymarket tick size missing for token_id={token_id}")
        return float(value)

    async def get_market_fee_data(self, condition_id: str) -> dict[str, Any]:
        raise NotImplementedError(
            "Polymarket fee metadata must be pulled through py-clob-client "
            "getClobMarketInfo/clob-market metadata. No undocumented CLOB path "
            f"is hard-coded for condition_id={condition_id}."
        )

    async def post_order(self, order: dict[str, Any]) -> Any:
        ORDERS_SUBMITTED.labels(self.venue, str(order.get("orderType", "UNKNOWN"))).inc()
        return await self.request(
            "POST",
            self.settings.polymarket_clob_url,
            "/order",
            json_body=order,
            auth=True,
        )

    async def cancel_all(self) -> int:
        try:
            payload = await self.request(
                "DELETE",
                self.settings.polymarket_clob_url,
                "/cancel-all",
                auth=True,
            )
        except Exception:
            ORDER_FAILURES.labels(self.venue, "cancel_all").inc()
            raise
        return _extract_cancel_count(payload)

    async def heartbeat(self) -> Any:
        return await self.request(
            "POST",
            self.settings.polymarket_clob_url,
            "/heartbeat",
            auth=True,
        )

    def start_heartbeat(self) -> None:
        if self.settings.dry_run or self._heartbeat_task is not None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self.heartbeat()
                log.info("polymarket_heartbeat_ok")
            except Exception:
                log.exception("polymarket_heartbeat_failed")
            await asyncio.sleep(self.settings.polymarket_heartbeat_seconds)


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
