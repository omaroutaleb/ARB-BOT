"""Polymarket REST client — Gamma (discovery) + CLOB (market data + trading).

Auth model (STRATEGY_SYNTHESIS.md §1.2):
  L1: EIP-712 ClobAuth signature → POST /auth/api-key → {apiKey, secret, passphrase}
  L2: every authenticated request attaches 5 POLY_* headers including HMAC-SHA256 body sig
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from eth_account import Account
from eth_account.messages import encode_typed_data

from src.config import PolymarketSignatureType, get_settings
from src.observability.logging import get_logger
from src.observability.metrics import orders_rejected, heartbeats_sent

log = get_logger(__name__)


@dataclass(slots=True)
class ApiCreds:
    api_key: str
    secret: str
    passphrase: str


class PolymarketError(RuntimeError):
    pass


class PolymarketClient:
    """Thin async client for the Polymarket Gamma + CLOB APIs.

    Heavy lifting (order signing) lives in `orders.py`. This module owns
    HTTP transport, L1→L2 auth bootstrap, and HMAC header construction.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._session: aiohttp.ClientSession | None = None
        self._creds: ApiCreds | None = None

    # ---------- lifecycle ----------

    async def __aenter__(self) -> "PolymarketClient":
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

    # ---------- auth ----------

    async def authenticate(self) -> ApiCreds:
        """L1 EIP-712 signature → POST /auth/api-key → cached creds.

        Polymarket actually offers two derive endpoints:
          - POST /auth/api-key   → creates new creds
          - GET  /auth/derive-api-key → returns existing creds for the address
        We try GET first (idempotent), then fall back to POST.
        """
        if self._creds is not None:
            return self._creds

        self.settings.require_polymarket_creds()

        timestamp = int(time.time())
        signature = self._sign_clob_auth(timestamp, nonce=0)

        headers = {
            "POLY_ADDRESS": self._wallet_address(),
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(timestamp),
            "POLY_NONCE": "0",
        }

        url_get = f"{self.settings.POLY_CLOB_URL}/auth/derive-api-key"
        url_post = f"{self.settings.POLY_CLOB_URL}/auth/api-key"
        sess = await self._sess()

        async with sess.get(url_get, headers=headers) as resp:
            if resp.status == 200:
                body = await resp.json(content_type=None)
            else:
                async with sess.post(url_post, headers=headers) as resp2:
                    if resp2.status not in (200, 201):
                        text = await resp2.text()
                        raise PolymarketError(f"POST /auth/api-key failed: {resp2.status} {text}")
                    body = await resp2.json(content_type=None)

        self._creds = ApiCreds(
            api_key=body["apiKey"],
            secret=body["secret"],
            passphrase=body["passphrase"],
        )
        log.info("polymarket.auth.ok", address=self._wallet_address())
        return self._creds

    def creds(self) -> ApiCreds:
        if self._creds is None:
            raise PolymarketError("not authenticated; call authenticate() first")
        return self._creds

    def _wallet_address(self) -> str:
        addr = self.settings.POLY_WALLET_ADDRESS
        if not addr:
            raise PolymarketError("POLY_WALLET_ADDRESS not set")
        return addr

    def _sign_clob_auth(self, timestamp: int, nonce: int = 0) -> str:
        """EIP-712 ClobAuth typed-data signature (L1)."""
        if self.settings.POLY_PRIVATE_KEY is None:
            raise PolymarketError("POLY_PRIVATE_KEY not set")
        priv = self.settings.POLY_PRIVATE_KEY.get_secret_value()

        domain = {"name": "ClobAuthDomain", "version": "1", "chainId": self.settings.POLY_CHAIN_ID}
        message_types = {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        }
        message = {
            "address": self._wallet_address(),
            "timestamp": str(timestamp),
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        }
        encoded = encode_typed_data(
            domain_data=domain,
            message_types=message_types,
            message_data=message,
        )
        signed = Account.from_key(priv).sign_message(encoded)
        sig_hex = signed.signature.hex()
        return sig_hex if sig_hex.startswith("0x") else "0x" + sig_hex

    # ---------- L2 HMAC headers ----------

    def _l2_headers(self, method: str, path: str, body: Any | None) -> dict[str, str]:
        creds = self.creds()
        timestamp = str(int(time.time()))

        body_str = "" if body is None else json.dumps(body, separators=(",", ":"))
        # Polymarket HMAC scheme: HMAC-SHA256(timestamp + method + path + body)
        message = f"{timestamp}{method.upper()}{path}{body_str}"
        decoded_secret = base64.urlsafe_b64decode(creds.secret)
        sig = hmac.new(decoded_secret, message.encode("utf-8"), hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8")

        return {
            "POLY_ADDRESS": self._wallet_address(),
            "POLY_SIGNATURE": sig_b64,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": creds.api_key,
            "POLY_PASSPHRASE": creds.passphrase,
            "Content-Type": "application/json",
        }

    # ---------- transport ----------

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.start()
        assert self._session is not None
        return self._session

    async def _get_public(self, url: str, params: dict | None = None) -> Any:
        sess = await self._sess()
        async with sess.get(url, params=params) as resp:
            return await self._parse(resp)

    async def _request_auth(self, method: str, path: str, body: Any | None = None) -> Any:
        sess = await self._sess()
        url = f"{self.settings.POLY_CLOB_URL}{path}"
        headers = self._l2_headers(method, path, body)
        async with sess.request(method, url, json=body, headers=headers) as resp:
            return await self._parse(resp, body=body, path=path)

    @staticmethod
    async def _parse(
        resp: aiohttp.ClientResponse,
        *,
        body: Any = None,
        path: str | None = None,
    ) -> Any:
        text = await resp.text()
        if resp.status >= 400:
            if "/order" in (path or ""):
                orders_rejected.labels(platform="polymarket", code=str(resp.status)).inc()
            raise PolymarketError(f"{resp.status} {resp.reason} url={resp.url} body={text[:500]}")
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # ---------- Gamma discovery ----------

    async def gamma_markets(self, **params: Any) -> list[dict]:
        url = f"{self.settings.POLY_GAMMA_URL}/markets"
        return await self._get_public(url, params=params) or []

    async def gamma_market_by_slug(self, slug: str) -> dict | None:
        markets = await self.gamma_markets(slug=slug)
        return markets[0] if markets else None

    async def gamma_events(self, **params: Any) -> list[dict]:
        url = f"{self.settings.POLY_GAMMA_URL}/events"
        return await self._get_public(url, params=params) or []

    async def gamma_markets_keyset(self, cursor: str | None = None, limit: int = 100) -> dict:
        url = f"{self.settings.POLY_GAMMA_URL}/markets/keyset"
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._get_public(url, params=params) or {}

    # ---------- CLOB market data (public) ----------

    async def book(self, token_id: str) -> dict:
        url = f"{self.settings.POLY_CLOB_URL}/book"
        return await self._get_public(url, params={"token_id": token_id}) or {}

    async def midpoint(self, token_id: str) -> float | None:
        url = f"{self.settings.POLY_CLOB_URL}/midpoint"
        data = await self._get_public(url, params={"token_id": token_id}) or {}
        mid = data.get("mid")
        return float(mid) if mid is not None else None

    async def price(self, token_id: str, side: str) -> float | None:
        url = f"{self.settings.POLY_CLOB_URL}/price"
        data = await self._get_public(url, params={"token_id": token_id, "side": side}) or {}
        p = data.get("price")
        return float(p) if p is not None else None

    async def tick_size(self, token_id: str) -> float:
        """Per-market tick. NEVER assume — Polymarket has 0.1, 0.01, 0.001, 0.0001."""
        url = f"{self.settings.POLY_CLOB_URL}/tick-size/{token_id}"
        data = await self._get_public(url) or {}
        ts = data.get("minimum_tick_size") or data.get("tick_size") or 0.01
        return float(ts)

    async def prices_history(self, market: str, interval: str = "1h", **extra: Any) -> list[dict]:
        url = f"{self.settings.POLY_CLOB_URL}/prices-history"
        return (await self._get_public(url, params={"market": market, "interval": interval, **extra})) or []

    # ---------- CLOB trading (authenticated) ----------

    async def submit_order(self, signed_order: dict) -> dict:
        """Submit a single EIP-712-signed order. See orders.py for signing."""
        return await self._request_auth("POST", "/order", body=signed_order) or {}

    async def submit_orders(self, signed_orders: list[dict]) -> dict:
        if len(signed_orders) > 15:
            raise PolymarketError("Polymarket batch order limit is 15")
        return await self._request_auth("POST", "/orders", body=signed_orders) or {}

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request_auth("DELETE", f"/order/{order_id}") or {}

    async def cancel_all(self) -> dict:
        return await self._request_auth("DELETE", "/cancel-all") or {}

    async def cancel_market_orders(self, market: str) -> dict:
        return await self._request_auth("DELETE", "/cancel-market-orders", body={"market": market}) or {}

    async def open_orders(self) -> list[dict]:
        return await self._request_auth("GET", "/orders") or []

    async def trades(self) -> list[dict]:
        return await self._request_auth("GET", "/trades") or []

    # ---------- heartbeat ----------

    async def heartbeat(self) -> None:
        """Polymarket auto-cancels all orders if no heartbeat >~15s. We send every 5."""
        try:
            await self._request_auth("POST", "/heartbeat", body={})
            heartbeats_sent.inc()
        except PolymarketError as exc:
            log.warning("polymarket.heartbeat.failed", error=str(exc))

    async def heartbeat_loop(self, stop_event: asyncio.Event) -> None:
        interval = max(1, int(self.settings.POLY_HEARTBEAT_INTERVAL_SEC))
        while not stop_event.is_set():
            await self.heartbeat()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    # ---------- proxy helpers ----------

    def signature_type(self) -> PolymarketSignatureType:
        return self.settings.POLY_SIGNATURE_TYPE

    def funder_address(self) -> str:
        sig = self.signature_type()
        if sig == PolymarketSignatureType.EOA:
            return self._wallet_address()
        if not self.settings.POLY_FUNDER_ADDRESS:
            raise PolymarketError(f"POLY_FUNDER_ADDRESS required for signature type {sig.name}")
        return self.settings.POLY_FUNDER_ADDRESS
