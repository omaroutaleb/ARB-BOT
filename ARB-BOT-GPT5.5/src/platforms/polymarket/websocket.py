from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from src.config import Settings
from src.observability.logging import get_logger


log = get_logger(__name__)
FrameHandler = Callable[[dict[str, Any]], Awaitable[None]]


class PolymarketWebSocket:
    def __init__(self, settings: Settings, on_frame: FrameHandler):
        self.settings = settings
        self.on_frame = on_frame
        self.market_asset_ids: set[str] = set()
        self.user_markets: set[str] = set()

    def set_market_subscriptions(self, asset_ids: set[str]) -> None:
        self.market_asset_ids = set(asset_ids)

    def set_user_subscriptions(self, market_ids: set[str]) -> None:
        self.user_markets = set(market_ids)

    async def run_market(self) -> None:
        await self._run(
            self.settings.polymarket_market_ws_url,
            self._market_subscribe_payload,
            "polymarket_market_ws",
        )

    async def run_user(self) -> None:
        await self._run(
            self.settings.polymarket_user_ws_url,
            self._user_subscribe_payload,
            "polymarket_user_ws",
        )

    async def _run(self, url: str, payload_factory: Callable[[], dict[str, Any]], name: str) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=10, ping_timeout=10) as ws:
                    await ws.send(json.dumps(payload_factory()))
                    log.info("websocket_connected", name=name, subscriptions=self._subscription_count(name))
                    backoff = 1.0
                    async for message in ws:
                        payload = json.loads(message)
                        await self.on_frame(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("websocket_disconnected", name=name, retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _market_subscribe_payload(self) -> dict[str, Any]:
        return {"type": "MARKET", "assets_ids": sorted(self.market_asset_ids)}

    def _user_subscribe_payload(self) -> dict[str, Any]:
        if not self.settings.polymarket_auth_ready:
            raise RuntimeError("Polymarket user websocket requires L2 credentials")
        assert self.settings.polymarket_api_key is not None
        assert self.settings.polymarket_api_secret is not None
        assert self.settings.polymarket_api_passphrase is not None
        return {
            "type": "USER",
            "markets": sorted(self.user_markets),
            "auth": {
                "apiKey": self.settings.polymarket_api_key.get_secret_value(),
                "secret": self.settings.polymarket_api_secret.get_secret_value(),
                "passphrase": self.settings.polymarket_api_passphrase.get_secret_value(),
            },
        }

    def _subscription_count(self, name: str) -> int:
        if "market" in name:
            return len(self.market_asset_ids)
        return len(self.user_markets)

