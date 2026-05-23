from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.config import Settings
from src.fees.calculator import FeeDataMissing, FeeSchedule, calculate_fee
from src.observability.logging import get_logger
from src.observability.metrics import OPPORTUNITIES
from src.platforms.limitless.orders import create_limitless_order, sign_limitless_order
from src.risk.limits import RiskEngine, RiskState
from src.state.db import BotDB, TradeJournal
from src.strategies.base import Opportunity, Strategy


log = get_logger(__name__)


class YesNoComplementarityStrategy(Strategy):
    name = "yes_no_complementarity"

    def __init__(
        self,
        *,
        settings: Settings,
        db: BotDB,
        journal: TradeJournal,
        risk: RiskEngine,
        risk_state: RiskState,
        limitless_client: Any | None = None,
        dry_run_books: list[dict[str, Any]] | None = None,
    ):
        self.settings = settings
        self.db = db
        self.journal = journal
        self.risk = risk
        self.risk_state = risk_state
        self.limitless_client = limitless_client
        self.dry_run_books = dry_run_books or []

    async def scan(self) -> list[Opportunity]:
        # PAPER-TRADE PATCH: in dry-run we still scan LIVE markets, so the bot
        # exercises the same code path it would live and accumulates real trade
        # decisions. The pre-loaded dry_run_books fixture is only used when no
        # live client is available (offline tests).
        if self.dry_run_books and self.limitless_client is None:
            markets = self.dry_run_books
        else:
            markets = await self._live_market_books()
        opportunities: list[Opportunity] = []
        best_sum = None
        for market in markets:
            try:
                opportunity = self._evaluate_market(market)
            except FeeDataMissing as exc:
                log.warning("fee_data_missing", strategy=self.name, market=market.get("slug"), reason=str(exc))
                continue
            # Track best sum-of-asks even when no opportunity (for tick summary).
            try:
                yes_ask = _best_ask(market, "YES")
                no_ask = _best_ask(market, "NO")
                if yes_ask is not None and no_ask is not None:
                    s = float(yes_ask) + float(no_ask)
                    if best_sum is None or s < best_sum:
                        best_sum = s
            except Exception:
                pass
            if opportunity:
                OPPORTUNITIES.labels(self.name, "limitless").inc()
                opportunities.append(opportunity)
        log.info(
            "phase1_tick_summary",
            strategy=self.name,
            universe_size=len(markets),
            opportunities=len(opportunities),
            best_sum_asks=round(best_sum, 4) if best_sum is not None else None,
            threshold=float(self.settings.limitless_complement_ask_max),
        )
        return opportunities

    async def execute(self, opportunity: Opportunity) -> None:
        decision = self.risk.check_trade(
            venue="limitless",
            notional_usd=opportunity.size_usd,
            state=self.risk_state,
        )
        if not decision.approved:
            log.info("opportunity_rejected", strategy=self.name, reason=decision.reason)
            return
        arb_id = str(uuid4())
        if self.settings.dry_run:
            pnl = float(opportunity.edge)
            self.db.record_trade(
                arb_id=arb_id,
                strategy=self.name,
                venue="limitless",
                market_key=opportunity.market_key,
                pnl_usd=pnl,
                closed=True,
                payload=opportunity.payload,
            )
            self.journal.append(
                {
                    "arb_id": arb_id,
                    "strategy": self.name,
                    "venue": "limitless",
                    "market_key": opportunity.market_key,
                    "pnl_usd": pnl,
                    "dry_run": True,
                }
            )
            log.info("dry_run_trade_closed", strategy=self.name, market=opportunity.market_key, pnl_usd=pnl)
            return
        # Live mode intentionally requires venue order details from runtime market
        # metadata. Missing minimum size, fee, or token data must fail before this.
        if self.limitless_client is None:
            raise RuntimeError("Limitless client is required for live Phase 1 execution")
        if self.settings.limitless_private_key is None or self.settings.limitless_address is None:
            raise RuntimeError("Limitless private key and address are required for live order signing")
        payload = opportunity.payload
        required = ["market_slug", "yes_token_id", "no_token_id", "venue_exchange", "fee_rate_bps", "shares", "yes_ask", "no_ask", "owner_id"]
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            raise RuntimeError(f"Limitless live order metadata missing: {missing}")
        private_key = self.settings.limitless_private_key.get_secret_value()
        owner_id = str(payload["owner_id"])
        client_order_id = str(uuid4())
        yes_order = create_limitless_order(
            maker=self.settings.limitless_address,
            signer=self.settings.limitless_address,
            token_id=int(payload["yes_token_id"]),
            price=float(payload["yes_ask"]),
            size=float(payload["shares"]),
            side="BUY",
            fee_rate_bps=int(payload["fee_rate_bps"]),
        )
        no_order = create_limitless_order(
            maker=self.settings.limitless_address,
            signer=self.settings.limitless_address,
            token_id=int(payload["no_token_id"]),
            price=float(payload["no_ask"]),
            size=float(payload["shares"]),
            side="BUY",
            fee_rate_bps=int(payload["fee_rate_bps"]),
        )
        yes_signature = sign_limitless_order(
            private_key,
            yes_order,
            verifying_contract=str(payload["venue_exchange"]),
        )
        no_signature = sign_limitless_order(
            private_key,
            no_order,
            verifying_contract=str(payload["venue_exchange"]),
        )
        # The two reports agree on FAK for hedge/atomicity at this bankroll and
        # warn against FOK. The exact Limitless POST body has moved across docs,
        # so every venue-specific field here is sourced from live market/profile
        # metadata and any missing field rejects before signing.
        yes_payload = {
            "ownerId": owner_id,
            "marketSlug": payload["market_slug"],
            "orderType": "FAK",
            "clientOrderId": f"{client_order_id}-YES",
            "order": asdict(yes_order),
            "signature": yes_signature,
        }
        no_payload = {
            "ownerId": owner_id,
            "marketSlug": payload["market_slug"],
            "orderType": "FAK",
            "clientOrderId": f"{client_order_id}-NO",
            "order": asdict(no_order),
            "signature": no_signature,
        }
        await asyncio.gather(
            self.limitless_client.post_order(yes_payload),
            self.limitless_client.post_order(no_payload),
        )
        log.info("live_orders_submitted", strategy=self.name, market=opportunity.market_key, order_type="FAK")

    def _evaluate_market(self, market: dict[str, Any]) -> Opportunity | None:
        yes_ask = _best_ask(market, "YES")
        no_ask = _best_ask(market, "NO")
        if yes_ask is None or no_ask is None:
            return None
        fee_schedule = FeeSchedule.from_market("limitless", market, market.get("profile"))
        size = self.settings.phase1_trade_size_usd
        yes_size = size / max(yes_ask, 0.01)
        no_size = size / max(no_ask, 0.01)
        shares = min(yes_size, no_size)
        yes_fee = calculate_fee(fee_schedule, side="BUY", role="taker", price=yes_ask, size=shares)
        no_fee = calculate_fee(fee_schedule, side="BUY", role="taker", price=no_ask, size=shares)
        total_cost_per_share = Decimal(str(yes_ask)) + Decimal(str(no_ask))
        gross_edge = (Decimal("1") - total_cost_per_share) * Decimal(str(shares))
        net_edge = gross_edge - yes_fee - no_fee
        if total_cost_per_share <= Decimal(str(self.settings.limitless_complement_ask_max)) and net_edge > 0:
            return Opportunity(
                strategy=self.name,
                venue="limitless",
                market_key=str(market.get("slug") or market.get("id")),
                edge=net_edge,
                size_usd=size,
                payload={
                    "market_slug": str(market.get("slug") or market.get("id")),
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "shares": shares,
                    "gross_edge": str(gross_edge),
                    "net_edge": str(net_edge),
                    "yes_token_id": _position_id(market, 0),
                    "no_token_id": _position_id(market, 1),
                    "venue_exchange": _venue_exchange(market),
                    "fee_rate_bps": _fee_rate_bps(fee_schedule),
                    "owner_id": (market.get("profile") or {}).get("id"),
                },
            )
        return None

    async def _live_market_books(self) -> list[dict[str, Any]]:
        if self.limitless_client is None:
            return []
        # PAPER-TRADE PATCH: skip /portfolio/profile in dry-run since we may not
        # have a valid API key. Profile is only needed for live order owner_id.
        try:
            profile = await self.limitless_client.profile()
        except Exception as exc:
            if self.settings.dry_run:
                log.info("profile_skipped_in_dry_run", reason=str(exc)[:80])
                profile = {}
            else:
                raise
        slugs_payload = await self.limitless_client.active_slugs()
        slugs = _extract_slugs_with_filter(slugs_payload)
        books: list[dict[str, Any]] = []
        # PAPER-TRADE PATCH: tolerate per-market failures and pre-filter to
        # crypto tickers so we do not 400 on AMM/sports markets.
        for slug in slugs[:50]:
            try:
                market = await self.limitless_client.market(slug)
                # Pull both YES/NO asks from market detail; orderbook endpoint
                # returns only one tokens book at a time on Limitless.
                book = self._book_from_market_detail(market)
                market.update({"orderbook": book, "profile": profile})
                books.append(market)
            except Exception as exc:
                log.debug("market_fetch_failed", slug=slug, error=str(exc)[:120])
                continue
        return books

    @staticmethod
    def _book_from_market_detail(market: dict[str, Any]) -> dict[str, Any]:
        """Derive a {yesAsks, noAsks} book from market.tradePrices.buy.market."""
        tp = (market.get("tradePrices") or {}).get("buy") or {}
        m = tp.get("market")
        if isinstance(m, list) and len(m) >= 2:
            try:
                yes_ask = float(m[0])
                no_ask = float(m[1])
                return {
                    "yesAsks": [{"price": yes_ask, "size": 0}],
                    "noAsks": [{"price": no_ask, "size": 0}],
                }
            except (ValueError, TypeError):
                pass
        return {}


