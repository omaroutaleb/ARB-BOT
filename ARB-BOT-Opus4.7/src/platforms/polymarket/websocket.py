"""Polymarket WebSocket client — /ws/market and /ws/user with auto-reconnect.

STRATEGY_SYNTHESIS.md §1.4 and Non-negotiable §4.5: re-subscribe on every reconnect.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Coroutine

import websockets
from websockets.exceptions import ConnectionClosed

from src.config import get_settings
from src.observability.logging import get_logger
from src.observability.metrics import ws_reconnects
from src.platforms.polymarket.client import ApiCreds

log = get_logger(__name__)

MessageHandler = Callable[[dict], Coroutine[Any, Any, None]]


class PolymarketWebSocket:
    """Single connection to either /ws/market or /ws/user, with reconnect.

    Subscribed asset/condition lists are tracked so reconnect can re-subscribe
    the full union (mandatory per research §1.4).
    """

    def __init__(
        self,
        url: str,
        *,
        is_user_channel: bool,
        creds: ApiCreds | None = None,
    ) -> None:
        self.settings = get_settings()
        self.url = url
        self.is_user_channel = is_user_channel
        self.creds = creds

        # Membership state — survives reconnect.
        self._asset_ids: set[str] = set()        # market channel
        self._condition_ids: set[str] = set()    # user channel

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._stop = asyncio.Event()
        self._on_message: MessageHandler | None = None

    def add_assets(self, asset_ids: list[str]) -> None:
        self._asset_ids.update(asset_ids)

    def add_conditions(self, condition_ids: list[str]) -> None:
        self._condition_ids.update(condition_ids)

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def run(self, on_message: MessageHandler) -> None:
        self._on_message = on_message
        backoff = 1.0
        channel_label = "user" if self.is_user_channel else "market"
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=10,
                    ping_timeout=15,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    await self._send_subscription(ws)
                    backoff = 1.0
                    async for raw in self._iter(ws):
                        await self._dispatch(raw)
            except (ConnectionClosed, OSError) as exc:
                if self._stop.is_set():
                    return
                ws_reconnects.labels(platform="polymarket", channel=channel_label).inc()
                log.warning(
                    "polymarket.ws.reconnect",
                    channel=channel_label,
                    error=str(exc),
                    backoff_sec=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
            except Exception as exc:
                log.exception("polymarket.ws.unexpected", channel=channel_label, error=str(exc))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
            finally:
                self._ws = None

    async def _iter(self, ws: websockets.WebSocketClientProtocol) -> AsyncIterator[str]:
        async for raw in ws:
            yield raw  # type: ignore[misc]

    async def _send_subscription(self, ws: websockets.WebSocketClientProtocol) -> None:
        if self.is_user_channel:
            if not self.creds:
                raise RuntimeError("user channel requires api creds")
            payload = {
                "auth": {
                    "apiKey": self.creds.api_key,
                    "secret": self.creds.secret,
                    "passphrase": self.creds.passphrase,
                },
                "type": "USER",
                "markets": sorted(self._condition_ids),
            }
        else:
            payload = {
                "type": "MARKET",
                "assets_ids": sorted(self._asset_ids),
            }
        await ws.send(json.dumps(payload))
        log.info(
            "polymarket.ws.subscribed",
            channel="user" if self.is_user_channel else "market",
            count=len(self._condition_ids if self.is_user_channel else self._asset_ids),
        )

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.debug("polymarket.ws.non_json", raw=str(raw)[:200])
            return
        if self._on_message is not None:
            try:
                await self._on_message(data)
            except Exception as exc:
                log.exception("polymarket.ws.handler_error", error=str(exc))


async def open_market_channel(
    asset_ids: list[str], on_message: MessageHandler
) -> tuple[PolymarketWebSocket, asyncio.Task]:
    settings = get_settings()
    ws = PolymarketWebSocket(settings.POLY_WS_MARKET, is_user_channel=False)
    ws.add_assets(asset_ids)
    task = asyncio.create_task(ws.run(on_message), name="poly.ws.market")
    return ws, task


async def open_user_channel(
    creds: ApiCreds,
    condition_ids: list[str],
    on_message: MessageHandler,
) -> tuple[PolymarketWebSocket, asyncio.Task]:
    settings = get_settings()
    ws = PolymarketWebSocket(settings.POLY_WS_USER, is_user_channel=True, creds=creds)
    ws.add_conditions(condition_ids)
    task = asyncio.create_task(ws.run(on_message), name="poly.ws.user")
    return ws, task
