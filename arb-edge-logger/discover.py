"""Discover all active markets on Limitless and Polymarket, classify them,
and emit two lists to pairs.json:

  - limitless_yes_no_candidates: every Limitless market with a YES+NO orderbook
    (these are single-venue arbitrage candidates — buy 1 YES + 1 NO < $1.00).
  - cross_venue_pairs: every (Limitless, Polymarket) pair that match on
    asset / direction / strike (within 1%) / deadline (within 5min) / oracle compatibility.

Run hourly. The logger reloads pairs.json on the next cycle.

Honest about limits:
  - Asset detection uses keyword matching on titles + slugs (BTC, ETH, DOGE, SOL, XRP, etc.).
  - Strike/direction extraction uses regex on natural-language titles. Some markets
    won't parse. We log how many were skipped and why.
  - Cross-venue matching is conservative: we'd rather miss a real pair than
    include a fake one.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# Hosts (verify against current docs; safe to override via env if they change)
LIMITLESS_BASE = "https://api.limitless.exchange"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"

# Coins we recognize. Easy to extend.
KNOWN_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "MATIC", "LINK", "DOT", "ATOM", "LTC", "BNB", "TRX", "SHIB", "PEPE")

# Cross-venue match tolerances. Conservative defaults: if you see zero matches, loosen.
STRIKE_TOLERANCE_PCT = 1.0          # within 1% of strike
DEADLINE_TOLERANCE_SEC = 5 * 60     # within 5 minutes

OUTPUT_PATH = Path(__file__).parent / "data" / "pairs.json"


@dataclass(slots=True)
class NormalizedMarket:
    venue: str                          # "limitless" | "polymarket"
    asset: str                          # "BTC" | "ETH" | ... | "?"
    direction: str                      # "above" | "below" | "updown" | "unknown"
    strike_usd: float | None
    deadline_utc: str | None            # ISO 8601 string
    duration_class: str                 # "5m" | "15m" | "30m" | "1h" | "4h" | "1d" | "unknown"
    oracle_hint: str                    # "chainlink-BTC" | "pyth-BTC" | "binance" | "uma" | "manual" | "unknown"
    slug: str
    raw_title: str
    yes_token_id: str | None
    no_token_id: str | None
    is_amm: bool                        # Limitless AMM markets don't have orderbooks
    has_orderbook: bool


@dataclass(slots=True)
class CrossVenuePair:
    pair_key: str                       # stable identifier
    asset: str
    direction: str
    strike_usd: float
    deadline_utc: str
    duration_class: str
    limitless: NormalizedMarket
    polymarket: NormalizedMarket


# ---------- HTTP helpers ----------

async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs) -> dict | list | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), **kwargs) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception as exc:
        print(f"  [warn] GET {url} failed: {exc}")
        return None


# ---------- Asset / direction / strike extraction ----------

_ASSET_WORD = {a: re.compile(rf"(?<![A-Za-z]){a}(?![A-Za-z])", re.IGNORECASE) for a in KNOWN_ASSETS}
_ASSET_SPELLED = [
    (re.compile(r"\bbitcoin\b", re.I), "BTC"),
    (re.compile(r"\bethereum\b", re.I), "ETH"),
    (re.compile(r"\bether\b(?!eum)", re.I), "ETH"),
    (re.compile(r"\bsolana\b", re.I), "SOL"),
    (re.compile(r"\bripple\b", re.I), "XRP"),
    (re.compile(r"\bdogecoin\b", re.I), "DOGE"),
    (re.compile(r"\bcardano\b", re.I), "ADA"),
    (re.compile(r"\bavalanche\b", re.I), "AVAX"),
    (re.compile(r"\bpolygon\b(?!\W*lab)", re.I), "MATIC"),
    (re.compile(r"\bchainlink\b", re.I), "LINK"),
]


def extract_asset(text: str) -> str:
    """Require word boundary so 'ADA' doesn't match 'CanADA' and 'ETH' doesn't
    match 'MegaETH'. Fully-spelled names take precedence (more specific)."""
    for pattern, sym in _ASSET_SPELLED:
        if pattern.search(text):
            return sym
    for a, pattern in _ASSET_WORD.items():
        if pattern.search(text):
            return a
    return "?"


def extract_direction(text: str) -> str:
    t = text.lower()
    if any(p in t for p in (" up or down", "updown", "up/down", " u/d ")):
        return "updown"
    if any(p in t for p in (" above ", ">=", "≥", "greater than", "over $", "at least", "exceed")):
        return "above"
    if any(p in t for p in (" below ", "<=", "≤", "less than", "under $", "fall below")):
        return "below"
    return "unknown"


_NUM_DOLLAR = re.compile(r"\$\s*([0-9][0-9,]*\.?\d*)")
_NUM_K = re.compile(r"\b(\d+(?:\.\d+)?)\s*[kK]\b")
_NUM_BARE = re.compile(r"\b(\d{3,7}(?:\.\d+)?)\b")


def extract_strike(text: str) -> float | None:
    m = _NUM_DOLLAR.search(text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = _NUM_K.search(text)
    if m:
        try:
            return float(m.group(1)) * 1000.0
        except ValueError:
            pass
    m = _NUM_BARE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def classify_duration(deadline_iso: str | None, start_iso: str | None, title: str) -> str:
    """Best-effort duration class. Prefers explicit deadline-start delta; falls
    back to title keywords."""
    if deadline_iso and start_iso:
        try:
            d = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
            s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            sec = (d - s).total_seconds()
            if sec <= 5 * 60 + 30:
                return "5m"
            if sec <= 15 * 60 + 30:
                return "15m"
            if sec <= 30 * 60 + 60:
                return "30m"
            if sec <= 60 * 60 + 120:
                return "1h"
            if sec <= 4 * 60 * 60 + 240:
                return "4h"
            if sec <= 24 * 60 * 60 + 3600:
                return "1d"
            return "weekly"
        except ValueError:
            pass
    t = title.lower()
    if "5m" in t or "5 minute" in t:
        return "5m"
    if "15m" in t or "15 minute" in t:
        return "15m"
    if "30m" in t or "30 minute" in t:
        return "30m"
    if "1h" in t or "hourly" in t:
        return "1h"
    if "4h" in t:
        return "4h"
    if "daily" in t or "by midnight" in t or "today" in t:
        return "1d"
    return "unknown"


def classify_oracle(market_dict: dict, venue: str) -> str:
    """Best-effort oracle classification from market metadata."""
    fields = []
    for k in ("oracleType", "oracle", "resolutionSource", "resolution_source",
              "description", "title", "question"):
        v = market_dict.get(k)
        if v:
            fields.append(str(v))
    blob = " ".join(fields).lower()
    asset = extract_asset(blob).lower()

    if "chainlink" in blob:
        return f"chainlink-{asset}" if asset != "?" else "chainlink"
    if "pyth" in blob:
        return f"pyth-{asset}" if asset != "?" else "pyth"
    if "binance" in blob and ("candle" in blob or "1h" in blob or "klines" in blob or "usdt" in blob):
        return "binance"
    if venue == "polymarket":
        return "uma"            # Polymarket default
    if "manual" in blob:
        return "manual"
    return "unknown"


# ---------- Limitless fetch ----------

async def fetch_limitless_markets(session: aiohttp.ClientSession) -> list[NormalizedMarket]:
    print("[discover] fetching Limitless active markets...")
    data = await _get_json(session, f"{LIMITLESS_BASE}/markets/active")
    if data is None:
        print("  [error] could not fetch Limitless markets")
        return []
    raw_markets = data if isinstance(data, list) else (data.get("markets") or data.get("data") or [])
    print(f"  got {len(raw_markets)} raw entries")

    out: list[NormalizedMarket] = []
    skipped_no_asset = 0
    skipped_no_orderbook = 0
    for m in raw_markets:
        if not isinstance(m, dict):
            continue
        title = str(m.get("title") or m.get("question") or m.get("slug") or "")
        asset = extract_asset(title)
        if asset == "?":
            skipped_no_asset += 1
            continue
        direction = extract_direction(title)
        strike = m.get("strikePrice") or m.get("strike_usd") or m.get("strike")
        if strike is None:
            strike = extract_strike(title)
        try:
            strike = float(strike) if strike is not None else None
        except (ValueError, TypeError):
            strike = None

        deadline = (m.get("expirationDate") or m.get("deadline") or
                    m.get("expiresAt") or m.get("deadlineUtc"))
        start = m.get("startAt") or m.get("createdAt")
        duration = classify_duration(str(deadline) if deadline else None,
                                     str(start) if start else None, title)
        oracle = classify_oracle(m, "limitless")

        # Limitless real shape: tokens: {yes: "...", no: "..."} and tradeType: "clob"|"amm"
        tokens_obj = m.get("tokens") or {}
        yes_tok = tokens_obj.get("yes")
        no_tok = tokens_obj.get("no")
        yes_tok = str(yes_tok) if yes_tok else None
        no_tok = str(no_tok) if no_tok else None

        trade_type = str(m.get("tradeType") or "").lower()
        is_amm = trade_type == "amm"
        has_book = (trade_type == "clob") and yes_tok is not None and no_tok is not None
        if not has_book:
            skipped_no_orderbook += 1

        out.append(NormalizedMarket(
            venue="limitless",
            asset=asset, direction=direction,
            strike_usd=strike,
            deadline_utc=str(deadline) if deadline else None,
            duration_class=duration,
            oracle_hint=oracle,
            slug=str(m.get("slug") or m.get("address") or ""),
            raw_title=title,
            yes_token_id=yes_tok, no_token_id=no_tok,
            is_amm=is_amm,
            has_orderbook=has_book,
        ))

    print(f"  normalized: {len(out)}  (skipped {skipped_no_asset} no-asset, {skipped_no_orderbook} no-orderbook)")
    return out


# ---------- Polymarket fetch ----------

async def fetch_polymarket_markets(session: aiohttp.ClientSession) -> list[NormalizedMarket]:
    print("[discover] fetching Polymarket active markets...")
    # Limit to 500 to keep this loop fast. Reasonable for a discovery scan.
    data = await _get_json(session, f"{POLYMARKET_GAMMA}/markets",
                           params={"active": "true", "closed": "false", "limit": 500})
    if data is None:
        print("  [error] could not fetch Polymarket markets")
        return []
    raw_markets = data if isinstance(data, list) else (data.get("markets") or [])
    print(f"  got {len(raw_markets)} raw entries")

    out: list[NormalizedMarket] = []
    skipped_no_asset = 0
    for m in raw_markets:
        if not isinstance(m, dict):
            continue
        title = str(m.get("question") or m.get("title") or m.get("slug") or "")
        asset = extract_asset(title)
        if asset == "?":
            skipped_no_asset += 1
            continue
        direction = extract_direction(title)
        strike = extract_strike(title)
        deadline = m.get("end_date_iso") or m.get("endDateIso") or m.get("endDate")
        start = m.get("start_date_iso") or m.get("startDateIso") or m.get("startDate")
        duration = classify_duration(str(deadline) if deadline else None,
                                     str(start) if start else None, title)
        oracle = classify_oracle(m, "polymarket")

        tokens = m.get("clobTokenIds") or m.get("clob_token_ids") or []
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except json.JSONDecodeError:
                tokens = []
        yes_tok = str(tokens[0]) if len(tokens) >= 1 else None
        no_tok = str(tokens[1]) if len(tokens) >= 2 else None

        out.append(NormalizedMarket(
            venue="polymarket",
            asset=asset, direction=direction, strike_usd=strike,
            deadline_utc=str(deadline) if deadline else None,
            duration_class=duration,
            oracle_hint=oracle,
            slug=str(m.get("slug") or m.get("conditionId") or ""),
            raw_title=title,
            yes_token_id=yes_tok, no_token_id=no_tok,
            is_amm=False,
            has_orderbook=(yes_tok is not None and no_tok is not None),
        ))

    print(f"  normalized: {len(out)}  (skipped {skipped_no_asset} no-asset)")
    return out


# ---------- Pairing ----------

def _objective_oracle(o: str) -> bool:
    return o.startswith("chainlink-") or o.startswith("pyth-")


def find_cross_pairs(
    lim: list[NormalizedMarket], poly: list[NormalizedMarket]
) -> list[CrossVenuePair]:
    pairs: list[CrossVenuePair] = []
    matched_keys: set[str] = set()
    strike_tol = STRIKE_TOLERANCE_PCT / 100.0
    for l in lim:
        if l.direction == "updown" or l.direction == "unknown":
            continue
        if l.strike_usd is None or l.deadline_utc is None:
            continue
        try:
            l_deadline = datetime.fromisoformat(str(l.deadline_utc).replace("Z", "+00:00"))
        except ValueError:
            continue
        for p in poly:
            if p.direction != l.direction:
                continue
            if p.asset != l.asset:
                continue
            if p.strike_usd is None or p.deadline_utc is None:
                continue
            if abs(p.strike_usd - l.strike_usd) / l.strike_usd > strike_tol:
                continue
            try:
                p_deadline = datetime.fromisoformat(str(p.deadline_utc).replace("Z", "+00:00"))
            except ValueError:
                continue
            if abs((p_deadline - l_deadline).total_seconds()) > DEADLINE_TOLERANCE_SEC:
                continue
            # Oracle compat: both must be objective. Different assets within objective
            # set (chainlink-BTC vs pyth-BTC) is fine — same asset = compatible.
            if not (_objective_oracle(l.oracle_hint) and _objective_oracle(p.oracle_hint)):
                continue
            l_asset_in_oracle = l.oracle_hint.split("-", 1)[1] if "-" in l.oracle_hint else ""
            p_asset_in_oracle = p.oracle_hint.split("-", 1)[1] if "-" in p.oracle_hint else ""
            if l_asset_in_oracle.lower() != p_asset_in_oracle.lower():
                continue

            pair_key = f"{l.asset}|{l.direction}|{l.strike_usd:.6g}|{l.deadline_utc}|L:{l.slug}|P:{p.slug}"
            if pair_key in matched_keys:
                continue
            matched_keys.add(pair_key)
            pairs.append(CrossVenuePair(
                pair_key=pair_key,
                asset=l.asset, direction=l.direction,
                strike_usd=l.strike_usd, deadline_utc=l.deadline_utc,
                duration_class=l.duration_class,
                limitless=l, polymarket=p,
            ))
    return pairs


# ---------- Main ----------

async def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[discover] start {datetime.now(tz=timezone.utc).isoformat()}")
    t0 = time.time()
    async with aiohttp.ClientSession() as sess:
        lim, poly = await asyncio.gather(
            fetch_limitless_markets(sess),
            fetch_polymarket_markets(sess),
        )

    # Single-venue YES+NO candidates: must have orderbook AND be a clear price market
    # (either explicit strike with direction, or an up/down market). Filters out
    # accidentally-matched non-price markets (e.g. NHL "Avalanche" hockey team).
    def _is_price_market(m: NormalizedMarket) -> bool:
        if not m.has_orderbook:
            return False
        if m.direction == "updown":
            return True
        return m.direction in ("above", "below") and m.strike_usd is not None

    lim_candidates = [m for m in lim if _is_price_market(m)]
    poly_candidates = [m for m in poly if _is_price_market(m)]

    pairs = find_cross_pairs(lim, poly)

    payload = {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "discovery_duration_sec": round(time.time() - t0, 2),
        "limitless_yes_no_candidates": [asdict(m) for m in lim_candidates],
        "polymarket_yes_no_candidates": [asdict(m) for m in poly_candidates],
        "cross_venue_pairs": [
            {**{k: v for k, v in asdict(p).items() if k not in ("limitless", "polymarket")},
             "limitless": asdict(p.limitless), "polymarket": asdict(p.polymarket)}
            for p in pairs
        ],
        "counts": {
            "limitless_total_normalized": len(lim),
            "limitless_yes_no_candidates": len(lim_candidates),
            "polymarket_total_normalized": len(poly),
            "polymarket_yes_no_candidates": len(poly_candidates),
            "cross_venue_pairs": len(pairs),
        },
        "tolerances": {
            "strike_pct": STRIKE_TOLERANCE_PCT,
            "deadline_sec": DEADLINE_TOLERANCE_SEC,
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"[discover] done in {time.time() - t0:.1f}s")
    print(f"  Limitless YES+NO candidates: {len(lim_candidates)}")
    print(f"  Polymarket YES+NO candidates: {len(poly_candidates)}")
    print(f"  Cross-venue pairs: {len(pairs)}")
    print(f"  output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
