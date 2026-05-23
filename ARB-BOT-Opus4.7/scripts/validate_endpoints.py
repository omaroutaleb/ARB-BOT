"""Pre-deploy endpoint validation. Run from CI / before `docker compose up`.

Fetches each platform's llms.txt index and checks that every hard-coded URL
in src/config.py is still present in the doc listing. Fails fast if any
endpoint has been renamed/removed.

Per STRATEGY_SYNTHESIS.md Non-negotiable §4.2.

Usage:
    python -m scripts.validate_endpoints
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlparse

import aiohttp

from src.config import get_settings


# URLs that MUST be present (in some form) in the llms.txt indices.
POLYMARKET_PATHS = [
    "/markets",
    "/events",
    "/book",
    "/midpoint",
    "/price",
    "/spread",
    "/prices-history",
    "/tick-size",
    "/order",
    "/orders",
    "/cancel-all",
    "/heartbeat",
    "/auth/api-key",
]

LIMITLESS_PATHS = [
    "/markets/active",
    "/markets/active/slugs",
    "/markets",
    "/orders",
    "/orders/cancel-batch",
    "/portfolio/positions",
    "/portfolio/trades",
    "/portfolio/redeem",
    "/portfolio/withdraw",
    "/portfolio/allowance",
    "/portfolio/profile",
]


async def _fetch(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{url} returned {resp.status}")
        return await resp.text()


async def check_one(session: aiohttp.ClientSession, name: str, llms_url: str, required: list[str]) -> list[str]:
    """Return list of missing paths."""
    try:
        text = await _fetch(session, llms_url)
    except Exception as exc:
        print(f"[!] {name}: could not fetch {llms_url}: {exc}")
        return required[:]      # treat as all missing — fail-closed
    missing: list[str] = []
    for path in required:
        if path not in text:
            missing.append(path)
    return missing


async def run() -> int:
    settings = get_settings()
    async with aiohttp.ClientSession() as sess:
        poly_missing, lim_missing = await asyncio.gather(
            check_one(sess, "polymarket", settings.POLY_DOCS_LLMS, POLYMARKET_PATHS),
            check_one(sess, "limitless", settings.LIMITLESS_DOCS_LLMS, LIMITLESS_PATHS),
        )

    failed = False
    if poly_missing:
        print(f"[FAIL] polymarket missing paths in {settings.POLY_DOCS_LLMS}:")
        for p in poly_missing:
            print(f"        {p}")
        failed = True
    else:
        print(f"[ok] polymarket: all {len(POLYMARKET_PATHS)} paths found in llms.txt")

    if lim_missing:
        print(f"[FAIL] limitless missing paths in {settings.LIMITLESS_DOCS_LLMS}:")
        for p in lim_missing:
            print(f"        {p}")
        failed = True
    else:
        print(f"[ok] limitless: all {len(LIMITLESS_PATHS)} paths found in llms.txt")

    if failed:
        print("\nEndpoint drift detected. Investigate before deploying.")
        return 1
    print("\nAll endpoints validated.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
