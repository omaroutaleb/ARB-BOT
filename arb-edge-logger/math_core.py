"""Core math for edge measurement. Kept in one file with NO external dependencies
so it's testable and obviously-correct.

The single most important property: every function here is pure (no I/O, no globals).
The self-test in selftest.py exercises every branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------- Book walking ----------

@dataclass(slots=True)
class BookLevel:
    """One level in an orderbook. `price` is the per-share price; `size` is shares available."""
    price: float
    size: float


@dataclass(slots=True)
class WalkResult:
    """Result of walking the book for a target USD notional.

    `filled_usd` may be < target_usd if depth was insufficient. `avg_price` is None
    in that case (we don't pretend we could fill).
    """
    target_usd: float
    filled_usd: float
    filled_shares: float
    avg_price: float | None
    depth_exhausted: bool       # True if we ran out of book before filling


def walk_asks_for_usd_buy(asks: list[BookLevel], target_usd: float) -> WalkResult:
    """Walk asks (ascending price) to fill `target_usd` of buying. Returns realistic
    weighted average price. If book depth < target_usd, returns partial fill and
    flags depth_exhausted=True. Caller decides whether to count this observation.

    Asks MUST be pre-sorted ascending by price. We assert this.
    """
    if target_usd <= 0:
        return WalkResult(target_usd=target_usd, filled_usd=0.0, filled_shares=0.0,
                          avg_price=None, depth_exhausted=False)
    if not asks:
        return WalkResult(target_usd=target_usd, filled_usd=0.0, filled_shares=0.0,
                          avg_price=None, depth_exhausted=True)
    # Defensive: confirm ascending order.
    for i in range(1, len(asks)):
        if asks[i].price < asks[i - 1].price:
            raise ValueError("asks must be sorted ascending by price")

    remaining_usd = target_usd
    filled_shares = 0.0
    spent_usd = 0.0
    for level in asks:
        if level.price <= 0 or level.size <= 0:
            continue        # malformed level; skip
        level_capacity_usd = level.price * level.size
        if level_capacity_usd >= remaining_usd:
            # Partial fill within this level
            shares_taken = remaining_usd / level.price
            filled_shares += shares_taken
            spent_usd += remaining_usd
            remaining_usd = 0.0
            break
        # Take the whole level
        filled_shares += level.size
        spent_usd += level_capacity_usd
        remaining_usd -= level_capacity_usd

    if filled_shares <= 0:
        return WalkResult(target_usd=target_usd, filled_usd=0.0, filled_shares=0.0,
                          avg_price=None, depth_exhausted=True)

    return WalkResult(
        target_usd=target_usd,
        filled_usd=spent_usd,
        filled_shares=filled_shares,
        avg_price=spent_usd / filled_shares,
        depth_exhausted=(remaining_usd > 0),
    )


def walk_bids_for_usd_sell(bids: list[BookLevel], target_usd: float) -> WalkResult:
    """Walk bids (descending price) to sell `target_usd` worth at the OBSERVED bids.
    Bids MUST be pre-sorted descending by price. Same return semantics as walk_asks.
    """
    if target_usd <= 0:
        return WalkResult(target_usd=target_usd, filled_usd=0.0, filled_shares=0.0,
                          avg_price=None, depth_exhausted=False)
    if not bids:
        return WalkResult(target_usd=target_usd, filled_usd=0.0, filled_shares=0.0,
                          avg_price=None, depth_exhausted=True)
    for i in range(1, len(bids)):
        if bids[i].price > bids[i - 1].price:
            raise ValueError("bids must be sorted descending by price")

    remaining_usd = target_usd
    sold_shares = 0.0
    received_usd = 0.0
    for level in bids:
        if level.price <= 0 or level.size <= 0:
            continue
        level_capacity_usd = level.price * level.size
        if level_capacity_usd >= remaining_usd:
            shares_taken = remaining_usd / level.price
            sold_shares += shares_taken
            received_usd += remaining_usd
            remaining_usd = 0.0
            break
        sold_shares += level.size
        received_usd += level_capacity_usd
        remaining_usd -= level_capacity_usd

    if sold_shares <= 0:
        return WalkResult(target_usd=target_usd, filled_usd=0.0, filled_shares=0.0,
                          avg_price=None, depth_exhausted=True)

    return WalkResult(
        target_usd=target_usd,
        filled_usd=received_usd,
        filled_shares=sold_shares,
        avg_price=received_usd / sold_shares,
        depth_exhausted=(remaining_usd > 0),
    )


# ---------- Fees ----------
# Conservative — when a runtime fee isn't available we use upper-bound fallbacks.
# Hard-coded values are the LAST resort; logger pulls live metadata when possible.

LIMITLESS_BUY_PEAK_PCT = 3.00       # at p=0.50
LIMITLESS_BUY_FLOOR_PCT = 0.40      # at p=0 or p=1
LIMITLESS_SELL_PEAK_PCT = 1.50
LIMITLESS_SELL_FLOOR_PCT = 0.42

POLYMARKET_CRYPTO_RATE_FALLBACK = 0.072
POLYMARKET_CRYPTO_EXPONENT_FALLBACK = 1.0


def limitless_taker_fee_usd(
    *,
    notional_usd: float,
    price: float,
    is_buy: bool,
    market_meta_fee_bps: int | None = None,
) -> tuple[float, str]:
    """Returns (fee_usd, source_note). source_note is for logging — we ALWAYS know
    where the fee number came from."""
    if market_meta_fee_bps is not None and market_meta_fee_bps >= 0:
        rate = market_meta_fee_bps / 10_000.0
        return notional_usd * rate, f"limitless_market_meta_bps={market_meta_fee_bps}"

    # Fall back to documented curve, triangle interpolation peaking at p=0.50.
    if is_buy:
        floor, peak = LIMITLESS_BUY_FLOOR_PCT, LIMITLESS_BUY_PEAK_PCT
    else:
        floor, peak = LIMITLESS_SELL_FLOOR_PCT, LIMITLESS_SELL_PEAK_PCT
    distance_from_mid = abs(price - 0.5) * 2.0      # 0 at p=0.5, 1 at edges
    pct = peak - (peak - floor) * distance_from_mid
    rate = pct / 100.0
    return notional_usd * rate, f"limitless_curve_fallback_{'buy' if is_buy else 'sell'}_pct={pct:.3f}"


def polymarket_taker_fee_usd(
    *,
    notional_usd: float,
    price: float,
    market_meta: dict | None = None,
) -> tuple[float, str]:
    """Polymarket formula: fee = C * p * rate * (p*(1-p))^exponent.

    Pulls rate/exponent from market metadata if available, else falls back to
    crypto-category defaults (which are conservative-higher).
    """
    if not (0 < price < 1):
        return 0.0, "polymarket_zero_price"

    rate = POLYMARKET_CRYPTO_RATE_FALLBACK
    exponent = POLYMARKET_CRYPTO_EXPONENT_FALLBACK
    source = "polymarket_crypto_fallback"

    if market_meta:
        fd = market_meta.get("fd") or {}
        if "rate" in fd:
            rate = float(fd["rate"])
            source = f"polymarket_market_fd.rate={rate}"
        elif "feeRate" in market_meta:
            rate = float(market_meta["feeRate"])
            source = f"polymarket_market_feeRate={rate}"
        if "exponent" in fd:
            exponent = float(fd["exponent"])
        elif "feeExponent" in market_meta:
            exponent = float(market_meta["feeExponent"])

    base = price * (1.0 - price)
    fee = notional_usd * price * rate * (base ** exponent)
    return fee, source


# ---------- Single-venue YES+NO complementarity ----------

@dataclass(slots=True)
class YesNoObservation:
    """One observation of YES+NO complementarity on a single venue at one size."""
    # Inputs
    venue: str
    market_key: str
    size_usd: float
    # Walked-book results
    yes_walk: WalkResult
    no_walk: WalkResult
    # Cost layers (each labeled)
    naive_sum_top_asks: float | None           # top-of-book only — what fake-edge bots see
    realistic_sum_avg_asks: float | None       # depth-walked average — closer to truth
    fees_yes_usd: float
    fees_no_usd: float
    fees_source: str
    # The bottom line
    gross_edge_usd: float | None               # (1 * shares) - cost, BEFORE fees
    net_edge_usd: float | None                 # AFTER fees
    edge_per_share_usd: float | None           # net_edge / shares (useful per-size comparison)
    # Reliability
    depth_ok: bool                             # True if both sides filled requested size
    observation_ts_ns: int
    response_age_ms: float | None              # how stale the underlying book snapshot is (if known)


def evaluate_yes_no_complementarity(
    *,
    venue: str,
    market_key: str,
    yes_asks: list[BookLevel],
    no_asks: list[BookLevel],
    size_usd: float,
    yes_top_ask: float | None = None,
    no_top_ask: float | None = None,
    observation_ts_ns: int = 0,
    response_age_ms: float | None = None,
    limitless_fee_bps_meta: int | None = None,
    polymarket_meta: dict | None = None,
) -> YesNoObservation:
    """Compute one YES+NO complementarity observation.

    `size_usd` is the TOTAL ROUND-TRIP CAPITAL you're willing to deploy on this
    pair. We split it 50/50 across YES and NO. If the books are deep, actual
    cost ~= size_usd. If one side is thin, actual cost < size_usd and
    depth_ok=False (you got fewer shares than requested).

    Edge: (1 * shares) - (yes_cost + no_cost) - fees.
    Both naive (top-of-book) and realistic (walked) sums are recorded so analysis
    can later quantify how much edge is illusion.
    """
    per_leg_usd = size_usd / 2.0
    yes_walk = walk_asks_for_usd_buy(yes_asks, per_leg_usd)
    no_walk = walk_asks_for_usd_buy(no_asks, per_leg_usd)

    naive_sum = None
    if yes_top_ask is not None and no_top_ask is not None:
        naive_sum = float(yes_top_ask) + float(no_top_ask)

    if yes_walk.avg_price is None or no_walk.avg_price is None:
        return YesNoObservation(
            venue=venue, market_key=market_key, size_usd=size_usd,
            yes_walk=yes_walk, no_walk=no_walk,
            naive_sum_top_asks=naive_sum,
            realistic_sum_avg_asks=None,
            fees_yes_usd=0.0, fees_no_usd=0.0, fees_source="depth_insufficient",
            gross_edge_usd=None, net_edge_usd=None, edge_per_share_usd=None,
            depth_ok=False,
            observation_ts_ns=observation_ts_ns,
            response_age_ms=response_age_ms,
        )

    realistic_sum = yes_walk.avg_price + no_walk.avg_price

    # Use the BALANCED share count — we must buy equal YES and NO.
    shares = min(yes_walk.filled_shares, no_walk.filled_shares)
    cost_usd = shares * realistic_sum
    gross = shares * 1.0 - cost_usd       # $1 per share-pair at resolution

    # Fees on the smaller-shares notional for each leg (balanced).
    notional_yes = shares * yes_walk.avg_price
    notional_no = shares * no_walk.avg_price

    if venue == "limitless":
        fee_yes, src_yes = limitless_taker_fee_usd(
            notional_usd=notional_yes, price=yes_walk.avg_price, is_buy=True,
            market_meta_fee_bps=limitless_fee_bps_meta,
        )
        fee_no, src_no = limitless_taker_fee_usd(
            notional_usd=notional_no, price=no_walk.avg_price, is_buy=True,
            market_meta_fee_bps=limitless_fee_bps_meta,
        )
        fees_source = f"{src_yes}|{src_no}"
    elif venue == "polymarket":
        fee_yes, src_yes = polymarket_taker_fee_usd(
            notional_usd=notional_yes, price=yes_walk.avg_price, market_meta=polymarket_meta,
        )
        fee_no, src_no = polymarket_taker_fee_usd(
            notional_usd=notional_no, price=no_walk.avg_price, market_meta=polymarket_meta,
        )
        fees_source = f"{src_yes}|{src_no}"
    else:
        fee_yes = fee_no = 0.0
        fees_source = "unknown_venue_no_fees"

    net = gross - fee_yes - fee_no
    edge_per_share = (net / shares) if shares > 0 else None

    depth_ok = not (yes_walk.depth_exhausted or no_walk.depth_exhausted)

    return YesNoObservation(
        venue=venue, market_key=market_key, size_usd=size_usd,
        yes_walk=yes_walk, no_walk=no_walk,
        naive_sum_top_asks=naive_sum,
        realistic_sum_avg_asks=realistic_sum,
        fees_yes_usd=fee_yes, fees_no_usd=fee_no, fees_source=fees_source,
        gross_edge_usd=gross, net_edge_usd=net, edge_per_share_usd=edge_per_share,
        depth_ok=depth_ok,
        observation_ts_ns=observation_ts_ns,
        response_age_ms=response_age_ms,
    )


# ---------- Cross-venue ----------

@dataclass(slots=True)
class CrossVenueObservation:
    """One observation of cross-venue arb at one size.

    Strategy: buy the CHEAPER side of YES on venue A, buy NO on venue B such that
    A.YES + B.NO < 1.00. Both pay $1 at resolution if oracles agree.
    """
    pair_key: str
    venue_a: str
    venue_b: str
    size_usd: float
    a_yes_walk: WalkResult
    b_no_walk: WalkResult
    naive_a_yes_top: float | None
    naive_b_no_top: float | None
    naive_sum: float | None
    realistic_sum: float | None
    fees_a_usd: float
    fees_b_usd: float
    fees_source: str
    oracle_haircut_usd: float           # 0.5% (configurable) of notional, subtracted defensively
    gross_edge_usd: float | None
    net_edge_usd: float | None
    edge_per_share_usd: float | None
    depth_ok: bool
    a_response_ts_ns: int
    b_response_ts_ns: int
    skew_ms: float                      # |a_ts - b_ts| in ms — KEY observability metric
    skew_unreliable: bool               # True if skew > threshold
    observation_ts_ns: int


SKEW_RELIABLE_THRESHOLD_MS = 100.0      # if skew exceeds this, mark observation unreliable
ORACLE_HAIRCUT_PCT_DEFAULT = 0.5        # 0.5% conservative haircut on cross-venue


def evaluate_cross_venue(
    *,
    pair_key: str,
    venue_a: str,
    venue_b: str,
    a_yes_asks: list[BookLevel],
    b_no_asks: list[BookLevel],
    size_usd: float,
    a_yes_top_ask: float | None = None,
    b_no_top_ask: float | None = None,
    a_response_ts_ns: int,
    b_response_ts_ns: int,
    a_limitless_fee_bps: int | None = None,
    b_limitless_fee_bps: int | None = None,
    a_polymarket_meta: dict | None = None,
    b_polymarket_meta: dict | None = None,
    oracle_haircut_pct: float = ORACLE_HAIRCUT_PCT_DEFAULT,
    observation_ts_ns: int = 0,
) -> CrossVenueObservation:
    """Compute cross-venue edge at one size.

    Direction is buy YES on A, buy NO on B. Caller decides which direction is
    cheaper (usually by checking top-of-book first, then asking us to evaluate
    that direction at depth). Both sides must succeed; if either book is too thin,
    depth_ok=False.
    """
    skew_ms = abs(a_response_ts_ns - b_response_ts_ns) / 1_000_000.0
    skew_unreliable = skew_ms > SKEW_RELIABLE_THRESHOLD_MS

    per_leg_usd = size_usd / 2.0
    a_walk = walk_asks_for_usd_buy(a_yes_asks, per_leg_usd)
    b_walk = walk_asks_for_usd_buy(b_no_asks, per_leg_usd)

    naive_sum = None
    if a_yes_top_ask is not None and b_no_top_ask is not None:
        naive_sum = float(a_yes_top_ask) + float(b_no_top_ask)

    if a_walk.avg_price is None or b_walk.avg_price is None:
        return CrossVenueObservation(
            pair_key=pair_key, venue_a=venue_a, venue_b=venue_b, size_usd=size_usd,
            a_yes_walk=a_walk, b_no_walk=b_walk,
            naive_a_yes_top=a_yes_top_ask, naive_b_no_top=b_no_top_ask, naive_sum=naive_sum,
            realistic_sum=None,
            fees_a_usd=0.0, fees_b_usd=0.0, fees_source="depth_insufficient",
            oracle_haircut_usd=0.0,
            gross_edge_usd=None, net_edge_usd=None, edge_per_share_usd=None,
            depth_ok=False,
            a_response_ts_ns=a_response_ts_ns, b_response_ts_ns=b_response_ts_ns,
            skew_ms=skew_ms, skew_unreliable=skew_unreliable,
            observation_ts_ns=observation_ts_ns,
        )

    realistic_sum = a_walk.avg_price + b_walk.avg_price
    shares = min(a_walk.filled_shares, b_walk.filled_shares)
    cost = shares * realistic_sum
    gross = shares * 1.0 - cost

    notional_a = shares * a_walk.avg_price
    notional_b = shares * b_walk.avg_price

    def _fee(venue: str, notional: float, price: float, lim_bps: int | None, poly_meta: dict | None):
        if venue == "limitless":
            return limitless_taker_fee_usd(
                notional_usd=notional, price=price, is_buy=True, market_meta_fee_bps=lim_bps,
            )
        if venue == "polymarket":
            return polymarket_taker_fee_usd(
                notional_usd=notional, price=price, market_meta=poly_meta,
            )
        return 0.0, f"unknown_venue_{venue}"

    fee_a, src_a = _fee(venue_a, notional_a, a_walk.avg_price, a_limitless_fee_bps, a_polymarket_meta)
    fee_b, src_b = _fee(venue_b, notional_b, b_walk.avg_price, b_limitless_fee_bps, b_polymarket_meta)
    fees_source = f"{venue_a}:{src_a}|{venue_b}:{src_b}"

    # Oracle mismatch haircut: defensively reduce gross by a % of notional.
    # 0.5% by default — Chainlink vs Pyth median price divergence on same asset.
    haircut = (oracle_haircut_pct / 100.0) * cost

    net = gross - fee_a - fee_b - haircut
    edge_per_share = (net / shares) if shares > 0 else None
    depth_ok = not (a_walk.depth_exhausted or b_walk.depth_exhausted)

    return CrossVenueObservation(
        pair_key=pair_key, venue_a=venue_a, venue_b=venue_b, size_usd=size_usd,
        a_yes_walk=a_walk, b_no_walk=b_walk,
        naive_a_yes_top=a_yes_top_ask, naive_b_no_top=b_no_top_ask, naive_sum=naive_sum,
        realistic_sum=realistic_sum,
        fees_a_usd=fee_a, fees_b_usd=fee_b, fees_source=fees_source,
        oracle_haircut_usd=haircut,
        gross_edge_usd=gross, net_edge_usd=net, edge_per_share_usd=edge_per_share,
        depth_ok=depth_ok,
        a_response_ts_ns=a_response_ts_ns, b_response_ts_ns=b_response_ts_ns,
        skew_ms=skew_ms, skew_unreliable=skew_unreliable,
        observation_ts_ns=observation_ts_ns,
    )
