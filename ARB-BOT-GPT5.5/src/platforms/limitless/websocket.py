from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import socketio

from src.config import Settings
from src.observability.logging import get_logger


log = get_logger(__name__)
FrameHandler = Callable[[dict[str, Any]], Awaitable[None]]


class LimitlessWebSocket:
    """Socket.IO `/markets` client.

    Limitless replacement semantics are important: every subscribe call sends the
    full union of current slugs/addresses, never an incremental delta.
    """

    def __init__(self, settings: Settings, on_frame: FrameHandler):
        self.settings = settings
        self.on_frame = on_frame
        self.market_slugs: set[str] = set()
        self.market_addresses: set[str] = set()
        self.sio = socketio.AsyncClient(reconnection=True, logger=False, engineio_logger=False)
        self._register_handlers()

    def set_subscriptions(self, *, slugs: set[str], addresses: set[str] | None = None) -> None:
        self.market_slugs = set(slugs)
        self.market_addresses = set(addresses or set())

    def _headers(self) -> dict[str, str]:
        if self.settings.limitless_api_key is None:
            return {}
        return {"X-API-Key": self.settings.limitless_api_key.get_secret_value()}

    def _register_handlers(self) -> None:
        @self.sio.event(namespace="/markets")
        async def connect() -> None:  # type: ignore[no-untyped-def]
            await self._resubscribe()
            log.info("websocket_connected", name="limitless_markets_ws", subscriptions=len(self.market_slugs))

        @self.sio.event(namespace="/markets")
        async def disconnect() -> None:  # type: ignore[no-untyped-def]
            log.warning("websocket_disconnected", name="limitless_markets_ws")

        for event_name in (
            "orderbookUpdate",
            "orderEvent",
            "positions",
            "transaction",
            "marketResolved",
            "marketPrice",
        ):
            self.sio.on(event_name, self._handle_event(event_name), namespace="/markets")

    def _handle_event(self, event_name: str) -> Callable[[Any], Awaitable[None]]:
        async def handler(payload: Any) -> None:
            await self.on_frame({"event": event_name, "payload": payload})

        return handler

    async def run(self) -> None:
        while True:
            try:
                await self.sio.connect(
                    self.settings.limitless_ws_url,
                    namespaces=["/markets"],
                    headers=self._headers(),
                    transports=["websocket"],
                )
                await self.sio.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("websocket_connect_failed", name="limitless_markets_ws")
                await asyncio.sleep(5)

    async def _resubscribe(self) -> None:
        payload = {
            "marketSlugs": sorted(self.market_slugs),
            "marketAddresses": sorted(self.market_addresses),
        }
        await self.sio.emit("subscribe_market_prices", payload, namespace="/markets")
        await self.sio.emit("subscribe_positions", {"marketSlugs": sorted(self.market_slugs)}, namespace="/markets")
        await self.sio.emit("subscribe_order_events", namespace="/markets")