def _best_ask(market: dict[str, Any], outcome: str) -> float | None:
    book = market.get("orderbook") or market
    key_candidates = [f"{outcome.lower()}Asks", f"{outcome.lower()}_asks", outcome.lower()]
    levels = None
    for key in key_candidates:
        value = book.get(key)
        if isinstance(value, list):
            levels = value
            break
    if levels is None and isinstance(book.get("asks"), dict):
        levels = book["asks"].get(outcome) or book["asks"].get(outcome.lower())
    if not levels:
        return None
    first = levels[0]
    if isinstance(first, dict):
        return float(first.get("price"))
    if isinstance(first, (list, tuple)):
        return float(first[0])
    return float(first)


def _extract_slugs(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [str(item.get("slug") if isinstance(item, dict) else item) for item in payload]
    if isinstance(payload, dict):
        items = payload.get("markets") or payload.get("data") or payload.get("slugs") or []
        return _extract_slugs(items)
    return []


def _extract_slugs_with_filter(payload):
    """Like _extract_slugs but keeps only crypto tickers (BTC/ETH/SOL/etc)."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("markets") or payload.get("data") or payload.get("slugs") or []
    else:
        items = []
    out = []
    for item in items:
        if isinstance(item, dict):
            ticker = str(item.get("ticker") or "").upper()
            slug = str(item.get("slug") or "")
            if ticker in ("BTC", "ETH", "SOL") or any(k in slug.lower() for k in ("btc", "eth", "-sol-")):
                out.append(slug)
        else:
            out.append(str(item))
    return out


def _position_id(market: dict[str, Any], index: int) -> str | None:
    ids = market.get("positionIds") or market.get("position_ids")
    if isinstance(ids, list) and len(ids) > index:
        return str(ids[index])
    outcomes = market.get("outcomes") or market.get("tokens") or []
    if isinstance(outcomes, list):
        target = "yes" if index == 0 else "no"
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            name = str(outcome.get("name") or outcome.get("outcome") or outcome.get("label") or "").lower()
            if name == target:
                token = outcome.get("positionId") or outcome.get("tokenId") or outcome.get("token_id") or outcome.get("id")
                return str(token) if token is not None else None
    return None


def _venue_exchange(market: dict[str, Any]) -> str | None:
    venue = market.get("venue")
    if isinstance(venue, dict):
        exchange = venue.get("exchange")
        return str(exchange) if exchange else None
    return str(market.get("exchange")) if market.get("exchange") else None


def _fee_rate_bps(schedule: FeeSchedule) -> int | None:
    if schedule.fee_rate_bps is not None:
        return int(schedule.fee_rate_bps)
    if schedule.fee_rate_decimal is not None:
        return int(schedule.fee_rate_decimal * Decimal("10000"))
    return None
