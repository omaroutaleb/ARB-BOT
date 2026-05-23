"""Limitless WebSocket — Socket.IO /markets namespace with auto-reconnect.

STRATEGY_SYNTHESIS.md §1.4, §1.17:
  Use python-socketio asyncio client.
  Each `subscribe_market_prices` REPLACES the previous set — always send full union.
  Re-subscribe on every reconnect.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import socketio

from src.config import get_settings
from src.observability.logging import get_logger
from src.observability.metrics import ws_reconnects

log = get_logger(__name__)


class LimitlessWebSocket:
    """Single Socket.IO connection to /markets with persistent subscription state."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,        # forever
            reconnection_delay=1,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )
        self._namespace = "/markets"

        # Persistent membership state — survives reconnect.
        self._market_slugs: set[str] = set()
        self._market_addresses: set[str] = set()
        self._position_slugs: set[str] = set()
        self._order_events_subscribed = False

        self._handlers: dict[str, Callable[[Any], Awaitable[None]]] = {}
        self._stop = asyncio.Event()
        self._register_internal()

    # ---------- public API ----------

    def on(self, event: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        self._handlers[event] = handler
        self._sio.on(event, handler, namespace=self._namespace)

    def add_market_subscription(self, slugs: list[str] | None = None, addresses: list[str] | None = None) -> None:
        if slugs:
            self._market_slugs.update(slugs)
        if addresses:
            self._market_addresses.update(addresses)

    def add_position_subscription(self, slugs: list[str]) -> None:
        self._position_slugs.update(slugs)

    def subscribe_order_events(self) -> None:
        self._order_events_subscribed = True

    async def stop(self) -> None:
        self._stop.set()
        try:
            await self._sio.disconnect()
        except Exception:
            pass

    async def run(self) -> None:
        if self.settings.LIMITLESS_API_KEY is None:
            raise RuntimeError("LIMITLESS_API_KEY not set")
        api_key = self.settings.LIMITLESS_API_KEY.get_secret_value()
        url = self.settings.LIMITLESS_WS_URL

        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._sio.connect(
                    url,
                    namespaces=[self._namespace],
                    transports=["websocket"],
                    headers={"X-API-Key": api_key},
                    socketio_path="/socket.io",
                )
                await self._resubscribe()
                backoff = 1.0
                await self._sio.wait()
            except Exception as exc:
                if self._stop.is_set():
                    return
                ws_reconnects.labels(platform="limitless", channel="/markets").inc()
                log.warning("limitless.ws.reconnect", error=str(exc), backoff_sec=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    # ---------- internals ----------

    def _register_internal(self) -> None:
        @self._sio.event(namespace=self._namespace)
        async def connect():  # noqa
            log.info("limitless.ws.connected")

        @self._sio.event(namespace=self._namespace)
        async def disconnect():  # noqa
            log.warning("limitless.ws.disconnected")

    async def _resubscribe(self) -> None:
        """Send full union of subscriptions (subscribe_* REPLACES, not merges)."""
        if self._market_slugs or self._market_addresses:
            await self._sio.emit(
                "subscribe_market_prices",
                {
                    "marketSlugs": sorted(self._market_slugs),
                    "marketAddresses": sorted(self._market_addresses),
                },
                namespace=self._namespace,
            )
            log.info(
                "limitless.ws.subscribed",
                kind="market_prices",
                slug_count=len(self._market_slugs),
                addr_count=len(self._market_addresses),
            )
        if self._position_slugs:
            await self._sio.emit(
                "subscribe_positions",
                {"marketSlugs": sorted(self._position_slugs)},
                namespace=self._namespace,
            )
            log.info(
                "limitless.ws.subscribed", kind="positions", count=len(self._position_slugs)
            )
        if self._order_events_subscribed:
            await self._sio.emit("subscribe_order_events", namespace=self._namespace)
            log.info("limitless.ws.subscribed", kind="order_events")
