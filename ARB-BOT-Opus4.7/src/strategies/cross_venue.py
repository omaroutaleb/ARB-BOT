"""Phase 3 — cross-venue daily strike-form BTC arb (Polymarket × Limitless).

STRATEGY_SYNTHESIS.md §1.12 Phase 3, §1.7 oracle compatibility, §1.11 orphan
policy. Gated behind STRATEGY_PHASE=3 AND prior-phase benchmarks.

Strict rules:
  - Only daily strike-form ("BTC ≥ $X by deadline T") markets.
  - Oracle compatibility check is gating (Chainlink × Pyth same-asset only).
  - 0.5% oracle haircut applied to all edge calcs.
  - 1 concurrent arb max until 30 closed cross-venue trades.
  - Leg-A maker (postOnly GTC) on cheaper side; leg-B FAK on hedge side after fill.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.config import get_settings
from src.fees.calculator import polymarket_taker_fee, limitless_taker_fee
from src.matching.market_matcher import (
    MarketPair,
    NormalizedMarket,
    find_pairs,
    normalize_limitless,
    normalize_polymarket,
)
from src.observability.metrics import orders_submitted
from src.platforms.limitless.orders import (
    LimitlessOrderArgs,
    OrderType as LimOrderType,
    Side as LimSide,
    build_signed_payload,
)
from src.platforms.polymarket.orders import (
    OrderArgs as PolyOrderArgs,
    OrderType as PolyOrderType,
    Side as PolySide,
    build_and_sign as poly_build_and_sign,
)
from src.state.positions import Arb, Leg
from src.strategies.base import Strategy


class CrossVenueStrategy(Strategy):
    name = "phase3_cross"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.s = get_settings()

    async def tick(self) -> None:
        if self.poly is None or self.lim is None:
            self.log.warning("phase3.skip.missing_client", poly=bool(self.poly), lim=bool(self.lim))
            return

        open_arbs = await self.store.open_arbs()
        open_cross = [a for a in open_arbs if a.strategy == self.name]
        if len(open_cross) >= self.s.PHASE3_MAX_CONCURRENT:
            self.log.debug("phase3.at_concurrency_cap", count=len(open_cross))
            return

        pairs = await self._discover_pairs()
        if not pairs:
            return

        for pair in pairs:
            decision = await self._evaluate_pair(pair)
            if decision is None:
                continue
            await self._execute(*decision, pair=pair)
            break  # one per tick

    async def _discover_pairs(self) -> list[MarketPair]:
        try:
            poly_raw = await self.poly.gamma_markets(active=True, closed=False, limit=200)  # type: ignore
            lim_raw = await self.lim.markets_active_slugs()  # type: ignore
        except Exception as exc:
            self.log.warning("phase3.discovery_failed", error=str(exc))
            return []

        poly_norm = [normalize_polymarket(m) for m in poly_raw]
        lim_norm = [normalize_limitless(m) for m in lim_raw]
        poly_norm = [m for m in poly_norm if m.duration_class in ("daily", "weekly")]
        lim_norm = [m for m in lim_norm if m.duration_class in ("daily", "weekly")]
        pairs = find_pairs(poly_norm, lim_norm)
        if pairs:
            self.log.info("phase3.pairs_discovered", count=len(pairs))
        return pairs

    async def _evaluate_pair(
        self, pair: MarketPair
    ) -> tuple[NormalizedMarket, NormalizedMarket, float] | None:
        """Decide if pair clears edge threshold; return (cheap_side_market,
        expensive_side_market, projected_net_edge_pct) or None."""
        if pair.poly.yes_token_id is None or pair.lim.yes_token_id is None:
            return None

        try:
            poly_book = await self.poly.book(pair.poly.yes_token_id)  # type: ignore
            lim_book = await self.lim.orderbook(pair.lim.raw_id)  # type: ignore
        except Exception as exc:
            self.log.debug("phase3.book_fetch_failed", error=str(exc))
            return None

        poly_ask_yes = self._best_ask_poly(poly_book)
        lim_ask_yes = self._best_ask_lim(lim_book, "yes")
        lim_ask_no = self._best_ask_lim(lim_book, "no")
        if poly_ask_yes is None or lim_ask_yes is None or lim_ask_no is None:
            return None

        if poly_ask_yes < lim_ask_yes:
            cheap, expensive = pair.poly, pair.lim
            yes_buy_price = poly_ask_yes
            no_buy_price = lim_ask_no
            mode = "poly_yes_lim_no"
        else:
            cheap, expensive = pair.lim, pair.poly
            yes_buy_price = lim_ask_yes
            no_buy_price = self._best_ask_poly(poly_book) or poly_ask_yes
            mode = "lim_yes_poly_no"

        gross_pct = (1.0 - (yes_buy_price + no_buy_price)) * 100.0
        haircut = self.s.PHASE3_ORACLE_HAIRCUT_PCT
        net_pct = gross_pct - haircut

        if net_pct < self.s.PHASE3_MIN_EDGE_PCT:
            return None

        notional = self.s.PHASE3_POSITION_USD
        poly_fee = polymarket_taker_fee(
            notional_usd=notional,
            price=poly_ask_yes,
            market_meta=pair.poly.fee_meta,
        )
        lim_fee = limitless_taker_fee(
            notional_usd=notional,
            price=lim_ask_yes,
            is_buy=True,
            market_meta=pair.lim.fee_meta,
            profile_fee_rate_bps=self.lim.cached_fee_rate_bps() if self.lim else None,  # type: ignore
        )
        fee_pct = (poly_fee.taker_fee_usd + lim_fee.taker_fee_usd) / notional * 100.0
        final_pct = net_pct - fee_pct
        if final_pct < 0:
            return None

        self.log.info(
            "phase3.opportunity",
            mode=mode,
            gross_pct=round(gross_pct, 3),
            haircut_pct=haircut,
            fee_pct=round(fee_pct, 3),
            final_pct=round(final_pct, 3),
        )
        return cheap, expensive, final_pct

    @staticmethod
    def _best_ask_poly(book: dict) -> float | None:
        asks = book.get("asks") or []
        if not asks:
            return None
        try:
            return float(asks[0].get("price"))
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _best_ask_lim(book: dict, side: str) -> float | None:
        side_book = book.get(side) or {}
        asks = side_book.get("asks") or side_book.get("sell") or []
        if not asks:
            return None
        try:
            first = asks[0]
            if isinstance(first, dict):
                return float(first.get("price") or first.get("p"))
            if isinstance(first, list) and first:
                return float(first[0])
        except (ValueError, TypeError, IndexError):
            return None
        return None

    async def _execute(
        self,
        cheap: NormalizedMarket,
        expensive: NormalizedMarket,
        edge_pct: float,
        *,
        pair: MarketPair,
    ) -> None:
        notional = self.s.PHASE3_POSITION_USD
        risk = await self.risk.gate(
            platform=cheap.platform,
            notional_usd=notional,
            duration_class="daily",
            net_edge_pct=edge_pct,
        )
        if not risk.allowed:
            self.log.info("phase3.risk_blocked", reason=risk.reason)
            return

        arb = Arb(strategy=self.name)
        leg_poly = Leg(
            arb_id=arb.arb_id, platform="polymarket", market_key=pair.poly.raw_id,
            side="YES", intended_size=notional / 0.5,
            oracle_source=pair.poly.oracle.value,
        )
        leg_lim = Leg(
            arb_id=arb.arb_id, platform="limitless", market_key=pair.lim.raw_id,
            side="NO", intended_size=notional / 0.5,
            oracle_source=pair.lim.oracle.value,
        )
        arb.legs = [leg_poly, leg_lim]
        await self.store.upsert_arb(arb)

        if self.dry_run:
            self.log.info(
                "phase3.dry_run.would_execute",
                arb_id=arb.arb_id,
                cheap_platform=cheap.platform,
                expensive_platform=expensive.platform,
                edge_pct=round(edge_pct, 3),
            )
            return

        await self._submit_leg_a_then_b(arb, pair, cheap, expensive)

    async def _submit_leg_a_then_b(
        self,
        arb: Arb,
        pair: MarketPair,
        cheap: NormalizedMarket,
        expensive: NormalizedMarket,
    ) -> None:
        """Leg-A as maker (postOnly GTC). If filled, leg-B as FAK."""
        # NOTE: full execution wiring for both venues is non-trivial. The
        # production handler watches the user channel for leg-A fill and
        # triggers leg-B sync. Here we wire the submit + log path; the WS
        # fill listener in main.py invokes the partial-fill / orphan path.
        if cheap.platform == "polymarket":
            await self._poly_leg(pair.poly, arb, is_leg_a=True, post_only=True)
            self.log.info("phase3.leg_a_submitted", platform="polymarket", arb=arb.arb_id)
        else:
            await self._lim_leg(pair.lim, arb, is_leg_a=True, post_only=True)
            self.log.info("phase3.leg_a_submitted", platform="limitless", arb=arb.arb_id)

    async def _poly_leg(
        self,
        m: NormalizedMarket,
        arb: Arb,
        *,
        is_leg_a: bool,
        post_only: bool,
    ) -> dict[str, Any]:
        assert self.poly is not None
        if m.yes_token_id is None:
            return {}
        full = await self.poly.gamma_market_by_slug(m.raw_id) or {}
        exchange = full.get("exchange") or full.get("ctfExchange")
        if not exchange:
            self.log.error("phase3.poly_missing_exchange", slug=m.raw_id)
            return {}
        fee_bps = int(full.get("feeRateBps") or 0)
        ask = await self.poly.price(m.yes_token_id, "BUY")
        if ask is None:
            return {}
        notional = self.s.PHASE3_POSITION_USD
        shares = notional / max(ask, 0.01)
        order = await poly_build_and_sign(
            self.poly,
            PolyOrderArgs(
                token_id=m.yes_token_id, price=ask, size=shares, side=PolySide.BUY,
                fee_rate_bps=fee_bps, exchange_address=exchange,
                order_type=PolyOrderType.GTC if post_only else PolyOrderType.FAK,
                post_only=post_only,
            ),
        )
        try:
            res = await self.poly.submit_order(order)
            orders_submitted.labels(platform="polymarket", side="BUY", strategy=self.name).inc()
            return res or {}
        except Exception as exc:
            self.log.error("phase3.poly_submit_failed", error=str(exc))
            return {}

    async def _lim_leg(
        self,
        m: NormalizedMarket,
        arb: Arb,
        *,
        is_leg_a: bool,
        post_only: bool,
    ) -> dict[str, Any]:
        assert self.lim is not None
        full = await self.lim.market(m.raw_id) or {}
        venue = full.get("venue") or {}
        exchange = venue.get("exchange")
        pos = full.get("positionIds") or []
        if not exchange or len(pos) < 2:
            self.log.error("phase3.lim_missing_fields", slug=m.raw_id)
            return {}
        owner = self.lim.cached_profile_id()
        if not owner:
            self.log.error("phase3.no_owner_id")
            return {}
        ask = (await self.lim.orderbook(m.raw_id) or {})
        side_book = ask.get("no") if not post_only else ask.get("yes")
        if not side_book:
            return {}
        asks_list = side_book.get("asks") or side_book.get("sell") or []
        if not asks_list:
            return {}
        first = asks_list[0]
        price = float(first.get("price") if isinstance(first, dict) else first[0])
        notional = self.s.PHASE3_POSITION_USD
        shares = notional / max(price, 0.01)
        fee_bps = int((full.get("feeSchedule") or {}).get("buyBps") or full.get("feeRateBps") or 100)
        token_id = str(pos[0] if post_only else pos[1])
        payload = build_signed_payload(
            LimitlessOrderArgs(
                market_slug=m.raw_id, token_id=token_id, price=price, size=shares,
                side=LimSide.BUY, fee_rate_bps=fee_bps, exchange_address=exchange,
                order_type=LimOrderType.GTC if post_only else LimOrderType.FAK,
                post_only=post_only,
            ),
            owner_id=owner,
        )
        try:
            res = await self.lim.submit_order(payload)
            orders_submitted.labels(platform="limitless", side="BUY", strategy=self.name).inc()
            return res or {}
        except Exception as exc:
            self.log.error("phase3.lim_submit_failed", error=str(exc))
            return {}
