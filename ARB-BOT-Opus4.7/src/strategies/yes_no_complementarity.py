"""Phase 1 strategy — YES + NO complementarity arbitrage on Limitless ONLY.

STRATEGY_SYNTHESIS.md §1.12 Phase 1, Opus §C1, GPT5.5 daily section.

Edge: when ask(YES) + ask(NO) ≤ PHASE1_EDGE_THRESHOLD (default 0.985),
buy 1 YES + 1 NO. Worst case at resolution: $1.00 redeem - cost. Net edge:
  edge = 1.00 - (ask_yes + ask_no) - fees - slippage

Because BOTH legs are on Limitless, BOTH legs face Limitless taker fees, and
there is NO cross-chain bridging or oracle mismatch. This is the genuinely
riskless starter strategy.

Universe filter:
  - category=Crypto, duration ∈ PHASE1_ALLOWED_DURATIONS (default daily,hourly)
  - 24h volume ≥ PHASE1_MIN_24H_VOLUME_USD
  - oracle must NOT be "manual" (we want auto-resolved markets)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import get_settings
from src.fees.calculator import limitless_taker_fee
from src.observability.metrics import orders_submitted
from src.platforms.limitless.orders import (
    LimitlessOrderArgs,
    OrderType,
    Side,
    build_signed_payload,
)
from src.state.positions import Arb, Leg
from src.strategies.base import Strategy


def _now_iso_safe() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(slots=True)
class _OppRow:
    slug: str
    ask_yes: float
    ask_no: float
    sum_asks: float
    market_meta: dict


class YesNoComplementarityStrategy(Strategy):
    name = "phase1_yes_no"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.s = get_settings()

    async def tick(self) -> None:
        if self.lim is None:
            self.log.warning("phase1.skip.no_limitless_client")
            return

        # Lazy-load profile id and fee rate (cached on the client)
        if self.lim.cached_profile_id() is None and not self.dry_run:
            try:
                await self.lim.portfolio_profile()
            except Exception as exc:
                self.log.warning("phase1.profile_fetch_failed", error=str(exc))

        # 1. discover universe
        markets = await self._build_universe()

        # 2. scan ALL universe markets (regardless of threshold) so we can
        # report the best near-miss in the tick summary.
        candidates = await self._scan_books(markets) if markets else []
        crossed = [c for c in candidates if c.sum_asks <= self.s.PHASE1_EDGE_THRESHOLD]

        # Always emit per-tick summary at INFO so paper-trade run is observable.
        best_sum = min((c.sum_asks for c in candidates), default=None) if candidates else None
        sample = candidates[0] if candidates else None
        self.log.info(
            "phase1.tick_summary",
            universe_size=len(markets),
            evaluated=len(candidates),
            crossed_threshold=len(crossed),
            best_sum_asks=round(best_sum, 4) if best_sum is not None else None,
            sample_slug=sample.slug if sample else None,
            sample_yes_ask=sample.ask_yes if sample else None,
            sample_no_ask=sample.ask_no if sample else None,
            threshold=self.s.PHASE1_EDGE_THRESHOLD,
        )

        if not crossed:
            return

        # 3. trade the best one (one at a time per tick — we don't queue races)
        crossed.sort(key=lambda r: r.sum_asks)
        best = crossed[0]
        await self._execute(best)

    async def _build_universe(self) -> list[dict]:
        assert self.lim is not None
        try:
            slugs = await self.lim.markets_active_slugs()
        except Exception as exc:
            self.log.warning("phase1.discovery_failed", error=str(exc))
            return []

        # The /markets/active/slugs endpoint is intentionally lightweight and
        # does NOT include volume24h — so we cannot enforce
        # PHASE1_MIN_24H_VOLUME_USD here. We use ticker + slug pattern instead.
        # Per-market detail (with volume) is fetched in _scan_books for the
        # candidates that pass.
        durations = set(self.s.phase1_durations())
        accept_any_duration = "any" in durations
        out: list[dict] = []
        for s in slugs:
            ticker = str(s.get("ticker") or "").upper()
            slug_text = str(s.get("slug") or "").lower()
            haystack = f"{ticker} {slug_text}"
            asset = self._asset(haystack)
            if asset == "?":
                continue
            if not accept_any_duration:
                duration = self._classify(slug_text, s)
                if duration not in durations:
                    continue
            out.append(s)
        return out

    @staticmethod
    def _asset(text: str) -> str:
        t = text.lower()
        if "btc" in t or "bitcoin" in t:
            return "BTC"
        if "eth" in t or "ether" in t:
            return "ETH"
        if "sol" in t:
            return "SOL"
        return "?"

    @staticmethod
    def _classify(text: str, market: dict) -> str:
        t = text.lower()
        if "daily" in t or " today " in t or " by midnight" in t:
            return "daily"
        if "hourly" in t or " in 1h" in t:
            return "hourly"
        # Best-effort fallback by deadline if duration not in title
        return str(market.get("duration") or market.get("durationClass") or "").lower()

    async def _scan_books(self, universe: list[dict]) -> list[_OppRow]:
        """Scan candidate markets for YES+NO arb opportunities.

        The Limitless `/markets/{slug}/orderbook` endpoint only returns ONE
        token's book at a time, but the market-detail endpoint
        (`/markets/{slug}`) exposes BOTH sides at `tradePrices.buy.market[0/1]`
        (YES/NO market buy asks). Using market detail is one request per slug
        and gives both legs.
        """
        assert self.lim is not None
        opp: list[_OppRow] = []
        threshold = self.s.PHASE1_EDGE_THRESHOLD
        failures = 0
        for entry in universe:
            slug = entry.get("slug")
            if not slug:
                continue
            try:
                full = await self.lim.market(slug)
            except Exception as exc:
                failures += 1
                self.log.debug("phase1.market_fetch_failed", slug=slug, error=str(exc)[:80])
                continue
            if not full:
                continue

            ask_yes, ask_no = self._asks_from_market_detail(full)
            if ask_yes is None or ask_no is None:
                continue
            # Skip markets without two-sided liquidity. A price of 0.00 means
            # the buy side has no orders — we cannot actually fill at that
            # price, so it must not count as an opportunity.
            if ask_yes <= 0.0 or ask_no <= 0.0:
                continue
            sum_asks = ask_yes + ask_no
            opp.append(_OppRow(
                slug=slug, ask_yes=ask_yes, ask_no=ask_no, sum_asks=sum_asks, market_meta=full,
            ))
        # Only mark as `opportunity` if it crosses the threshold; but keep ALL
        # rows so we can emit the best_sum_asks in the tick summary.
        if failures:
            self.log.info("phase1.book_fetch_failures", count=failures)
        return opp

    @staticmethod
    def _asks_from_market_detail(market: dict) -> tuple[float | None, float | None]:
        """Pull YES and NO market-buy asks from Limitless market detail.

        Shape: `tradePrices.buy.market = [yes_market_ask, no_market_ask]`.
        Falls back to None if the shape differs.
        """
        tp = market.get("tradePrices") or {}
        buy = tp.get("buy") or {}
        market_asks = buy.get("market")
        if not isinstance(market_asks, list) or len(market_asks) < 2:
            return None, None
        try:
            return float(market_asks[0]), float(market_asks[1])
        except (ValueError, TypeError):
            return None, None

    async def _execute(self, opp: _OppRow) -> None:
        """Build the YES+NO atomic-ish pair using FAK on both legs."""
        assert self.lim is not None

        position_usd = self.s.PHASE1_MAX_POSITION_USD
        shares = position_usd / max(opp.sum_asks, 0.01)

        risk = await self.risk.gate(
            platform="limitless",
            notional_usd=position_usd,
            duration_class="daily",
            net_edge_pct=(1.0 - opp.sum_asks) * 100.0,
        )
        if not risk.allowed:
            self.log.info("phase1.risk_blocked", slug=opp.slug, reason=risk.reason)
            return

        # Fee sanity — confirm post-fee edge still positive
        fee_yes = limitless_taker_fee(
            notional_usd=shares * opp.ask_yes,
            price=opp.ask_yes,
            is_buy=True,
            market_meta=opp.market_meta,
            profile_fee_rate_bps=self.lim.cached_fee_rate_bps(),
        )
        fee_no = limitless_taker_fee(
            notional_usd=shares * opp.ask_no,
            price=opp.ask_no,
            is_buy=True,
            market_meta=opp.market_meta,
            profile_fee_rate_bps=self.lim.cached_fee_rate_bps(),
        )
        gross_edge_usd = shares * (1.0 - opp.sum_asks)
        net_edge_usd = gross_edge_usd - fee_yes.taker_fee_usd - fee_no.taker_fee_usd
        if net_edge_usd <= 0:
            self.log.info(
                "phase1.no_net_edge",
                slug=opp.slug,
                gross_usd=gross_edge_usd,
                fees_usd=fee_yes.taker_fee_usd + fee_no.taker_fee_usd,
            )
            return

        meta = opp.market_meta
        venue = (meta.get("venue") or {})
        exchange_addr = venue.get("exchange")
        # Limitless uses `tokens.yes` / `tokens.no` in the market-detail
        # response; older shapes used `positionIds: [yes, no]`. Accept both.
        position_ids = meta.get("positionIds") or []
        tokens = meta.get("tokens") or {}
        yes_tok = None
        no_tok = None
        if isinstance(tokens, dict) and tokens.get("yes") and tokens.get("no"):
            yes_tok, no_tok = str(tokens["yes"]), str(tokens["no"])
        elif len(position_ids) >= 2:
            yes_tok, no_tok = str(position_ids[0]), str(position_ids[1])
        if not exchange_addr or not yes_tok or not no_tok:
            self.log.warning(
                "phase1.market_missing_fields",
                slug=opp.slug,
                has_exchange=bool(exchange_addr),
                has_tokens=bool(yes_tok and no_tok),
            )
            return
        fee_rate_bps = self._fee_rate_bps(meta)

        arb = Arb(strategy=self.name)
        leg_yes = Leg(
            arb_id=arb.arb_id, platform="limitless", market_key=opp.slug,
            side="YES", intended_size=shares, status="pending",
        )
        leg_no = Leg(
            arb_id=arb.arb_id, platform="limitless", market_key=opp.slug,
            side="NO", intended_size=shares, status="pending",
        )
        arb.legs = [leg_yes, leg_no]
        await self.store.upsert_arb(arb)

        self.log.info(
            "phase1.opportunity",
            slug=opp.slug,
            ask_yes=opp.ask_yes,
            ask_no=opp.ask_no,
            shares=round(shares, 3),
            gross_edge_usd=round(gross_edge_usd, 4),
            net_edge_usd=round(net_edge_usd, 4),
        )

        if self.dry_run:
            # PAPER TRADE: simulate the FAK fills at the observed ask prices.
            # YES+NO redeems for $1.00 at resolution regardless of outcome,
            # so realized PnL is deterministic from entry prices and fees.
            import uuid as _uuid

            leg_yes.status = "filled"
            leg_yes.filled_size = shares
            leg_yes.avg_price = opp.ask_yes
            leg_yes.order_ids.append(f"paper-{_uuid.uuid4()}")
            leg_yes.closed_at_utc = _now_iso_safe()
            leg_yes.realized_pnl_usd = None  # PnL is tracked at the arb level

            leg_no.status = "filled"
            leg_no.filled_size = shares
            leg_no.avg_price = opp.ask_no
            leg_no.order_ids.append(f"paper-{_uuid.uuid4()}")
            leg_no.closed_at_utc = _now_iso_safe()
            leg_no.realized_pnl_usd = None

            await self.store.upsert_arb(arb)
            await self.store.close_arb(arb.arb_id, realized_pnl_usd=net_edge_usd)
            self.log.info(
                "phase1.paper_trade_closed",
                arb_id=arb.arb_id,
                slug=opp.slug,
                shares=round(shares, 3),
                cost_usd=round(shares * opp.sum_asks, 4),
                fees_usd=round(fee_yes.taker_fee_usd + fee_no.taker_fee_usd, 4),
                realized_pnl_usd=round(net_edge_usd, 4),
            )
            return

        owner_id = self.lim.cached_profile_id()
        if not owner_id:
            self.log.error("phase1.no_owner_id")
            return

        yes_payload = build_signed_payload(
            LimitlessOrderArgs(
                market_slug=opp.slug, token_id=yes_tok, price=opp.ask_yes,
                size=shares, side=Side.BUY, fee_rate_bps=fee_rate_bps,
                exchange_address=exchange_addr, order_type=OrderType.FAK,
            ),
            owner_id=owner_id,
        )
        no_payload = build_signed_payload(
            LimitlessOrderArgs(
                market_slug=opp.slug, token_id=no_tok, price=opp.ask_no,
                size=shares, side=Side.BUY, fee_rate_bps=fee_rate_bps,
                exchange_address=exchange_addr, order_type=OrderType.FAK,
            ),
            owner_id=owner_id,
        )

        results = await asyncio.gather(
            self.lim.submit_order(yes_payload),
            self.lim.submit_order(no_payload),
            return_exceptions=True,
        )
        for side_name, res, leg in (("YES", results[0], leg_yes), ("NO", results[1], leg_no)):
            if isinstance(res, Exception):
                leg.status = "hedge_failed"
                leg.notes = f"submit_error: {res!r}"[:200]
                self.log.error("phase1.submit_failed", side=side_name, error=str(res))
            else:
                order_id = (res or {}).get("orderId") or (res or {}).get("id")
                if order_id:
                    leg.order_ids.append(str(order_id))
                orders_submitted.labels(
                    platform="limitless", side=side_name, strategy=self.name
                ).inc()
        await self.store.upsert_arb(arb)

    @staticmethod
    def _fee_rate_bps(meta: dict) -> int:
        sched = meta.get("feeSchedule") or {}
        if "buyBps" in sched:
            return int(sched["buyBps"])
        if "feeRateBps" in meta:
            return int(meta["feeRateBps"])
        # Conservative fallback — 100 bps. Will be tightened by per-market metadata in practice.
        return 100
