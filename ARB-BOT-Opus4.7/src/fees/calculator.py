"""Fee math for both venues. NEVER hard-codes rates — pulls from runtime data.

References:
  - STRATEGY_SYNTHESIS.md §1.6
  - Polymarket fee formula: `Fee = C × p × feeRate × (p × (1−p))^exponent`
  - Limitless: per-side dynamic curve from market.feeSchedule, capped 0.40-3.00% buy,
    0.42-1.50% sell. Maker = 0.

Decision (§2.1): Hard-coded rate constants are FALLBACK only. The bot calls
`calc_taker_fee(...)` with `market_meta` and we pull `feeRate`/`exponent`/
`feeRateBps` from there. Constants are only used if metadata is missing.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fallback constants — last resort if market metadata absent.
# Higher of the two reports (0.072) used per §2.1 — more conservative (over-estimates cost).
POLYMARKET_CRYPTO_FALLBACK_RATE = 0.072
POLYMARKET_FALLBACK_EXPONENT = 1.0
POLYMARKET_TAKER_REBATE_FRACTION_CRYPTO = 0.20  # 20% of taker fee returned to makers

# Limitless: research §1.6 — buy peak 3.00%, sell peak 1.50% at p=0.50.
# We model a triangle around p=0.50 if no metadata is available.
LIMITLESS_BUY_PEAK_PCT = 3.00
LIMITLESS_SELL_PEAK_PCT = 1.50
LIMITLESS_BUY_FLOOR_PCT = 0.40
LIMITLESS_SELL_FLOOR_PCT = 0.42


@dataclass(slots=True)
class FeeQuote:
    """Outcome of a fee calculation. All values in USD."""
    taker_fee_usd: float
    maker_rebate_usd: float
    effective_cost_pct: float   # fee / notional (decimal, e.g. 0.018 for 1.8%)
    notes: str = ""


def _polymarket_formula(notional_usd: float, price: float, rate: float, exponent: float) -> float:
    """`fee = C × p × rate × (p × (1−p))^exponent`. C = notional in USD."""
    if not (0 < price < 1):
        return 0.0
    base = price * (1.0 - price)
    return notional_usd * price * rate * (base ** exponent)


def polymarket_taker_fee(
    *,
    notional_usd: float,
    price: float,
    market_meta: dict | None = None,
) -> FeeQuote:
    """Per-market data is preferred. Acceptable keys looked up in `market_meta`:
        `fd.rate` or `feeRate`  — the rate constant
        `fd.exponent` or `feeExponent` — exponent
        `feeRateBps` — alternative direct flat-bps representation (used by some categories)
    Falls back to crypto constants if absent."""
    rate = POLYMARKET_CRYPTO_FALLBACK_RATE
    exponent = POLYMARKET_FALLBACK_EXPONENT
    notes_parts: list[str] = []

    if market_meta:
        fd = market_meta.get("fd") or {}
        if "rate" in fd:
            rate = float(fd["rate"])
            notes_parts.append(f"rate={rate} (per fd.rate)")
        elif "feeRate" in market_meta:
            rate = float(market_meta["feeRate"])
            notes_parts.append(f"rate={rate} (per feeRate)")
        if "exponent" in fd:
            exponent = float(fd["exponent"])
            notes_parts.append(f"exp={exponent} (per fd.exponent)")
        elif "feeExponent" in market_meta:
            exponent = float(market_meta["feeExponent"])
            notes_parts.append(f"exp={exponent} (per feeExponent)")

        fee_rate_bps = market_meta.get("feeRateBps")
        if fee_rate_bps is not None and not notes_parts:
            flat_rate = float(fee_rate_bps) / 10000.0
            fee_usd = notional_usd * flat_rate
            return FeeQuote(
                taker_fee_usd=fee_usd,
                maker_rebate_usd=fee_usd * POLYMARKET_TAKER_REBATE_FRACTION_CRYPTO,
                effective_cost_pct=flat_rate,
                notes=f"flat feeRateBps={fee_rate_bps}",
            )

    fee_usd = _polymarket_formula(notional_usd, price, rate, exponent)
    rebate = fee_usd * POLYMARKET_TAKER_REBATE_FRACTION_CRYPTO
    return FeeQuote(
        taker_fee_usd=fee_usd,
        maker_rebate_usd=rebate,
        effective_cost_pct=(fee_usd / notional_usd) if notional_usd > 0 else 0.0,
        notes="; ".join(notes_parts) or "fallback rate (no market metadata)",
    )


def limitless_taker_fee(
    *,
    notional_usd: float,
    price: float,
    is_buy: bool,
    market_meta: dict | None = None,
    profile_fee_rate_bps: int | None = None,
) -> FeeQuote:
    """Limitless fee model.

    Preference order (per Opus open question §J8):
      1. market_meta.feeSchedule  — per-market override
      2. profile_fee_rate_bps     — global per-account
      3. modeled triangle peaking at p=0.50 (research §1.6)
    """
    if market_meta:
        fee_sched = market_meta.get("feeSchedule") or {}
        bps_field = "buyBps" if is_buy else "sellBps"
        if bps_field in fee_sched:
            bps = float(fee_sched[bps_field])
            rate = bps / 10000.0
            return FeeQuote(
                taker_fee_usd=notional_usd * rate,
                maker_rebate_usd=0.0,
                effective_cost_pct=rate,
                notes=f"limitless feeSchedule.{bps_field}={bps}",
            )

    if profile_fee_rate_bps is not None:
        rate = float(profile_fee_rate_bps) / 10000.0
        return FeeQuote(
            taker_fee_usd=notional_usd * rate,
            maker_rebate_usd=0.0,
            effective_cost_pct=rate,
            notes=f"profile feeRateBps={profile_fee_rate_bps}",
        )

    # Fallback: triangle interpolation peaking at p=0.50.
    if is_buy:
        floor, peak = LIMITLESS_BUY_FLOOR_PCT, LIMITLESS_BUY_PEAK_PCT
    else:
        floor, peak = LIMITLESS_SELL_FLOOR_PCT, LIMITLESS_SELL_PEAK_PCT

    distance_from_mid = abs(price - 0.5) * 2.0     # 0 at p=0.5, 1 at p=0/1
    pct = peak - (peak - floor) * distance_from_mid
    rate = pct / 100.0
    return FeeQuote(
        taker_fee_usd=notional_usd * rate,
        maker_rebate_usd=0.0,
        effective_cost_pct=rate,
        notes="fallback triangle model (no market metadata)",
    )


def round_trip_taker_cost(
    *,
    notional_usd: float,
    polymarket_price: float,
    limitless_price: float,
    polymarket_meta: dict | None = None,
    limitless_meta: dict | None = None,
    limitless_profile_bps: int | None = None,
    limitless_side_is_buy: bool = True,
) -> float:
    """Total taker-taker round-trip cost for a cross-venue arb. Conservative estimate."""
    poly = polymarket_taker_fee(
        notional_usd=notional_usd,
        price=polymarket_price,
        market_meta=polymarket_meta,
    )
    lim = limitless_taker_fee(
        notional_usd=notional_usd,
        price=limitless_price,
        is_buy=limitless_side_is_buy,
        market_meta=limitless_meta,
        profile_fee_rate_bps=limitless_profile_bps,
    )
    return poly.taker_fee_usd + lim.taker_fee_usd
