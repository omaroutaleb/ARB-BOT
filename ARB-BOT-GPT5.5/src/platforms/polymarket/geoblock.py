from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(frozen=True)
class GeoblockResult:
    blocked: bool
    reason: str | None
    raw: dict[str, Any]


def parse_geoblock_payload(payload: dict[str, Any]) -> GeoblockResult:
    blocked = bool(
        payload.get("blocked")
        or payload.get("geoBlocked")
        or payload.get("isBlocked")
        or payload.get("restricted")
    )
    reason = (
        payload.get("reason")
        or payload.get("message")
        or payload.get("country")
        or payload.get("status")
    )
    return GeoblockResult(blocked=blocked, reason=str(reason) if reason else None, raw=payload)


async def check_geoblock(url: str, timeout_seconds: float = 5.0) -> GeoblockResult:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            payload = await response.json()
    return parse_geoblock_payload(payload)

