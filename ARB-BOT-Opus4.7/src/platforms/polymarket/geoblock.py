"""Polymarket geoblock startup check. Refuses to start if user IP is blocked.

STRATEGY_SYNTHESIS.md §1.15.
"""

from __future__ import annotations

import aiohttp

from src.config import get_settings
from src.observability.logging import get_logger

log = get_logger(__name__)


class GeoblockedError(RuntimeError):
    """Raised at startup when the user's IP is in a blocked jurisdiction."""


async def check_geoblock(timeout_sec: float = 10.0) -> dict:
    """Calls GET https://polymarket.com/api/geoblock. Returns the parsed body
    on success. Raises GeoblockedError if `blocked=true` is set, or any
    other non-2xx response from the endpoint (fail-closed)."""
    settings = get_settings()
    url = settings.POLY_GEOBLOCK_URL
    timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                raise GeoblockedError(
                    f"geoblock endpoint returned {resp.status}; refusing to start"
                )
            body = await resp.json(content_type=None)

    blocked = bool(body.get("blocked", False))
    country = body.get("country") or body.get("countryCode") or "?"
    log.info("polymarket.geoblock.checked", blocked=blocked, country=country, body=body)

    if blocked:
        raise GeoblockedError(
            f"Polymarket geoblocked from country={country}; bot refuses to start"
        )

    return body
