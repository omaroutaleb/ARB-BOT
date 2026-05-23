"""Cross-platform market matching.

STRATEGY_SYNTHESIS.md §1.7, §1.12, §2.2:
  - Canonical rule signature: asset, direction, strike_usd, expiry_utc,
    duration_class, oracle, tick_size.
  - Match strike within tolerance (default 0.5%); expiry within tolerance.
  - Both oracles MUST be cross-venue compatible (objective price feeds).
  - Delta-vs-open markets (Polymarket 5m up/down) excluded from matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import get_settings
from src.observability.logging import get_logger
from src.oracles.compatibility import (
    OracleSource,
    classify_limitless_oracle,
    classify_polymarket_oracle,
    cross_venue_compatible,
)

log = get_logger(__name__)


class Direction:
    ABOVE = "above"
    BELOW = "below"
    UPDOWN = "updown"     # delta-vs-open — NOT cross-venue matchable
    UNKNOWN = "unknown"


class DurationClass:
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    DAILY = "daily"
    WEEKLY = "weekly"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class NormalizedMarket:
    platform: str                     # "polymarket" | "limitless"
    raw_id: str                       # slug or conditionId
    raw_title: str
    asset: str                        # "BTC" | "ETH" | ...
    direction: str                    # one of Direction.*
    strike_usd: float | None
    expiry_utc: datetime | None
    duration_class: str
    oracle: OracleSource
    yes_token_id: str | None
    no_token_id: str | None
    tick_size: float | None
    volume_24h_usd: float
    is_arbable: bool                  # False for delta-vs-open / unknown rule shape
    fee_meta: dict


def _utc(ts: str | int | float | datetime | None) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    try:
        # Try ISO 8601 with optional Z
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_asset(text: str) -> str:
    text_l = text.lower()
    if "btc" in text_l or "bitcoin" in text_l:
        return "BTC"
    if "eth" in text_l or "ether" in text_l:
        return "ETH"
    if "sol" in text_l:
        return "SOL"
    return "?"


def _extract_strike(text: str) -> float | None:
    """Pull a numeric strike like '$110,000' or '110k'. Returns None if absent."""
    m = re.search(r"\$\s*([0-9][0-9,]*\.?\d*)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*[kK]\b", text)
    if m:
        return float(m.group(1)) * 1000.0
    m = re.search(r"\b(\d{4,7}(?:\.\d+)?)\b", text)
    return float(m.group(1)) if m else None


def _extract_direction(text: str) -> str:
    t = text.lower()
    if " up or down" in t or "updown" in t or "u/d" in t:
        return Direction.UPDOWN
    if any(kw in t for kw in (" above ", " ≥", ">=", "over $", " greater than", " above$")):
        return Direction.ABOVE
    if any(kw in t for kw in (" below ", " ≤", "<=", "under $", " less than")):
        return Direction.BELOW
    return Direction.UNKNOWN


def _classify_duration(seconds: float | None, text: str = "") -> str:
    if seconds is not None:
        if seconds <= 5 * 60 + 30:
            return DurationClass.M5
        if seconds <= 15 * 60 + 30:
            return DurationClass.M15
        if seconds <= 30 * 60 + 60:
            return DurationClass.M30
        if seconds <= 60 * 60 + 120:
            return DurationClass.H1
        if seconds <= 4 * 60 * 60 + 240:
            return DurationClass.H4
        if seconds <= 24 * 60 * 60 + 3600:
            return DurationClass.DAILY
        if seconds <= 7 * 24 * 60 * 60 + 3600:
            return DurationClass.WEEKLY
    t = text.lower()
    if "5m" in t or "5 minute" in t:
        return DurationClass.M5
    if "15m" in t or "15 minute" in t:
        return DurationClass.M15
    if "1h" in t or "hourly" in t:
        return DurationClass.H1
    if "daily" in t or "today" in t or "by midnight" in t:
        return DurationClass.DAILY
    return DurationClass.UNKNOWN


def normalize_polymarket(market: dict) -> NormalizedMarket:
    title = str(market.get("question") or market.get("title") or market.get("slug") or "")
    end = _utc(market.get("end_date_iso") or market.get("endDateIso") or market.get("endDate"))
    start = _utc(market.get("start_date_iso") or market.get("startDateIso") or market.get("startDate"))

    direction = _extract_direction(title)
    strike = _extract_strike(title) if direction in (Direction.ABOVE, Direction.BELOW) else None

    duration_sec: float | None = None
    if start and end:
        duration_sec = (end - start).total_seconds()
    duration_class = _classify_duration(duration_sec, title)
    oracle = classify_polymarket_oracle(market)

    tokens = market.get("clobTokenIds") or market.get("clob_token_ids") or []
    if isinstance(tokens, str):
        try:
            import json
            tokens = json.loads(tokens)
        except Exception:
            tokens = []
    yes_tok = tokens[0] if len(tokens) >= 1 else None
    no_tok = tokens[1] if len(tokens) >= 2 else None

    is_arbable = direction in (Direction.ABOVE, Direction.BELOW) and strike is not None and oracle != OracleSource.UMA

    vol = float(market.get("volume24hr") or market.get("volume_24hr") or 0.0)

    fee_meta = {
        k: market[k] for k in ("fd", "feeRate", "feeRateBps", "feeExponent", "feeSchedule") if k in market
    }

    return NormalizedMarket(
        platform="polymarket",
        raw_id=str(market.get("conditionId") or market.get("condition_id") or market.get("slug") or ""),
        raw_title=title,
        asset=_extract_asset(title),
        direction=direction,
        strike_usd=strike,
        expiry_utc=end,
        duration_class=duration_class,
        oracle=oracle,
        yes_token_id=str(yes_tok) if yes_tok else None,
        no_token_id=str(no_tok) if no_tok else None,
        tick_size=float(market["minimum_tick_size"]) if market.get("minimum_tick_size") is not None else None,
        volume_24h_usd=vol,
        is_arbable=is_arbable,
        fee_meta=fee_meta,
    )


def normalize_limitless(market: dict) -> NormalizedMarket:
    title = str(market.get("title") or market.get("question") or market.get("slug") or "")
    end = _utc(market.get("deadline") or market.get("expiresAt") or market.get("deadlineUtc"))

    direction = _extract_direction(title)
    strike = market.get("strikePrice") or market.get("strike_usd") or market.get("strike")
    if strike is None:
        strike = _extract_strike(title)
    strike = float(strike) if strike is not None else None

    pos_ids = market.get("positionIds") or []
    yes_tok = str(pos_ids[0]) if len(pos_ids) >= 1 else None
    no_tok = str(pos_ids[1]) if len(pos_ids) >= 2 else None

    duration_class = _classify_duration(None, title)
    oracle = classify_limitless_oracle(market)
    vol = float(market.get("volume24h") or market.get("volume24hUsd") or market.get("liquidity") or 0.0)
    is_arbable = direction in (Direction.ABOVE, Direction.BELOW) and strike is not None and oracle != OracleSource.MANUAL

    return NormalizedMarket(
        platform="limitless",
        raw_id=str(market.get("slug") or market.get("address") or ""),
        raw_title=title,
        asset=_extract_asset(title),
        direction=direction,
        strike_usd=strike,
        expiry_utc=end,
        duration_class=duration_class,
        oracle=oracle,
        yes_token_id=yes_tok,
        no_token_id=no_tok,
        tick_size=0.01,
        volume_24h_usd=vol,
        is_arbable=is_arbable,
        fee_meta={k: market[k] for k in ("feeSchedule", "feeRateBps") if k in market},
    )


@dataclass(slots=True)
class MarketPair:
    poly: NormalizedMarket
    lim: NormalizedMarket
    score: float                  # higher = better match


def find_pairs(
    poly_markets: list[NormalizedMarket],
    lim_markets: list[NormalizedMarket],
) -> list[MarketPair]:
    settings = get_settings()
    strike_tol_pct = settings.PHASE3_STRIKE_TOLERANCE_PCT / 100.0
    expiry_tol_sec = settings.PHASE3_EXPIRY_TOLERANCE_SEC

    pairs: list[MarketPair] = []
    for p in poly_markets:
        if not p.is_arbable or p.direction == Direction.UPDOWN:
            continue
        for l in lim_markets:
            if not l.is_arbable or l.direction == Direction.UPDOWN:
                continue
            if p.asset != l.asset:
                continue
            if p.direction != l.direction:
                continue
            if p.strike_usd is None or l.strike_usd is None:
                continue
            if abs(p.strike_usd - l.strike_usd) / p.strike_usd > strike_tol_pct:
                continue
            if p.expiry_utc is None or l.expiry_utc is None:
                continue
            if abs((p.expiry_utc - l.expiry_utc).total_seconds()) > expiry_tol_sec:
                continue
            if not cross_venue_compatible(p.oracle, l.oracle):
                continue

            strike_dev = abs(p.strike_usd - l.strike_usd) / p.strike_usd
            expiry_dev = abs((p.expiry_utc - l.expiry_utc).total_seconds()) / max(expiry_tol_sec, 1)
            score = 1.0 - (strike_dev / max(strike_tol_pct, 1e-9)) - expiry_dev
            pairs.append(MarketPair(poly=p, lim=l, score=score))

    pairs.sort(key=lambda x: x.score, reverse=True)
    return pairs
