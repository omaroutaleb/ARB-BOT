from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from src.config import LIMITLESS_ENDPOINTS, POLYMARKET_ENDPOINTS, Settings, get_settings
from src.observability.logging import configure_logging, get_logger


log = get_logger(__name__)


@dataclass(frozen=True)
class EndpointCheck:
    name: str
    url: str
    docs_url: str
    found: bool


async def validate_endpoints(settings: Settings) -> dict[str, Any]:
    docs = await _fetch_docs(settings)
    checks: list[EndpointCheck] = []
    for name, url in POLYMARKET_ENDPOINTS.items():
        checks.append(_check(name, url, settings.polymarket_llms_url, docs["polymarket"]))
    for name, url in LIMITLESS_ENDPOINTS.items():
        checks.append(_check(name, url, settings.limitless_llms_url, docs["limitless"]))
    missing = [check.__dict__ for check in checks if not check.found]
    return {
        "ok": not missing,
        "checked": len(checks),
        "missing": missing,
    }


async def _fetch_docs(settings: Settings) -> dict[str, str]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(settings.polymarket_llms_url) as response:
            response.raise_for_status()
            polymarket = await response.text()
        async with session.get(settings.limitless_llms_url) as response:
            response.raise_for_status()
            limitless = await response.text()
    return {"polymarket": polymarket, "limitless": limitless}


def _check(name: str, url: str, docs_url: str, docs_text: str) -> EndpointCheck:
    found = any(variant in docs_text for variant in _variants(url))
    return EndpointCheck(name=name, url=url, docs_url=docs_url, found=found)


def _variants(url: str) -> set[str]:
    parsed = urlparse(url)
    path = parsed.path
    host = parsed.netloc
    placeholder_path = (
        path.replace("{slug}", ":slug")
        .replace("{orderId}", ":orderId")
        .replace("{token_id}", ":token_id")
    )
    loose_path = path.split("/{", 1)[0] if "/{" in path else path
    return {
        url,
        f"{parsed.scheme}://{host}{path}",
        path,
        placeholder_path,
        loose_path,
        path.replace("{slug}", "[slug]")
        .replace("{orderId}", "[orderId]")
        .replace("{token_id}", "[token_id]"),
    }


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Validate hard-coded endpoint URLs against live llms.txt docs")
    parser.add_argument("--no-strict", action="store_true", help="Print missing endpoints but exit zero")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        result = await validate_endpoints(settings)
    except Exception as exc:
        log.exception("endpoint_validation_error", error=str(exc))
        return 0 if args.no_strict else 1
    if result["ok"]:
        log.info("endpoint_validation_ok", checked=result["checked"])
        return 0
    log.error("endpoint_validation_failed", missing=result["missing"])
    return 0 if args.no_strict else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
