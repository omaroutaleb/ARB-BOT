"""Oracle compatibility for cross-venue arbitrage.

STRATEGY_SYNTHESIS.md §1.7 + §2.3.
Two markets are cross-venue arbitrageable only if BOTH resolve from
"objective price feeds." UMA Optimistic Oracle is subjective (dispute layer)
and is therefore NEVER paired across venues for arb purposes.

The accepted set:
  - Chainlink-BTC/USD-stream
  - Chainlink-ETH/USD-stream
  - Pyth-Crypto.BTC/USD
  - Pyth-Crypto.ETH/USD

Even within the accepted set, apply a 0.5% haircut (PHASE3_ORACLE_HAIRCUT_PCT)
because Chainlink and Pyth aggregate prices differently. Two markets with
identical strikes can still settle on opposite sides if the underlying
oracle median diverges within tolerance.
"""

from __future__ import annotations

from enum import Enum


class OracleSource(str, Enum):
    UMA = "UMA"
    CHAINLINK_BTC_USD = "Chainlink-BTC/USD-stream"
    CHAINLINK_ETH_USD = "Chainlink-ETH/USD-stream"
    PYTH_BTC_USD = "Pyth-Crypto.BTC/USD"
    PYTH_ETH_USD = "Pyth-Crypto.ETH/USD"
    BINANCE_CANDLE = "Binance-candle"
    MANUAL = "Manual"
    UNKNOWN = "Unknown"


OBJECTIVE_SOURCES: frozenset[OracleSource] = frozenset({
    OracleSource.CHAINLINK_BTC_USD,
    OracleSource.CHAINLINK_ETH_USD,
    OracleSource.PYTH_BTC_USD,
    OracleSource.PYTH_ETH_USD,
})


def is_objective(src: OracleSource) -> bool:
    return src in OBJECTIVE_SOURCES


def cross_venue_compatible(a: OracleSource, b: OracleSource) -> bool:
    """True iff both sources are objective price feeds for the SAME underlying.

    Same-source pairing is always compatible (e.g. Chainlink-BTC × Chainlink-BTC).
    Cross-source pairing (Chainlink × Pyth) is compatible if both reference
    the same asset (BTC × BTC, ETH × ETH), subject to mandatory haircut.
    """
    if not (is_objective(a) and is_objective(b)):
        return False

    btc_set = {OracleSource.CHAINLINK_BTC_USD, OracleSource.PYTH_BTC_USD}
    eth_set = {OracleSource.CHAINLINK_ETH_USD, OracleSource.PYTH_ETH_USD}
    return (a in btc_set and b in btc_set) or (a in eth_set and b in eth_set)


def classify_polymarket_oracle(market: dict) -> OracleSource:
    """Best-effort classification from Polymarket market metadata.

    Polymarket markets don't carry a single canonical `oracle` field — the
    information is split across `resolution_source`, `oracle_address`,
    `umaResolutionStatus`, and free-text fields. We look at all of them and
    fall back to UMA (the platform default) if no objective signature
    is found.
    """
    txt_pool = " ".join(
        str(market.get(k, "")) for k in (
            "resolution_source",
            "resolutionSource",
            "description",
            "question",
            "title",
        )
    ).lower()

    if "chainlink" in txt_pool and "btc" in txt_pool:
        return OracleSource.CHAINLINK_BTC_USD
    if "chainlink" in txt_pool and "eth" in txt_pool:
        return OracleSource.CHAINLINK_ETH_USD
    if "pyth" in txt_pool and "btc" in txt_pool:
        return OracleSource.PYTH_BTC_USD
    if "pyth" in txt_pool and "eth" in txt_pool:
        return OracleSource.PYTH_ETH_USD
    if "binance" in txt_pool and ("candle" in txt_pool or "1h" in txt_pool or "btcusdt" in txt_pool):
        return OracleSource.BINANCE_CANDLE
    return OracleSource.UMA


def classify_limitless_oracle(market: dict) -> OracleSource:
    """Limitless: majority Pyth for crypto, manual for non-financial."""
    txt_pool = " ".join(
        str(market.get(k, "")) for k in (
            "oracleType",
            "oracle",
            "resolutionSource",
            "title",
            "description",
        )
    ).lower()

    if "chainlink" in txt_pool and "btc" in txt_pool:
        return OracleSource.CHAINLINK_BTC_USD
    if "chainlink" in txt_pool and "eth" in txt_pool:
        return OracleSource.CHAINLINK_ETH_USD
    if "pyth" in txt_pool and "btc" in txt_pool:
        return OracleSource.PYTH_BTC_USD
    if "pyth" in txt_pool and "eth" in txt_pool:
        return OracleSource.PYTH_ETH_USD
    if "manual" in txt_pool:
        return OracleSource.MANUAL
    return OracleSource.UNKNOWN
