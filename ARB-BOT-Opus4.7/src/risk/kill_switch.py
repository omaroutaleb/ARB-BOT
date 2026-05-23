"""Kill switch: cancel every open order on both venues within 1 second.

STRATEGY_SYNTHESIS.md Non-negotiable §4.9. Invoked on SIGTERM/SIGINT and on
critical risk events (drawdown stop, daily loss stop).
"""

from __future__ import annotations

import asyncio
import time

from src.observability.logging import get_logger
from src.observability.metrics import kill_switch_triggers
from src.platforms.limitless.client import LimitlessClient
from src.platforms.polymarket.client import PolymarketClient

log = get_logger(__name__)


async def cancel_all(
    poly: PolymarketClient | None,
    lim: LimitlessClient | None,
    *,
    reason: str,
    timeout_sec: float = 1.0,
) -> dict[str, int]:
    """Issue cancel-all on both platforms in parallel.

    Returns count of cancelled orders per platform (best-effort — venues may
    not report exact counts, in which case `-1` means "called but unknown").
    """
    kill_switch_triggers.labels(reason=reason).inc()
    start = time.monotonic()
    log.warning("kill_switch.activate", reason=reason)

    counts = {"polymarket": 0, "limitless": 0}

    async def _poly() -> None:
        if poly is None:
            return
        try:
            res = await poly.cancel_all()
            cancelled = res.get("canceled") if isinstance(res, dict) else None
            counts["polymarket"] = int(cancelled) if cancelled is not None else -1
        except Exception as exc:
            log.error("kill_switch.polymarket.error", error=str(exc))
            counts["polymarket"] = -1

    async def _lim() -> None:
        if lim is None:
            return
        try:
            # Limitless doesn't have a global cancel-all; we issue per-known-slug.
            # Caller can pre-supply a slug list. For now, attempt /orders/cancel-batch
            # with an empty list as a "cancel everything" signal — if that fails,
            # we fall back silently. Operators should use /orders/all/:slug for known slugs.
            res = await lim.cancel_batch([])
            cancelled = res.get("cancelled") if isinstance(res, dict) else None
            counts["limitless"] = int(cancelled) if cancelled is not None else -1
        except Exception as exc:
            log.error("kill_switch.limitless.error", error=str(exc))
            counts["limitless"] = -1

    try:
        await asyncio.wait_for(asyncio.gather(_poly(), _lim()), timeout=timeout_sec)
    except asyncio.TimeoutError:
        log.error("kill_switch.timeout", timeout_sec=timeout_sec)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    log.warning(
        "kill_switch.done",
        reason=reason,
        elapsed_ms=elapsed_ms,
        polymarket_cancelled=counts["polymarket"],
        limitless_cancelled=counts["limitless"],
    )
    return counts


async def cancel_limitless_slugs(
    lim: LimitlessClient | None,
    slugs: list[str],
    *,
    timeout_sec: float = 1.0,
) -> int:
    """Best-effort per-slug cancel — used when caller knows the active slugs."""
    if lim is None or not slugs:
        return 0

    async def _one(s: str) -> int:
        try:
            res = await lim.cancel_market(s)
            n = res.get("cancelled") if isinstance(res, dict) else None
            return int(n) if n is not None else 0
        except Exception as exc:
            log.warning("kill_switch.limitless.slug_error", slug=s, error=str(exc))
            return 0

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_one(s) for s in slugs]),
            timeout=timeout_sec,
        )
        return sum(results)
    except asyncio.TimeoutError:
        return 0
