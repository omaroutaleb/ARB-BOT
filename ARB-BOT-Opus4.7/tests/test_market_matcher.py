"""Market-matcher and oracle-compatibility tests.

Covers STRATEGY_SYNTHESIS.md §1.7 + §2.2 (delta-vs-open exclusion) + §2.3 (oracle gating).
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.matching.market_matcher import (
    Direction,
    find_pairs,
    normalize_limitless,
    normalize_polymarket,
)
from src.oracles.compatibility import (
    OracleSource,
    cross_venue_compatible,
    is_objective,
)


class TestOracleCompatibility:
    def test_uma_is_not_objective(self):
        assert not is_objective(OracleSource.UMA)

    def test_chainlink_is_objective(self):
        assert is_objective(OracleSource.CHAINLINK_BTC_USD)

    def test_pyth_is_objective(self):
        assert is_objective(OracleSource.PYTH_BTC_USD)

    def test_uma_cross_anything_incompatible(self):
        assert not cross_venue_compatible(OracleSource.UMA, OracleSource.CHAINLINK_BTC_USD)
        assert not cross_venue_compatible(OracleSource.UMA, OracleSource.PYTH_BTC_USD)

    def test_same_asset_chainlink_pyth_compatible(self):
        assert cross_venue_compatible(OracleSource.CHAINLINK_BTC_USD, OracleSource.PYTH_BTC_USD)

    def test_different_asset_incompatible(self):
        assert not cross_venue_compatible(OracleSource.CHAINLINK_BTC_USD, OracleSource.PYTH_ETH_USD)

    def test_binance_candle_not_objective_for_cross(self):
        assert not cross_venue_compatible(OracleSource.BINANCE_CANDLE, OracleSource.PYTH_BTC_USD)


class TestPolymarketNormalization:
    def test_delta_vs_open_not_arbable(self):
        m = normalize_polymarket({
            "question": "Will BTC be up or down in 5 minutes?",
            "slug": "btc-updown-5m",
            "conditionId": "0xabc",
        })
        assert m.direction == Direction.UPDOWN
        assert m.is_arbable is False        # §2.2 — excluded

    def test_strike_above_extracted(self):
        m = normalize_polymarket({
            "question": "Will BTC be above $110,000 on May 30, 2026?",
            "slug": "btc-above-110k",
            "conditionId": "0xdef",
            "resolution_source": "Chainlink BTC/USD",
            "end_date_iso": "2026-05-30T00:00:00Z",
        })
        assert m.direction == Direction.ABOVE
        assert m.strike_usd == 110_000.0
        assert m.oracle == OracleSource.CHAINLINK_BTC_USD
        assert m.is_arbable is True

    def test_uma_market_not_arbable(self):
        m = normalize_polymarket({
            "question": "Will BTC be above $100,000 on Friday?",
            "slug": "btc-uma",
            "conditionId": "0x1",
        })
        # No explicit Chainlink/Pyth mention → defaults to UMA
        assert m.oracle == OracleSource.UMA
        assert m.is_arbable is False


class TestLimitlessNormalization:
    def test_pyth_btc_classified(self):
        m = normalize_limitless({
            "slug": "btc-100k-2026",
            "title": "BTC above $100,000",
            "oracleType": "pyth",
            "description": "resolves via Pyth Crypto.BTC/USD feed",
            "strikePrice": 100000,
            "deadline": "2026-05-30T00:00:00Z",
            "positionIds": ["1", "2"],
        })
        assert m.oracle == OracleSource.PYTH_BTC_USD
        assert m.direction == Direction.ABOVE
        assert m.strike_usd == 100_000.0
        assert m.is_arbable is True


class TestPairing:
    def _poly_market(self, strike: float, oracle_name: str = "Chainlink BTC/USD"):
        end = datetime.now(tz=timezone.utc) + timedelta(hours=24)
        return normalize_polymarket({
            "question": f"Will BTC be above ${strike:,} on Friday?",
            "slug": f"poly-btc-{int(strike)}",
            "conditionId": f"0xpoly{int(strike)}",
            "resolution_source": oracle_name,
            "end_date_iso": end.isoformat(),
            "start_date_iso": (end - timedelta(hours=24)).isoformat(),
            "clobTokenIds": ["100", "200"],
        })

    def _lim_market(self, strike: float, oracle_name: str = "pyth btc"):
        end = datetime.now(tz=timezone.utc) + timedelta(hours=24)
        return normalize_limitless({
            "slug": f"lim-btc-{int(strike)}",
            "title": f"BTC above ${strike:,}",
            "oracleType": oracle_name,
            "description": f"Pyth Crypto.BTC/USD",
            "strikePrice": strike,
            "deadline": end.isoformat(),
            "positionIds": ["1", "2"],
        })

    def test_matching_strikes_pair(self):
        pairs = find_pairs([self._poly_market(110000)], [self._lim_market(110000)])
        assert len(pairs) == 1

    def test_strike_outside_tolerance_no_pair(self):
        pairs = find_pairs([self._poly_market(110000)], [self._lim_market(115000)])
        # 5000/110000 ≈ 4.5% > 0.5% tolerance
        assert pairs == []

    def test_uma_excluded_from_pairing(self):
        poly = self._poly_market(110000, oracle_name="UMA resolves")
        # No Chainlink/Pyth signature → falls back to UMA → not arbable → no pair
        pairs = find_pairs([poly], [self._lim_market(110000)])
        assert pairs == []
