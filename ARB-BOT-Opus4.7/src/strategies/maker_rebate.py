"""Phase 2 — Polymarket maker-rebate harvesting on 15m crypto.

STRATEGY_SYNTHESIS.md §1.12 Phase 2, Opus §C4 + §I (Recommended Phase 2).

Quote tiny $5–$10 GTC postOnly orders 1 tick inside best bid on short-window
BTC crypto markets. The edge is:
   maker_rebate_per_fill = 20% × taker_fee_paid_by_counterparty
   + convergence: 5m markets typically compress to $0.92–$0.99 in final 30s

Cancel-and-replace on book updates. Heartbeat task lives in main; this strategy
just maintains a quote.
"""

from __future__ import annotations

import asyncio

from src.config import get_settings
from src.observability.metrics import orders_submitted
from src.platforms.polymarket.orders import (
    OrderArgs,
    OrderType,
    Side,
    build_and_sign,
)
from src.strategies.base import Strategy


class MakerRebateStrategy(Strategy):
    name = "phase2_maker"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.s = get_settings()
        self._active_orders: dict[str, str] = {}     # token_id → order_id

    async def tick(self) -> None:
        if self.poly is None:
            self.log.warning("phase2.skip.no_polymarket_client")
            return

        # 1. discovery — find target markets matching PHASE2_MARKETS + PHASE2_DURATIONS
        targets = await self._build_universe()
        if not targets:
            self.log.debug("phase2.universe_empty")
            return

        # 2. for each target, ensure a passive quote exists 1 tick inside best
        for market in targets:
            try:
                await self._maintain_quote(market)
            except Exception as exc:
                self.log.exception("phase2.maintain_error", slug=market.get("slug"), error=str(exc))

    async def _build_universe(self) -> list[dict]:
        assert self.poly is not None
        assets = [a.upper() for a in self.s.phase2_markets()]
        durations = self.s.phase2_durations()
        try:
            mkts = await self.poly.gamma_markets(active=True, closed=False, limit=100)
        except Exception as exc:
            self.log.warning("phase2.discovery_failed", error=str(exc))
            return []

        out: list[dict] = []
        for m in mkts:
            text = str(m.get("question") or m.get("title") or m.get("slug") or "").lower()
            if not any(a.lower() in text for a in assets):
                continue
            if not any(d in text for d in durations):
                continue
            out.append(m)
        return out

    async def _maintain_quote(self, market: dict) -> None:
        assert self.poly is not None
        slug = market.get("slug") or market.get("conditionId")
        tokens = market.get("clobTokenIds") or market.get("clob_token_ids") or []
        if isinstance(tokens, str):
            import json
            try:
                tokens = json.loads(tokens)
            except Exception:
                tokens = []
        if not tokens:
            return
        yes_tok = str(tokens[0])

        book = await self.poly.book(yes_tok)
        bids = book.get("bids") or []
        if not bids:
            return

        best_bid = float(bids[0].get("price") or 0.0)
        if best_bid <= 0 or best_bid >= 1:
            return

        tick = await self.poly.tick_size(yes_tok)
        target_price = round(best_bid - (tick * self.s.PHASE2_TICK_INSIDE), 6)
        if target_price <= 0:
            return

        shares = self.s.PHASE2_QUOTE_SIZE_USD / target_price
        # Polymarket 5-share min
        if shares < 5:
            self.log.debug("phase2.below_min_size", slug=slug, shares=shares)
            return

        risk = await self.risk.gate(
            platform="polymarket",
            notional_usd=self.s.PHASE2_QUOTE_SIZE_USD,
        )
        if not risk.allowed:
            self.log.info("phase2.risk_blocked", slug=slug, reason=risk.reason)
            return

        exchange_addr = self._exchange_address(market)
        fee_rate_bps = int(market.get("feeRateBps") or 0)

        if self.dry_run:
            self.log.info(
                "phase2.dry_run.would_quote",
                slug=slug, price=target_price, shares=round(shares, 3), best_bid=best_bid,
            )
            return

        try:
            payload = await build_and_sign(
                self.poly,
                OrderArgs(
                    token_id=yes_tok,
                    price=target_price,
                    size=shares,
                    side=Side.BUY,
                    fee_rate_bps=fee_rate_bps,
                    exchange_address=exchange_addr,
                    order_type=OrderType.GTC,
                    post_only=True,
                ),
            )
            res = await self.poly.submit_order(payload)
            order_id = (res or {}).get("orderId") or (res or {}).get("id")
            if order_id:
                # Cancel prior quote on this market (cancel-then-place isn't atomic but
                # postOnly prevents accidental takes if the book moves under us).
                old = self._active_orders.get(yes_tok)
                if old and old != order_id:
                    try:
                        await self.poly.cancel_order(old)
                    except Exception:
                        pass
                self._active_orders[yes_tok] = str(order_id)
                orders_submitted.labels(platform="polymarket", side="BUY", strategy=self.name).inc()
                self.log.info(
                    "phase2.quote_placed",
                    slug=slug, price=target_price, shares=round(shares, 3), order_id=order_id,
                )
        except Exception as exc:
            self.log.warning("phase2.quote_failed", slug=slug, error=str(exc))

    @staticmethod
    def _exchange_address(market: dict) -> str:
        # Polymarket CTF Exchange V2 address — pulled from market metadata when present.
        addr = market.get("exchange") or market.get("ctfExchange")
        if not addr:
            raise RuntimeError(
                "market metadata missing exchange address; getClobMarketInfo() should populate"
            )
        return str(addr)
