from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from src.oracles.compatibility import compare_oracles


Platform = Literal["polymarket", "limitless"]
Direction = Literal["above", "below", "updown", "unknown"]


@dataclass(frozen=True)
class NormalizedMarket:
    platform: Platform
    market_id: str
    title: str
    asset: str
    direction: Direction
    strike_usd: float | None
    expiry_utc: datetime | None
    duration_class: str | None
    oracle: str
    tie_rule: str | None
    payout_rule: str | None
    yes_token_id: str | None
    no_token_id: str | None
    tick_size: float | None
    delta_from_open: bool = False

    @property
    def rule_signature(self) -> tuple[Any, ...]:
        return (
            self.asset,
            self.direction,
            self.strike_usd,
            self.expiry_utc,
            self.duration_class,
            self.oracle.lower(),
            (self.tie_rule or "").lower(),
            (self.payout_rule or "").lower(),
            self.delta_from_open,
        )


@dataclass(frozen=True)
class MarketPair:
    polymarket: NormalizedMarket
    limitless: NormalizedMarket
    hard_arb: bool
    haircut_bps: int
    reason: str


def normalize_market(platform: Platform, market: dict[str, Any]) -> NormalizedMarket:
    title = str(market.get("title") or market.get("question") or market.get("name") or market.get("slug") or "")
    outcomes = market.get("outcomes") or market.get("tokens") or []
    yes_token, no_token = _extract_yes_no_tokens(market, outcomes)
    return NormalizedMarket(
        platform=platform,
        market_id=str(market.get("conditionId") or market.get("id") or market.get("address") or market.get("slug")),
        title=title,
        asset=_extract_asset(title, market),
        direction=_extract_direction(title),
        strike_usd=_extract_strike(title, market),
        expiry_utc=_parse_datetime(
            market.get("deadline")
            or market.get("endDate")
            or market.get("end_date")
            or market.get("resolutionTime")
            or market.get("expiry")
        ),
        duration_class=_duration_class(market, title),
        oracle=str(market.get("oracle") or market.get("resolutionSource") or market.get("resolution_source") or ""),
        tie_rule=str(market.get("tieRule") or market.get("tie_rule") or ""),
        payout_rule=str(market.get("payoutRule") or market.get("payout_rule") or ""),
        yes_token_id=yes_token,
        no_token_id=no_token,
        tick_size=_to_float(market.get("tickSize") or market.get("tick_size") or market.get("minimum_tick_size")),
        delta_from_open=_is_delta_from_open(title, market),
    )


def find_pairs(
    polymarket_markets: list[NormalizedMarket],
    limitless_markets: list[NormalizedMarket],
    *,
    strike_tolerance_pct: float = 0.005,
    mismatch_haircut_bps: int = 50,
) -> list[MarketPair]:
    pairs: list[MarketPair] = []
    for poly in polymarket_markets:
        for lim in limitless_markets:
            pair = evaluate_pair(
                poly,
                lim,
                strike_tolerance_pct=strike_tolerance_pct,
                mismatch_haircut_bps=mismatch_haircut_bps,
            )
            if pair is not None:
                pairs.append(pair)
    return pairs


def evaluate_pair(
    poly: NormalizedMarket,
    lim: NormalizedMarket,
    *,
    strike_tolerance_pct: float = 0.005,
    mismatch_haircut_bps: int = 50,
) -> MarketPair | None:
    if poly.asset != lim.asset or poly.direction != lim.direction:
        return None
    if poly.delta_from_open != lim.delta_from_open:
        return None
    if poly.strike_usd is not None and lim.strike_usd is not None:
        denom = max(poly.strike_usd, 1.0)
        if abs(poly.strike_usd - lim.strike_usd) / denom > strike_tolerance_pct:
            return None
    elif poly.strike_usd != lim.strike_usd:
        return None
    if poly.expiry_utc and lim.expiry_utc:
        tolerance = 300 if (poly.duration_class in {"5m", "15m", "30m"} or lim.duration_class in {"5m", "15m", "30m"}) else 3600
        if abs((poly.expiry_utc - lim.expiry_utc).total_seconds()) > tolerance:
            return None
    compatibility = compare_oracles(poly.oracle, lim.oracle, mismatch_haircut_bps=mismatch_haircut_bps)
    if not compatibility.relative_value_compatible:
        return None
    hard_arb = compatibility.hard_arb_compatible and poly.rule_signature == lim.rule_signature
    reason = "strict rule parity" if hard_arb else compatibility.reason
    return MarketPair(poly, lim, hard_arb=hard_arb, haircut_bps=compatibility.haircut_bps, reason=reason)


def _extract_asset(title: str, market: dict[str, Any]) -> str:
    explicit = market.get("ticker") or market.get("asset") or market.get("underlying")
    if explicit:
        return str(explicit).upper().replace("USD", "").replace("/", "")
    match = re.search(r"\b(BTC|ETH|SOL|XRP|DOGE|USDC)\b", title, re.IGNORECASE)
    return match.group(1).upper() if match else "UNKNOWN"


def _extract_direction(title: str) -> Direction:
    text = title.lower()
    if "up or down" in text or " up/down" in text:
        return "updown"
    if any(word in text for word in ("above", "over", "greater than", ">")):
        return "above"
    if any(word in text for word in ("below", "under", "less than", "<")):
        return "below"
    return "unknown"


def _extract_strike(title: str, market: dict[str, Any]) -> float | None:
    for key in ("strike", "strikePrice", "strike_price", "targetPrice"):
        value = _to_float(market.get(key))
        if value is not None:
            return value
    match = re.search(r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})+(?:\.\d+)?)", title)
    if match:
        return float(match.group(1).replace(",", ""))
    match = re.search(r"\$?\s*([0-9]+(?:\.\d+)?)", title)
    if match and any(word in title.lower() for word in ("above", "below", "over", "under")):
        return float(match.group(1))
    return None


def _duration_class(market: dict[str, Any], title: str) -> str | None:
    explicit = market.get("duration") or market.get("durationClass") or market.get("timeframe")
    if explicit:
        return str(explicit).lower()
    text = title.lower()
    for value in ("5m", "15m", "30m", "1h", "daily", "weekly"):
        if value in text:
            return value
    if "hour" in text:
        return "1h"
    if "day" in text:
        return "daily"
    return None


def _is_delta_from_open(title: str, market: dict[str, Any]) -> bool:
    text = title.lower()
    return bool(market.get("deltaFromOpen") or "up or down" in text or "price to beat" in text)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_yes_no_tokens(market: dict[str, Any], outcomes: Any) -> tuple[str | None, str | None]:
    yes = market.get("yes_token_id") or market.get("yesTokenId")
    no = market.get("no_token_id") or market.get("noTokenId")
    if yes and no:
        return str(yes), str(no)
    if isinstance(outcomes, list):
        for index, outcome in enumerate(outcomes):
            if not isinstance(outcome, dict):
                continue
            name = str(outcome.get("outcome") or outcome.get("name") or outcome.get("label") or index).lower()
            token = outcome.get("token_id") or outcome.get("tokenId") or outcome.get("id") or outcome.get("positionId")
            if token is None:
                continue
            if name == "yes":
                yes = token
            elif name == "no":
                no = token
    position_ids = market.get("positionIds")
    if isinstance(position_ids, list) and len(position_ids) >= 2:
        yes = yes or position_ids[0]
        no = no or position_ids[1]
    return str(yes) if yes else None, str(no) if no else None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None

